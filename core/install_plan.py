"""Read-only installation decisions, shared by preview and deployment."""
from pathlib import Path
from copy import deepcopy
from core.game_rules import recipe_for, validate_location, WIKI, REVIEWED
from core.compatibility_table import lookup
from core.game_support import capabilities
from core.safety import SafetyManager
from core.files import canonical, sha256
from core.settings import HOOKS, validate, defaults
from core.native_runtimes import BUNDLED, resolve_deployment_package


def build_plan(analysis, settings, version_manager=None):
    effective = defaults() | {k:v for k,v in settings.items() if k in defaults()}
    notes, errors = [], []
    result = dict(settings=effective, notes=notes, errors=errors, files=[], decisions=[],
                  target_dir=analysis['target_dir'], target_exe=analysis['target_exe'],
                  sources=[], recipe_id=None, reviewed=None, ini_overrides={})
    def why(decision, reason):
        result['decisions'].append({'decision':decision, 'why':reason})
    try:
        effective.update(validate(effective, require_mode=True))
        recipe = recipe_for(analysis)
        local = lookup(analysis, settings.get('compatibility_path'))
        if local:
            recipe = (recipe or {}) | local
        native = capabilities(analysis)
        result['capabilities'] = native
        force = settings.get('force',False)
        reuse_native = effective['runtime_method'] != BUNDLED
        automatic = effective['installation_mode'] == 'automatic' and not force and not reuse_native
        if force:
            notes.append('Force inject overrides compatibility conflicts. Original files are backed up; externally changed managed files are preserved separately in .optiscaler_backup/preserved. Use Restore original files to undo installation. Running games, invalid files and corrupt backups still block installation.')
        if analysis.get('anti_cheat'):
            errors.append(analysis['anti_cheat'] + ' detected. Auto installation is blocked; only use an explicitly supported offline configuration.')
        api = analysis.get('graphics_api', 'Unknown')
        why('Target: '+analysis['target_dir'], 'Beside the selected real '+analysis.get('architecture','x64')+' game executable.')
        why('Graphics API: '+api, 'Executable imports and local graphics modules; multiple APIs require manual verification.')
        if effective['frame_gen_enabled'] and api != 'DX12':
            errors.append('Frame Generation requires detected DirectX 12; detected API: '+api+'.')
            result['blocking_error'] = errors[-1]
        if recipe:
            result.update(recipe_id=recipe.get('id'), title=recipe.get('title','Local compatibility entry'),
                          reviewed=REVIEWED, ini_overrides=deepcopy(recipe.get('ini_overrides',{})))
            if recipe.get('page'): result['sources']=[WIKI+recipe['page']]
            try:validate_location(recipe, analysis['target_exe'])
            except ValueError as exc:errors.append(str(exc))
            notes.append(recipe.get('notes','Local compatibility override.'))
            if automatic:
                effective['starting_upscaler']=recipe.get('input',effective['starting_upscaler'])
                if recipe.get('fg'): effective['fg_input']=recipe['fg'][0]
            if recipe.get('no_spoof'): effective['gpu_spoofing']=False
            if effective['frame_gen_enabled'] and not recipe.get('fg', ['allowed']):
                errors.append('No reviewed XeSS FG route for this game.')
        elif automatic:
            if native['dlss']: effective['starting_upscaler']='DLSS'
            elif native['fsr']: effective['starting_upscaler']='FSR'
            elif native['xess']: effective['starting_upscaler']='XeSS'
            elif effective['upscaler_enabled']: errors.append('No supported upscaler input detected. Inspect the game and use Manual mode only after verifying support.')
            if native['dlssg']: effective['fg_input']='dlssg'
            elif native['fsrfg']: effective['fg_input']='fsrfg'
            elif effective['frame_gen_enabled']: errors.append('No native frame-generation input detected.')
        if not reuse_native and effective['upscaler_enabled'] and (native['xess'] or recipe and recipe.get('native_xess')):
            errors.append('Native XeSS is present. Use the in-game Super Resolution setting instead of replacing it.')
        if not reuse_native and effective['frame_gen_enabled'] and native['xefg']:
            errors.append('Native XeSS Frame Generation is present; no injection is needed.')
        target = Path(analysis['target_dir'])
        managed = set()
        if SafetyManager.has_active_backup(target):
            manifest = SafetyManager.load_manifest(target, analysis.get('all_target_dirs',[]))
            for folder,record in manifest['folders'].items():
                managed.update(canonical(Path(folder)/n) for n in [*record['created_files'],*(i['filename'] for i in record['overwritten_files'])])
        occupied = {p.name.lower():p for p in target.iterdir() if p.is_file()}
        conflicts = [h for h in HOOKS if h.lower() in occupied and canonical(occupied[h.lower()]) not in managed]
        if conflicts: notes.append('Existing proxies preserved (possibly ReShade): '+', '.join(conflicts))
        if automatic and not force:
            preferred = recipe.get('hook') if recipe else None
            imports = set(analysis.get('imports',[]))
            # Imported utility DLLs avoid intercepting the graphics stack when possible.
            candidates = ([preferred] if preferred else []) + [h for h in ('version.dll','winmm.dll','wininet.dll','winhttp.dll','dbghelp.dll') if h in imports]
            candidates += ['dxgi.dll','winmm.dll','version.dll','dbghelp.dll','wininet.dll','winhttp.dll']
            if api == 'DX12': candidates += ['d3d12.dll']
            free = [h for h in candidates if h.lower() not in occupied or canonical(occupied[h.lower()]) in managed]
            if not free: errors.append('No collision-free proxy filename is available.')
            else: effective['hook_method']=free[0]
            why('Proxy: '+effective['hook_method'], 'Local compatibility preference, then imported utility library, then collision-free fallback; in-game activation must be verified.')
        else:
            why('Proxy: '+effective['hook_method'], 'Explicit user selection; existing files will be backed up.')
        if effective['hook_method']=='OptiScaler.asi':
            notes.append('OptiScaler.asi requires an existing ASI loader; ArcScaler does not install one.')
        gpu = settings.get('hardware',{}).get('primary_gpu',{})
        result['xess_path'] = gpu.get('xess_acceleration','Runtime auto')
        why('XeSS path: '+result['xess_path'], 'Intel runtime selects XMX on Arc or DP4a on integrated Intel; NetworkModel is not a hardware selector.')
        why('Input: '+effective['starting_upscaler'], 'Local compatibility entry or detected DLSS, FSR/FidelityFX and XeSS runtimes.' if automatic else 'Explicit user selection.')
        if version_manager is None:
            from core.version_manager import VersionManager
            from core.paths import resource_root
            version_manager=VersionManager(resource_root()/'assets/versions')
        tag=effective['optiscaler_version']
        if not tag:
            versions=version_manager.get_installed_versions()
            tag=versions[0] if versions else ''
        effective['optiscaler_version'] = tag
        package, reused, library_overrides = resolve_deployment_package(analysis, effective, version_manager)
        result['reused_files'] = reused
        from core.runtime_validation import inspect_runtime
        result['runtime_checks'] = {path: inspect_runtime(Path(path), Path(path).name.lower()) for path in reused}
        for path, check in result['runtime_checks'].items():
            notes.append(f'{Path(path).name}: version {check["version"]}; {check["api"]}. Runtime compatibility remains unverified.')
        for section, values in library_overrides.items():
            result['ini_overrides'].setdefault(section, {}).update(values)
        if reuse_native:
            notes.append('Native DLL structure checked; runtime/API compatibility must be verified in game. Reused files remain untouched by installation and restoration.')
            if effective['frame_gen_enabled']:
                notes.append('Select DLSS Frame Generation in game for DLSSG input; disable native XeSS FG while testing this route. FakeNvapi helpers are still installed.')
            for path in reused:
                why('Reuse '+path, 'Existing game DLL; no copy or replacement. Hash checked again before installation.')
        effective['optiscaler_version']=tag
        result['files']=[effective['hook_method'] if n=='OptiScaler.dll' else n for n in package]
        result['source_hashes']={name:sha256(path) for name,path in package.items()}
        result['file_actions'] = []
        result['target_hashes'] = {}
        for name in result['files']:
            path = target / name
            result['target_hashes'][str(path)] = sha256(path) if path.is_file() else None
            result['file_actions'].append(dict(action='Replace' if path.exists() else 'Add', file=name,
                location=str(path), reason='Selected route dependency; original backed up' if path.exists() else 'Selected route dependency'))
        for path in reused:
            check = result['runtime_checks'][path]
            result['file_actions'].append(dict(action='Reuse', file=Path(path).name, location=path,
                reason='Native runtime; version '+check['version']+'; required exports present'))
        if SafetyManager.has_active_backup(target):
            manifest = SafetyManager.load_manifest(target, analysis.get('all_target_dirs', []))
            for folder, record in manifest['folders'].items():
                originals = {i['filename'] for i in record['overwritten_files']}
                for name in sorted(set(record['created_files']) | originals):
                    if name not in result['files']:
                        path = Path(folder)/name
                        result['target_hashes'][str(path)] = sha256(path) if path.is_file() else None
                        result['file_actions'].append(dict(action='Restore' if name in originals else 'Remove',
                            file=name, location=str(path), reason='No longer required by selected route'))
        for name in result['files']:
            why('Write '+name, 'Selected release '+tag+' / '+('proxy entry point' if name==effective['hook_method'] else 'selected mode runtime or configuration')+'; snapshot existing file and verify after copying.')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(str(exc))
        result['blocking_error'] = str(exc)
    return result


def format_plan(plan):
    lines=['Destination: '+plan['target_dir']]
    lines += [d['decision']+' — '+d['why'] for d in plan.get('decisions',[])]
    lines += plan['notes']
    lines += ['Not ready: '+error for error in plan['errors']]
    return '\n'.join(lines)
