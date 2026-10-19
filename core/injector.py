"""Per-game proxy deployment. File installation does not prove runtime activation."""
from contextlib import ExitStack
import configparser
import os
import time
from pathlib import Path
import psutil
from core.files import atomic_write, canonical, inside, operation_lock, safe_path, sha256, validate_pe
from core.safety import SafetyManager
from core.config_generator import ConfigGenerator
from core.version_manager import VersionManager
from core.detector import GameDetector
from core.install_plan import build_plan
from core.settings import HOOKS, validate, SCHEMA
from core.native_runtimes import BUNDLED, resolve_deployment_package


def _merge_ini(old_text, new_text):
    """Overlay any custom sections from the existing installed INI onto the freshly-generated one,
    preserving all comments and 47KB original structure (never stripping comments).
    """
    try:
        old = configparser.ConfigParser(interpolation=None, strict=False)
        old.optionxform = str
        old.read_string(old_text)
        new_cfg = configparser.ConfigParser(interpolation=None, strict=False)
        new_cfg.optionxform = str
        new_cfg.read_string(new_text)

        updates = {}
        for section in old.sections():
            if not new_cfg.has_section(section):
                updates[section] = dict(old.items(section))
        if updates:
            return ConfigGenerator.update_ini_text(new_text, updates)
        return new_text
    except Exception:
        return new_text


class Injector:
    DEFAULT_HOOKS = HOOKS

    def __init__(self, assets_dir=None):
        self.assets_dir = str(assets_dir or Path(__file__).resolve().parents[1] / 'assets')
        self.bin_dir = str(Path(self.assets_dir) / 'bin')
        self.version_manager = VersionManager(str(Path(self.assets_dir) / 'versions'))

    def has_valid_binaries(self, optiscaler_version=None):
        if optiscaler_version:
            return self.version_manager.is_version_valid(optiscaler_version)
        return bool(self.version_manager.get_installed_versions())

    @staticmethod
    def process_in_directory(target_exe, target_dir):
        for proc in psutil.process_iter(['name', 'exe']):
            try:
                pexe = proc.info.get('exe')
                if pexe and (canonical(pexe) == canonical(target_exe) or inside(pexe, target_dir)):
                    return True
                if not pexe and target_exe and (proc.info.get('name') or '').lower() == Path(target_exe).name.lower():
                    # Access denied: fail closed for a potentially matching process.
                    return True
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                raise RuntimeError('Cannot inspect a game process; close it before modifying files.')
        return False

    def is_game_running(self, target_exe, target_dir):
        return self.process_in_directory(target_exe, target_dir)

    def apply_injection(self, target_dir, target_exe, additional_dirs=None, all_target_dirs=None,
                        starting_upscaler='DLSS', hook_method='dxgi.dll', optiscaler_version=None,
                        xess_quality='User Defined', frame_gen_enabled=False, intercept_dlssg=True,
                        intercept_fsr3=False, reflex_to_xell_enabled=False, reflex_boost=False,
                        xess_network_model=None, sharpness=0.3, custom_scale=None, invert_depth=None,
                        jitter_cancellation=None, fg_input=None, base_dir=None, upscaler_enabled=False,
                        gpu_spoofing=False, installation_mode='automatic', force=False, compatibility_path=None, hardware=None, expected_plan=None,
                        runtime_method=BUNDLED, native_xess_path='', native_xefg_path='', native_xell_path=''):
        """Deploy OptiScaler into a game directory.

        force=True bypasses plan validation errors, skips the unmanaged-proxy block,
        and uses the user-chosen proxy filename (hook_method) regardless of the recipe.
        OptiScaler.ini is always injected; existing user edits are preserved.
        """
        try:
            requested = validate({k:v for k,v in locals().items() if k in SCHEMA and not (k in ('fg_input', 'optiscaler_version') and v is None)}, require_mode=True)
            primary = Path(target_dir).resolve()
            exe = Path(target_exe).resolve()
            if not primary.is_dir() or exe.parent != primary or exe.suffix.lower() != '.exe':
                raise ValueError('Select the actual game executable and its directory.')
            validate_pe(exe, dll=False)
            if hook_method not in self.DEFAULT_HOOKS:
                raise ValueError('Unsupported proxy filename.')
            dirs = {canonical(primary): str(primary)}
            for folder in [*(additional_dirs or []), *(all_target_dirs or [])]:
                folder = Path(folder).resolve()
                if not folder.is_dir():
                    raise ValueError(f'Target directory missing: {folder}')
                dirs[canonical(folder)] = str(folder)
            if not force and installation_mode == 'automatic' and len(dirs) > 1:
                raise ValueError('Automatic recipes deploy to one verified game executable directory only.')
            root = Path(base_dir).resolve() if base_dir else Path(os.path.commonpath(list(dirs.values())))
            if runtime_method != BUNDLED and len(dirs) != 1:
                raise ValueError('Native runtime reuse installs into one game executable directory only.')
            if root == Path(root.anchor) or any(not inside(d, root) for d in dirs.values()):
                raise ValueError('All targets must be inside one game installation.')
            active = optiscaler_version
            if active is None:
                installed = self.version_manager.get_installed_versions()
                active = installed[0] if installed else None
            if not active:
                raise ValueError('Install an OptiScaler release first.')
            with ExitStack() as stack:
                # Consistent ordering also protects overlapping per-game deployments.
                for directory in sorted(dirs.values(), key=canonical):
                    stack.enter_context(operation_lock(directory))
                stack.enter_context(operation_lock(self.version_manager.versions_dir))
                self._check_processes(target_exe, dirs.values())
                analysis = GameDetector.analyze_game(str(exe))
                if not analysis.get('valid'):
                    raise ValueError(analysis.get('error', 'Game scan failed.'))
                analysis['discovered_locations'], analysis['detected_upscalers'] = (
                    GameDetector._deep_scan_upscalers(str(root), str(primary)))
                analysis['all_target_dirs'] = list(dirs.values())
                analysis['base_dir'] = str(root)
                analysis['anti_cheat'] = GameDetector._detect_anti_cheat(str(primary), str(root))
                resolved_input = fg_input or (
                    'dlssg' if intercept_dlssg else 'fsrfg' if intercept_fsr3 else 'upscaler')
                deployment = build_plan(analysis, dict(requested,
                    fg_input=resolved_input, force=force, optiscaler_version=active,
                    compatibility_path=compatibility_path, hardware=hardware or {}), self.version_manager)
                if expected_plan and any(deployment.get(key) != expected_plan.get(key)
                                         for key in ('target_dir','files','settings','source_hashes','ini_overrides','reused_files','target_hashes')):
                    raise ValueError('The installation plan changed. Review a fresh preview before installing.')
                if deployment.get('blocking_error'):
                    raise ValueError(deployment['blocking_error'])
                if frame_gen_enabled and analysis.get('graphics_api') != 'DX12':
                    raise ValueError('Frame Generation requires detected DirectX 12.')
                if deployment['errors'] and not force:
                    return {'success': False, 'error': chr(10).join(deployment['errors']), 'plan': deployment}
                if deployment['errors'] and force:
                    # Convert errors to warnings so they appear in the log.
                    deployment['notes'] = list(deployment.get('notes', [])) + [
                        '[Force] overriding: ' + e for e in deployment['errors']]
                effective = deployment['settings']
                upscaler_enabled = effective.get('upscaler_enabled', upscaler_enabled)
                frame_gen_enabled = effective.get('frame_gen_enabled', frame_gen_enabled)
                gpu_spoofing = effective.get('gpu_spoofing', gpu_spoofing)
                fg_input = effective.get('fg_input', fg_input)
                # In force mode: honour the user-chosen hook and upscaler exactly.
                # In normal mode: follow what the recipe mandates.
                if not force:
                    hook_method = effective.get('hook_method', hook_method)
                    starting_upscaler = effective.get('starting_upscaler', starting_upscaler)
                package, reused_files, _ = resolve_deployment_package(analysis, effective, self.version_manager)
                if reused_files != deployment.get('reused_files', {}):
                    raise ValueError('Native runtimes changed. Review a fresh preview before installing.')

                # ── INI: generate then overlay any pre-existing user customisations ─────
                existing_ini = None
                installed_ini_path = primary / 'OptiScaler.ini'
                if installed_ini_path.is_file():
                    try:
                        existing_ini = installed_ini_path.read_text(encoding='utf-8-sig')
                    except OSError:
                        pass
                config = ConfigGenerator.generate_nvngx_ini(
                    starting_upscaler, hook_method, xess_quality, frame_gen_enabled,
                    intercept_dlssg, intercept_fsr3, reflex_to_xell_enabled, reflex_boost,
                    xess_network_model, sharpness, custom_scale, invert_depth,
                    jitter_cancellation, fg_input=fg_input,
                    template=package['OptiScaler.ini'].read_text(encoding='utf-8-sig'),
                    upscaler_enabled=upscaler_enabled, gpu_spoofing=gpu_spoofing)
                if existing_ini:
                    config = _merge_ini(existing_ini, config)
                if deployment.get('ini_overrides'):
                    config = ConfigGenerator.update_ini_text(config, deployment['ini_overrides'])

                # ── File map: OptiScaler.dll deployed under the chosen proxy filename ────
                # e.g. hook_method='winmm.dll' creates winmm.dll in the game folder.
                # Any existing file with that name is safely snapshotted first.
                files = {hook_method: package['OptiScaler.dll'],
                         'OptiScaler.ini': config.encode('utf-8')}
                if 'fakenvapi.ini' in package:
                    files['fakenvapi.ini'] = ConfigGenerator.generate_fakenvapi_ini(
                        package['fakenvapi.ini'].read_text(encoding='utf-8-sig'),
                        reflex_to_xell_enabled).encode('utf-8')
                for name, path in package.items():
                    if name.endswith('.dll') and name != 'OptiScaler.dll':
                        files[name] = path

                if not force and set(files) != set(deployment['files']):
                    raise ValueError('Deployment files do not match the reviewed preview plan.')

                plan = {folder: list(files) for folder in dirs.values()}
                metadata = {
                    'target_exe': str(exe),
                    'hook_method': hook_method,
                    'optiscaler_version': active,
                    'base_dir': str(root),
                    'recipe_id': deployment['recipe_id'],
                    'rule_reviewed': deployment['reviewed'],
                    'effective_settings': effective,
                    'installed_mode': 'sr+fg' if upscaler_enabled and frame_gen_enabled else 'fg' if frame_gen_enabled else 'sr',
                    'requested_settings': dict(requested,optiscaler_version=active),
                    'sources': deployment['sources'],
                    'additional_dirs': [d for d in dirs.values()
                                        if canonical(d) != canonical(primary)],
                    'force_injected': force,
                    'reused_files': reused_files,
                }
                if SafetyManager.has_active_backup(primary):
                    old_manifest = SafetyManager.load_manifest(primary, list(dirs.values()))
                    if old_manifest.get('schema')!=2:raise ValueError('Restore the legacy installation before installing again.')
                    if old_manifest.get('operation',{}).get('state') not in (None,'complete'):
                        raise ValueError('An interrupted operation needs recovery before installing again.')
                    conflicts = SafetyManager.conflicts(old_manifest)
                    if conflicts and not force:
                        return {'success':False,'error':'Externally modified files need review. Restore original files with preservation before reinstalling.', 'conflicts':conflicts}
                    if conflicts:
                        SafetyManager.preserve_conflicts(old_manifest)
                # No game mutation before snapshot success.
                if any(not Path(path).is_file() or sha256(path) != digest for path, digest in reused_files.items()):
                    raise ValueError('Native runtimes changed before installation. Review a fresh preview.')
                undo_checkpoint = SafetyManager.capture_undo(str(primary), plan)
                manifest = SafetyManager.create_multi_dir_snapshot(plan, metadata, str(primary))
                if undo_checkpoint:manifest['undo'] = undo_checkpoint
                else:manifest.pop('undo', None)
                try:
                    import hashlib
                    expected_hashes = {name:hashlib.sha256(source).hexdigest() if isinstance(source,bytes) else sha256(source) for name,source in files.items()}
                    SafetyManager.save_stage(manifest, 'deploying', expected={folder:dict(expected_hashes) for folder in dirs.values()})
                    self._check_processes(target_exe, dirs.values())
                    stale = {
                        (folder, name)
                        for folder, record in manifest['folders'].items()
                        for name in [
                            *record['created_files'],
                            *(i['filename'] for i in record['overwritten_files']),
                        ]
                        if name not in plan.get(folder, [])
                    }
                    SafetyManager.restore_files(manifest, stale)
                    for folder in dirs.values():
                        self._check_processes(target_exe, dirs.values())
                        for name, source in files.items():
                            dest = safe_path(folder, name)
                            if isinstance(source, bytes):
                                atomic_write(dest, source)
                                if dest.read_bytes() != source:
                                    raise OSError(f'Configuration verification failed: {dest}')
                            else:
                                expected = sha256(source)
                                atomic_write(dest, source=source)
                                if sha256(dest) != expected:
                                    raise OSError(f'Deployment verification failed: {dest}')
                    SafetyManager.save_stage(manifest, 'verifying')
                    if any(not Path(path).is_file() or sha256(path) != digest for path, digest in reused_files.items()):
                        raise ValueError('A reused native runtime changed during installation.')
                    manifest['metadata']['deployed_files'] = {folder:{name:sha256(safe_path(folder,name)) for name in files} for folder in dirs.values()}
                    if any(hashes != expected_hashes for hashes in manifest['metadata']['deployed_files'].values()):
                        raise OSError('Final installed hashes do not match the planned files.')
                    manifest['metadata']['installed_hashes'] = manifest['metadata']['deployed_files'][str(primary)]
                    manifest['metadata']['installed_at'] = time.time()
                    SafetyManager.save_stage(manifest, 'complete')
                    try:SafetyManager.prune_undo(manifest)
                    except OSError:pass
                except Exception as exc:
                    try:
                        self._check_processes(target_exe, dirs.values())
                        recovery = SafetyManager.rollback(
                            str(primary), metadata['additional_dirs'], _locked=True)
                    except Exception as recovery_exc:
                        recovery = {'success': False, 'error': str(recovery_exc)}
                    return {'success': False,
                            'error': f'Deployment failed: {exc}',
                            'recovery': recovery}
                force_note = ' [Force inject]' if force else ''
                return {
                    'success': True,
                    'target_dir': str(primary),
                    'all_injected_dirs': list(dirs.values()),
                    'hook_method': hook_method,
                    'version_used': active,
                    'injected_suite': list(files),
                    'plan': deployment,
                    'message': (
                        f'Installed {active} via {hook_method}{force_note}. '
                        f'Files verified; confirm XeSS activation in the game overlay.'
                    ),
                }
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def verify_installation(self, target_dir, additional_dirs=None):
        from core.installation_state import installation_health
        health=installation_health(target_dir, additional_dirs)
        return dict(health, success=health['state']=='installed', error=health['message'])

    def _check_processes(self, exe, dirs):
        if any(self.is_game_running(exe, d) for d in dirs):
            raise RuntimeError('A game process is running. Close the game before modifying files.')

    def revert_injection(self, target_dir, target_exe='', additional_dirs=None, all_target_dirs=None, preserve_changes=False):
        try:
            dirs = {canonical(d): str(Path(d).resolve())
                    for d in [target_dir, *(additional_dirs or []), *(all_target_dirs or [])]}
            with ExitStack() as stack:
                for d in sorted(dirs.values(), key=canonical):
                    stack.enter_context(operation_lock(d))
                self._check_processes(target_exe, dirs.values())
                return SafetyManager.rollback(target_dir, list(dirs.values()), _locked=True, preserve_changes=preserve_changes)
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def is_injected(self, target_dir):
        if not SafetyManager.has_active_backup(target_dir):
            return False
        try:
            content = (Path(target_dir) / 'OptiScaler.ini').read_text(encoding='utf-8')
            return ConfigGenerator.MARKER_SIGNATURE in content
        except OSError:
            return False

    def undo_last_change(self, target_dir, target_exe='', additional_dirs=None):
        try:
            dirs = {canonical(d): str(Path(d).resolve()) for d in [target_dir, *(additional_dirs or [])]}
            with ExitStack() as stack:
                for d in sorted(dirs.values(), key=canonical):stack.enter_context(operation_lock(d))
                self._check_processes(target_exe, dirs.values())
                return SafetyManager.undo_last(target_dir, list(dirs.values()))
        except Exception as exc:
            return {'success':False, 'error':str(exc)}
