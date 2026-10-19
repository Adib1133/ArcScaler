"""Official release discovery and staged, validated package installation."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import urlparse
import zipfile
from core.files import canonical, inside, operation_lock, safe_path, sha256, validate_pe, write_json


class VersionManager:
    GITHUB_API_URL = 'https://api.github.com/repos/optiscaler/OptiScaler/releases'
    MAX_ARCHIVE = 512 * 1024 * 1024
    MAX_EXTRACTED = 2 * 1024 * 1024 * 1024
    BASE_DLLS = ('libxess.dll', 'libxess_dx11.dll', 'fakenvapi.dll', 'libxell.dll')
    FG_DLLS = ('libxess_fg.dll',)

    def __init__(self, versions_dir=None):
        self.versions_dir = str(versions_dir or Path(__file__).resolve().parents[1] / 'assets/versions')
        Path(self.versions_dir).mkdir(parents=True, exist_ok=True)
        self.cache_file = str(Path(self.versions_dir) / 'releases_cache.json')
        self.last_error = None
        # Recover a directory-swap interrupted by process/power loss, even while offline.
        previous_versions = list(Path(self.versions_dir).glob('.previous-*'))
        if previous_versions:
            with operation_lock(self.versions_dir):
                for previous in previous_versions:
                    self._recover_commit(previous.name[len('.previous-'):])

    def version_path(self, tag):
        if not isinstance(tag, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', tag):
            raise ValueError('Invalid release tag.')
        return safe_path(self.versions_dir, tag)

    @staticmethod
    def sort_key(tag):
        nums = tuple(int(n) for n in re.findall(r'\d+', tag)[:3])
        return (nums + (0,) * (3 - len(nums)), not bool(re.search(r'alpha|beta|rc|nightly|preview', tag, re.I)), tag)

    def resolve_package(self, tag, frame_gen=True, upscaler=True, fake_nvapi=True, exclude_prefixes=()):
        # XeFG requires FakeNvapi even when GPU identity spoofing is disabled.
        fake_nvapi = fake_nvapi or frame_gen
        root = self.version_path(tag)
        proxy = next((root / n for n in ('OptiScaler.dll', 'dxgi.dll', 'nvngx.dll') if (root / n).is_file()), None)
        if proxy is None:
            raise ValueError(f'{tag}: OptiScaler proxy is missing.')
        files = {'OptiScaler.dll': proxy}
        required = ['OptiScaler.ini']
        if upscaler:
            required += ['libxess.dll']
        if frame_gen:
            required += ['libxess_fg.dll', 'libxell.dll']
        if fake_nvapi:
            required += ['fakenvapi.dll', 'fakenvapi.ini', 'libxell.dll']
        # Discover supported runtime variants from this release, not a fixed copy list.
        for path in sorted(root.iterdir()):
            name = path.name.lower()
            wanted = (upscaler and name.startswith('libxess') and 'fg' not in name
                      or frame_gen and name.startswith(('libxess_fg', 'libxessfg'))
                      or (frame_gen or fake_nvapi) and name.startswith(('libxell', 'fakenvapi')))
            if wanted and not name.startswith(exclude_prefixes) and path.suffix.lower() in ('.dll', '.ini'):
                files[path.name] = path
        for name in required:
            if name.lower().startswith(exclude_prefixes):
                continue
            path = safe_path(root, name)
            if not path.is_file():
                raise ValueError(f'{tag}: required file missing: {name}')
            files[name] = path
        for name, path in files.items():
            if not inside(path, root):
                raise ValueError('Package contains a linked file outside the version directory.')
            if name.endswith('.dll'):
                validate_pe(path)
        metadata = root / 'version_meta.json'
        if metadata.is_file():
            meta = json.loads(metadata.read_text(encoding='utf-8'))
            selected_paths = {canonical(path) for path in files.values()}
            for name, expected in meta.get('file_hashes', {}).items():
                path = safe_path(root, name)
                if canonical(path) not in selected_paths:
                    continue
                if not path.is_file() or sha256(path) != expected:
                    raise ValueError(f'Installed package has changed: {name}. Re-download the release.')
        return files

    def is_version_valid(self, tag_name):
        try:
            self.resolve_package(tag_name)
            return True
        except (OSError, ValueError, TypeError):
            return False

    def get_version_details(self, tag_name):
        try:
            root = self.version_path(tag_name)
            files = {str(p.relative_to(root)): p.stat().st_size for p in root.rglob('*') if p.is_file()}
            valid = self.is_version_valid(tag_name)
            return {'installed': valid, 'is_valid': valid, 'has_stubs': bool(files) and not valid,
                    'files': files, 'total_size_mb': round(sum(files.values()) / 1048576, 2)}
        except (OSError, ValueError):
            return {'installed': False, 'is_valid': False, 'files': {}, 'total_size_mb': 0, 'has_stubs': False}

    def get_installed_versions(self):
        def has_core(tag):
            try:
                self.resolve_package(tag, False, False, False)
                return True
            except (OSError, ValueError, TypeError):
                return False
        return sorted([p.name for p in Path(self.versions_dir).iterdir()
                       if p.is_dir() and not p.name.startswith('.') and has_core(p.name)],
                      key=self.sort_key, reverse=True)

    def delete_version(self, tag):
        """Permanently delete an installed version directory.  Raises ValueError if the tag is unsafe."""
        root = self.version_path(tag)
        if not root.is_dir():
            raise ValueError(f'{tag}: version directory not found.')
        # Safety: directory must be directly inside versions_dir
        if canonical(root.parent) != canonical(Path(self.versions_dir)):
            raise ValueError('Version path is outside the managed directory.')
        shutil.rmtree(root, ignore_errors=False)

    def _enrich_installed_status(self, releases):
        result = []
        for item in releases:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            details = self.get_version_details(entry.get('tag_name'))
            entry.update(installed=details['is_valid'], has_stubs=details['has_stubs'], size_mb=details['total_size_mb'])
            result.append(entry)
        return result

    def fetch_available_releases(self, force_refresh=False):
        self.last_error = None
        cached = []
        try:
            cached = json.loads(Path(self.cache_file).read_text(encoding='utf-8'))
            if not isinstance(cached, list):
                cached = []
            if cached and not force_refresh and time.time() - Path(self.cache_file).stat().st_mtime < 3600:
                return self._enrich_installed_status(cached)
        except (OSError, ValueError):
            pass
        try:
            request = urllib.request.Request(self.GITHUB_API_URL, headers={'User-Agent': 'ArcScaler/1.0'})
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = json.loads(response.read(4 * 1024 * 1024))
            if not isinstance(raw, list):
                raise ValueError('GitHub returned an invalid release list.')
            releases = []
            for item in raw:
                if item.get('draft'):
                    continue
                assets = [{'name': a['name'], 'download_url': a['browser_download_url'],
                           'size': a['size'], 'digest': a.get('digest')}
                          for a in item.get('assets', []) if a.get('name', '').lower().endswith(('.7z', '.zip'))]
                releases.append({'tag_name': item['tag_name'], 'name': item.get('name') or item['tag_name'],
                                 'published_at': item.get('published_at'), 'prerelease': item.get('prerelease', False),
                                 'body': (item.get('body') or '')[:1000], 'assets': assets})
            write_json(self.cache_file, releases)
            return self._enrich_installed_status(releases)
        except Exception as exc:
            self.last_error = f'Unable to refresh GitHub releases: {exc}'
            # Never invent release metadata or download URLs while offline.
            if not cached:
                cached = [{'tag_name': tag, 'name': tag + ' (local)', 'assets': [], 'body': 'Installed locally.'}
                          for tag in self.get_installed_versions()]
            return self._enrich_installed_status(cached)

    def _recover_commit(self, tag):
        dest = self.version_path(tag)
        previous = safe_path(self.versions_dir, '.previous-' + tag)
        if previous.exists() and not dest.exists():
            os.replace(previous, dest)
        return previous

    def download_and_install_version(self, release_info, progress_callback=None, cancel_event=None):
        report = progress_callback or (lambda *args: None)
        def check_cancel():
            if cancel_event is not None and cancel_event.is_set():raise ValueError('Download cancelled')
        committed = False
        try:
            check_cancel()
            tag = release_info.get('tag_name')
            dest = self.version_path(tag)
            assets = release_info.get('assets', [])
            asset = next((a for a in assets if a.get('name', '').lower().endswith(('.7z', '.zip'))), None)
            if not asset:
                raise ValueError('Release has no supported .7z or .zip package.')
            name = asset['name']
            if Path(name).name != name or '/' in name or '\\' in name:
                raise ValueError('Invalid asset filename.')
            url = asset['download_url']
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.netloc != 'github.com' or not parsed.path.startswith('/optiscaler/OptiScaler/releases/download/'):
                raise ValueError('Only official OptiScaler release URLs are accepted.')
            expected_size = int(asset.get('size') or 0)
            if not 0 < expected_size <= self.MAX_ARCHIVE:
                raise ValueError('Invalid archive size.')
            with operation_lock(self.versions_dir):
                previous = self._recover_commit(tag)
                with tempfile.TemporaryDirectory(prefix='.download-', dir=self.versions_dir, ignore_cleanup_errors=True) as tmp:
                    archive = Path(tmp) / name
                    extract = Path(tmp) / 'payload'
                    extract.mkdir()
                    request = urllib.request.Request(url, headers={'User-Agent': 'ArcScaler/1.0'})
                    with urllib.request.urlopen(request, timeout=60) as response, archive.open('wb') as out:
                        downloaded = 0
                        while chunk := response.read(1024 * 1024):
                            check_cancel()
                            downloaded += len(chunk)
                            if downloaded > expected_size:
                                raise ValueError('Archive exceeds advertised size.')
                            out.write(chunk)
                            report(0.8 * downloaded / expected_size, f'Downloading {name}')
                    if downloaded != expected_size:
                        raise ValueError('Download is truncated.')
                    digest = asset.get('digest')
                    if digest and (not digest.startswith('sha256:') or sha256(archive) != digest[7:]):
                        raise ValueError('GitHub archive checksum mismatch.')
                    report(0.85, 'Validating and extracting archive')
                    check_cancel()
                    self._extract_archive(archive, extract)
                    check_cancel()
                    self._ensure_root_binaries(extract)
                    # Validate in isolation; stale installed files cannot satisfy this check.
                    staged_vm = VersionManager(tmp)
                    staged_vm.resolve_package('payload')
                    from core.config_generator import ConfigGenerator
                    ConfigGenerator.generate_nvngx_ini(template=(extract / 'OptiScaler.ini').read_text(encoding='utf-8-sig'))
                    metadata = dict(release_info)
                    metadata['archive_sha256'] = sha256(archive)
                    metadata['file_hashes'] = {str(p.relative_to(extract)).replace('\\', '/'): sha256(p)
                                               for p in extract.rglob('*') if p.is_file() and p.suffix.lower() in ('.dll', '.ini')}
                    write_json(extract / 'version_meta.json', metadata)
                    try:
                        if archive.exists():
                            archive.unlink()
                    except OSError:
                        pass
                    # Keep previous version until the new directory is fully committed.
                    check_cancel()
                    if previous.exists():
                        self._remove_staging(previous)
                    if dest.exists():
                        os.replace(dest, previous)
                    try:
                        os.replace(extract, dest)
                        committed = True
                    except BaseException:
                        if previous.exists() and not dest.exists():
                            os.replace(previous, dest)
                        raise
                    if previous.exists():
                        try:
                            self._remove_staging(previous)
                        except OSError:
                            pass  # A retained previous copy does not invalidate the new commit.
                    report(1, 'Package installed and checked')
                    return {'success': True, 'tag': tag, 'dir': str(dest)}
        except Exception as exc:
            if committed and 'dest' in locals() and dest.exists() and self.is_version_valid(tag):
                return {'success': True, 'tag': tag, 'dir': str(dest)}
            return {'success': False, 'error': f'Installation failed; existing version retained: {exc}'}

    def _remove_staging(self, path):
        path = Path(path)
        if not inside(path, self.versions_dir) or canonical(path) == canonical(self.versions_dir) or not path.name.startswith('.'):
            raise ValueError('Invalid staging cleanup path.')
        try:
            shutil.rmtree(path)
        except OSError:
            time.sleep(0.05)
            try:
                shutil.rmtree(path, ignore_errors=True)
            except Exception:
                pass

    def _extract_archive(self, archive_path, dest_dir):
        archive_path = Path(archive_path)
        dest_dir = Path(dest_dir)
        total = 0
        if archive_path.suffix.lower() == '.zip':
            with zipfile.ZipFile(archive_path) as archive:
                if len(archive.infolist()) > 10000:
                    raise ValueError('Too many archive entries.')
                for entry in archive.infolist():
                    safe_path(dest_dir, entry.filename.rstrip('/'))
                    if stat.S_ISLNK(entry.external_attr >> 16):
                        raise ValueError('Archive links are not allowed.')
                    total += entry.file_size
                    if total > self.MAX_EXTRACTED:
                        raise ValueError('Archive expands beyond the size limit.')
                archive.extractall(dest_dir)
        else:
            # Use Windows built-in tar.exe (bsdtar / libarchive) which supports BCJ2 and
            # every other filter used by OptiScaler's 7z releases.  py7zr fails on BCJ2.
            tar_exe = shutil.which('tar') or r'C:\Windows\System32\tar.exe'
            result = subprocess.run(
                [tar_exe, '-xf', str(archive_path), '-C', str(dest_dir)],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                raise ValueError(
                    f'Archive extraction failed (exit {result.returncode}): '
                    f'{(result.stderr or result.stdout or "").strip()[:400]}'
                )
        for path in dest_dir.rglob('*'):
            safe_path(dest_dir, path.relative_to(dest_dir).as_posix())
        return True

    def validate_local_version(self, tag, ignore_hashes=False):
        """Re-validate a locally placed (manually dropped) version directory.

        Returns {'valid': True, 'files': [...]} on success or
        {'valid': False, 'error': str} on failure.  When ignore_hashes=True,
        the stored SHA-256 manifest is not checked (useful for partial
        user-replaced files).
        """
        try:
            root = self.version_path(tag)
            if ignore_hashes:
                # Temporarily skip metadata hash checks by resolving without meta
                proxy = next(
                    (root / n for n in ('OptiScaler.dll', 'dxgi.dll', 'nvngx.dll')
                     if (root / n).is_file()), None
                )
                if proxy is None:
                    raise ValueError(f'{tag}: OptiScaler proxy is missing.')
                required = ['OptiScaler.ini', 'libxess.dll', 'libxess_dx11.dll',
                            'libxell.dll', 'fakenvapi.dll', 'fakenvapi.ini']
                for name in required:
                    path = safe_path(root, name)
                    if not path.is_file():
                        raise ValueError(f'{tag}: required file missing: {name}')
                    if name.endswith('.dll'):
                        validate_pe(path)
                files = sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()
                               and not p.name.startswith('.'))
                return {'valid': True, 'files': files}
            else:
                self.resolve_package(tag)
                files = sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()
                               and not p.name.startswith('.'))
                return {'valid': True, 'files': files}
        except Exception as exc:
            return {'valid': False, 'error': str(exc)}

    def rebuild_version_meta(self, tag, release_info=None):
        """Regenerate version_meta.json for a manually placed / modified version.

        This allows the GUI to re-accept files the user has replaced in the
        versions folder without requiring a full re-download.
        """
        root = self.version_path(tag)
        # Structural check first (without hash enforcement)
        result = self.validate_local_version(tag, ignore_hashes=True)
        if not result['valid']:
            raise ValueError(result['error'])
        metadata = dict(release_info or {'tag_name': tag, 'name': tag + ' (local)', 'assets': []})
        metadata['file_hashes'] = {
            str(p.relative_to(root)).replace('\\', '/'): sha256(p)
            for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.dll', '.ini')
            and not p.name.startswith('.')
        }
        write_json(root / 'version_meta.json', metadata)
        return metadata

    def _ensure_root_binaries(self, dest_dir):
        root = Path(dest_dir)
        proxies = [p for p in root.rglob('*') if p.is_file() and p.name.lower() in ('optiscaler.dll', 'dxgi.dll', 'nvngx.dll')]
        candidates = [p.parent for p in proxies if (p.parent / 'OptiScaler.ini').is_file()]
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) != 1:
            raise ValueError('Archive must contain one unambiguous OptiScaler package.')
        source = candidates[0]
        if source != root:
            # Preserve package-relative dependencies and license directories, not a flat DLL search.
            for child in list(source.iterdir()):
                target = root / child.name
                if target.exists():
                    raise ValueError('Archive has conflicting package roots.')
                if child.is_dir():
                    shutil.copytree(child, target)
                else:
                    shutil.copy2(child, target)
