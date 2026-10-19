"""Plan reuse without taking ownership of the game's runtime DLLs."""
from pathlib import Path
from core.files import canonical, inside, safe_path, sha256, validate_pe
from core.safety import SafetyManager

BUNDLED = 'Bundled runtimes'
REUSE = 'Reuse native XeSS + FG'
ADD_FG = 'Keep native XeSS; add missing FG'


def resolve_deployment_package(analysis, settings, manager):
    tag = settings['optiscaler_version']
    fg, sr = settings['frame_gen_enabled'], settings['upscaler_enabled']
    fake = fg or settings['gpu_spoofing'] or settings['reflex_to_xell_enabled']
    if settings.get('runtime_method', BUNDLED) == BUNDLED:
        return manager.resolve_package(tag, fg, sr, fake), {}, {}
    # Discover native dependencies before validating any unused bundled runtime.
    probe = manager.resolve_package(tag, False, False, False)
    for name in ['libxess.dll', *(['libxess_fg.dll'] if fg else []), *(['libxell.dll'] if fake else [])]:
        probe[name] = manager.version_path(tag) / name
    selected, reused, overrides = select_runtimes(analysis, settings, probe)
    exclude = ['libxess_dx11']
    if 'libxess.dll' not in selected: exclude.append('libxess.dll')
    if 'libxess_fg.dll' not in selected: exclude.extend(('libxess_fg', 'libxessfg'))
    if 'libxell.dll' not in selected: exclude.append('libxell')
    return manager.resolve_package(tag, fg, sr, fake, tuple(exclude)), reused, overrides


def select_runtimes(analysis, settings, package):
    """Return a filtered package, read-only dependencies and library overrides.

    PE validation checks architecture/structure, not runtime API compatibility.
    Never silently replace an invalid native DLL or reuse a managed DLL that
    rollback could remove. Explicit paths resolve ambiguous game layouts.
    """
    method = settings.get('runtime_method', BUNDLED)
    if method == BUNDLED:
        return dict(package), {}, {}
    if analysis.get('graphics_api') != 'DX12':
        raise ValueError('Native runtime reuse currently requires detected DirectX 12.')
    target = Path(analysis['target_dir']).resolve()
    root = Path(analysis.get('base_dir') or target).resolve()
    managed = set()
    if SafetyManager.has_active_backup(target):
        manifest = SafetyManager.load_manifest(target, analysis.get('all_target_dirs', []))
        for folder, record in manifest['folders'].items():
            managed.update(canonical(Path(folder) / name) for name in
                           [*record['created_files'], *(i['filename'] for i in record['overwritten_files'])])
    reused, overrides = {}, {}
    files = dict(package)

    def locate(name, setting, required):
        explicit = settings.get(setting, '').strip()
        if '\n' in explicit or '\r' in explicit:
            raise ValueError('Runtime paths must fit on one line.')
        if explicit:
            candidates = [Path(explicit)]
        else:
            candidates = [target / name] if (target / name).exists() else []
            if not candidates:
                candidates = [Path(d.get('full_path', str(Path(loc['folder']) / d['filename'])))
                              for loc in analysis.get('discovered_locations', [])
                              for d in loc.get('details', []) if d['filename'].lower() == name]
        candidates = list({canonical(p): p for p in candidates}.values())
        if not candidates:
            if required:
                raise ValueError(f'Native {name} not found. Select its DLL in Reuse native XeSS.')
            return None
        if len(candidates) != 1:
            raise ValueError(f'Multiple copies of {name} found. Select the intended native DLL.')
        candidate = candidates[0]
        if not candidate.is_absolute():
            raise ValueError(f'Select an absolute path for {name}.')
        if candidate.name.lower() != name or not inside(candidate, root):
            raise ValueError(f'Select {name} inside this game installation.')
        candidate = safe_path(root, candidate.relative_to(root).as_posix())
        if canonical(candidate) in managed:
            if not required and not explicit:
                return None  # Keep updating a previously deployed missing dependency.
            raise ValueError(f'{name} is managed by ArcScaler. Restore original files before reusing it.')
        from core.runtime_validation import inspect_runtime
        inspect_runtime(candidate, name)
        reused[str(candidate)] = sha256(candidate)
        return candidate

    sr = locate('libxess.dll', 'native_xess_path', True)
    overrides['XeSSPath'] = str(sr.parent)
    # Native SR is kept even when only the FG route is enabled.
    files = {n: p for n, p in files.items()
             if not (n.lower().startswith('libxess') and 'fg' not in n.lower())}
    if settings['frame_gen_enabled'] or 'libxell.dll' in files:
        for name, key, option in [('libxess_fg.dll', 'native_xefg_path', 'XeFGPath'),
                                  ('libxell.dll', 'native_xell_path', 'XeLLPath')]:
            if name == 'libxess_fg.dll' and not settings['frame_gen_enabled']:
                continue
            path = locate(name, key, method == REUSE and settings['frame_gen_enabled'])
            if path:
                overrides[option] = str(path.parent)
                prefix = ('libxess_fg', 'libxessfg') if name == 'libxess_fg.dll' else ('libxell',)
                files = {n: p for n, p in files.items() if not n.lower().startswith(prefix)}
            else:
                overrides[option] = str(target)
    # FakeNvapi is a separate dependency of the current FG route.
    from core.config_generator import ConfigGenerator
    template = ConfigGenerator.parser(package['OptiScaler.ini'].read_text(encoding='utf-8-sig'))
    if any(not template.has_option('Libraries', key) for key in overrides):
        raise ValueError('Selected release does not support native runtime library paths.')
    return files, reused, {'Libraries': overrides}
