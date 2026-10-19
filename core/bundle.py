"""Lossless, deduplicated offline runtime bundle with verified extraction."""
import hashlib
import json
from pathlib import Path
import zipfile
from core.files import safe_path, sha256


def pack(source, destination):
    source = Path(source)
    manifest = {}
    stored = set()
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_LZMA) as archive:
        for path in sorted(source.rglob('*')):
            if not path.is_file():
                continue
            name = path.relative_to(source).as_posix()
            safe_path(source, name)
            digest = sha256(path)
            manifest[name] = {'sha256': digest, 'size': path.stat().st_size}
            if digest not in stored:
                archive.write(path, 'blobs/' + digest)
                stored.add(digest)
        archive.writestr('manifest.json', json.dumps(manifest))


def unpack(source, destination):
    """Caller owns staging/commit; never publish partially extracted packages."""
    with zipfile.ZipFile(source) as archive:
        if archive.getinfo('manifest.json').file_size > 2 * 1024 * 1024:
            raise ValueError('Bundle manifest is too large.')
        manifest = json.loads(archive.read('manifest.json'))
        if not isinstance(manifest, dict) or len(manifest) > 10000:
            raise ValueError('Invalid bundle manifest.')
        total = 0
        for name, entry in manifest.items():
            target = safe_path(destination, name)
            digest, size = entry['sha256'], entry['size']
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('Invalid bundle hash.')
            if not isinstance(size, int) or size < 0:
                raise ValueError('Invalid bundle size.')
            total += size
            if total > 2 * 1024 ** 3 or archive.getinfo('blobs/' + digest).file_size != size:
                raise ValueError('Bundle exceeds declared size.')
            target.parent.mkdir(parents=True, exist_ok=True)
            actual = hashlib.sha256()
            written = 0
            with archive.open('blobs/' + digest) as incoming, target.open('xb') as outgoing:
                for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
                    written += len(chunk)
                    if written > size:
                        raise ValueError('Bundle exceeds declared size.')
                    actual.update(chunk)
                    outgoing.write(chunk)
            if written != size or actual.hexdigest() != digest:
                raise ValueError('Bundled runtime failed integrity verification.')
