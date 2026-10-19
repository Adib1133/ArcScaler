"""Deterministic executable selection and read-only game library discovery."""
import os
from pathlib import Path
import subprocess
import struct
from core.files import canonical, inside, validate_pe
from core.game_rules import recipe_for, validate_location
from core.pe import inspect_pe


class GameDetector:
    DLSS_FILES = ['nvngx_dlss.dll', 'nvngx_dlssg.dll', 'nvngx_dlssd.dll', '_nvngx.dll', 'nvngx.dll']
    FSR_FILES = ['ffx_fsr2api_dx12.dll', 'ffx_fsr2api_x64.dll', 'ffx_fsr3api_dx12.dll',
                 'ffx_fsr3api_x64.dll', 'amd_fidelityfx_dx12.dll', 'amd_fidelityfx_vk.dll',
                 'ffx_frameinterpolation_x64.dll', 'ffx_frameinterpolation_dx12.dll', 'amd_fidelityfx_framegeneration_dx12.dll']
    XESS_FILES = ['libxess.dll', 'libxess_dx11.dll', 'libxess_fg.dll', 'libxessfg.dll', 'libxell.dll']
    OPTISCALER_FILES = ['optiscaler.dll', 'optiscaler.ini', 'fakenvapi.dll', 'fakenvapi.ini']
    IGNORE_DIRS = {'saves', 'savegames', 'screenshots', 'logs', 'crashdumps', 'shadercache',
                   '_commonredist', 'directx', 'vcredist', '.git', '.optiscaler_backup'}
    ANTI_CHEAT_INDICATORS = {'Easy Anti-Cheat (EAC)': ['easyanticheat', 'start_protected_game.exe'],
                            'BattlEye': ['battleye', 'beservice.exe', 'launch_game_be.exe'],
                            'Riot Vanguard': ['vgk.sys', 'vgc.exe'], 'Equ8': ['equ8']}
    HELPER_PREFIXES = ('crash', 'unitycrash', 'unins', 'setup', 'dxsetup', 'vcredist', 'report', 'cef', 'updater')

    @staticmethod
    def _walk(root):
        for folder, dirs, files in os.walk(root):
            from core.tasks import checkpoint
            checkpoint()
            depth = len(Path(folder).relative_to(root).parts)
            dirs[:] = sorted(d for d in dirs if depth < 12 and d.lower() not in GameDetector.IGNORE_DIRS
                             and not Path(folder, d).is_symlink()
                             and not (hasattr(Path(folder, d), 'is_junction') and Path(folder, d).is_junction()))
            yield folder, dirs, sorted(files)

    @staticmethod
    def resolve_shortcut_if_needed(path):
        if not path or not str(path).lower().endswith('.lnk'):
            return path
        env = dict(os.environ, OPTISCALER_SHORTCUT=str(Path(path).resolve()))
        command = '(New-Object -ComObject WScript.Shell).CreateShortcut($env:OPTISCALER_SHORTCUT).TargetPath'
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                env=env, capture_output=True, text=True, timeout=10,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not result.stdout.strip():
            raise ValueError('Unable to resolve shortcut.')
        return result.stdout.strip()

    @staticmethod
    def _infer_game_root(exe_path):
        folder = Path(exe_path).resolve().parent
        parts = folder.parts
        for i in range(len(parts) - 1, 1, -1):
            if parts[i].lower() in ('binaries', 'bin', 'bin-mt'):
                return str(Path(*parts[:i]))
        return str(folder)

    @staticmethod
    def _find_primary_exe_in_folder(folder):
        candidates = []
        for root, dirs, files in GameDetector._walk(folder):
            if 'engine' in [p.lower() for p in Path(root).relative_to(folder).parts]:
                dirs[:] = []
                continue
            for name in files:
                path = Path(root) / name
                if path.suffix.lower() != '.exe' or name.lower().startswith(GameDetector.HELPER_PREFIXES):
                    continue
                try:
                    validate_pe(path, dll=False)
                except ValueError:
                    continue
                shipping = name.lower().endswith(('-win64-shipping.exe', '-wingdk-shipping.exe'))
                launcher = any(word in name.lower() for word in ('launcher', 'start', 'bootstrap'))
                recipe = recipe_for({'target_exe': str(path), 'base_dir': folder})
                correct_location = False
                if recipe:
                    try:
                        validate_location(recipe, path)
                        correct_location = True
                    except ValueError:
                        pass
                candidates.append((not correct_location, not shipping, launcher, -path.stat().st_size, str(path)))
        return sorted(candidates)[0][-1] if candidates else None

    @staticmethod
    def _deep_scan_upscalers(base_dir, primary_target_dir):
        categories = {'DLSS': GameDetector.DLSS_FILES, 'FSR': GameDetector.FSR_FILES,
                      'XeSS': GameDetector.XESS_FILES, 'OptiScaler': GameDetector.OPTISCALER_FILES}
        lookup = {name.lower(): category for category, names in categories.items() for name in names}
        detected = {category: [] for category in categories}
        locations = []
        for root, dirs, files in GameDetector._walk(base_dir):
            details = []
            for name in files:
                category = lookup.get(name.lower())
                if category:
                    path = Path(root) / name
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    if name not in detected[category]:
                        detected[category].append(name)
                    details.append({'filename': name, 'category': category, 'full_path': str(path), 'size': size})
            if details:
                locations.append({'folder': root, 'relative_folder': os.path.relpath(root, base_dir),
                                  'is_primary': canonical(root) == canonical(primary_target_dir),
                                  'files': [d['filename'] for d in details], 'details': details})
        return locations, detected

    @staticmethod
    def _detect_anti_cheat(target_dir, base_dir):
        for root, dirs, files in GameDetector._walk(base_dir):
            for name in [*dirs, *files]:
                for label, indicators in GameDetector.ANTI_CHEAT_INDICATORS.items():
                    if any(ind in name.lower() for ind in indicators):
                        return label
        return None

    @staticmethod
    def analyze_game(path, base_dir=None):
        try:
            if not path:
                raise ValueError('Path is empty.')
            resolved = Path(GameDetector.resolve_shortcut_if_needed(path)).resolve()
            if resolved.is_dir():
                base = str(resolved)
                candidate = GameDetector._find_primary_exe_in_folder(base)
                if not candidate:
                    raise ValueError('No supported AMD64 game executable found.')
                exe = Path(candidate)
            else:
                exe = resolved
                base = GameDetector._infer_game_root(exe)
                if any(word in exe.stem.lower() for word in ('launcher', 'bootstrap', 'start')):
                    candidate = GameDetector._find_primary_exe_in_folder(base)
                    if candidate:
                        exe = Path(candidate)
            if exe.suffix.lower() != '.exe':
                raise ValueError('Select a Windows game executable.')
            validate_pe(exe, dll=False)
            if base_dir:
                if not inside(exe, base_dir):
                    raise ValueError('Selected executable is outside the game installation.')
                base = str(Path(base_dir).resolve())
            folder = str(exe.parent)
            lower_parts = [p.lower() for p in exe.parts]
            engine = 'Generic Windows / DirectX'
            if 'binaries' in lower_parts and any(p in lower_parts for p in ('win64', 'wingdk')):
                engine = 'Unreal Engine'
            elif 'bin' in lower_parts and any(p.startswith('x64') for p in lower_parts):
                engine = 'REDengine'
            elif any(p.name.lower().endswith('_data') for p in exe.parent.iterdir() if p.is_dir()):
                engine = 'Unity Engine'
            locations, detected = GameDetector._deep_scan_upscalers(base, folder)
            pe = inspect_pe(exe)
            if pe['graphics_api'] == 'Unknown':
                local = {p.name.lower() for p in exe.parent.iterdir() if p.is_file()}
                evidence = {'DX12': {'d3d12.dll', 'amd_fidelityfx_dx12.dll', 'ffx_fsr2api_dx12.dll'},
                            'DX11': {'d3d11.dll', 'libxess_dx11.dll'}, 'Vulkan': {'vulkan-1.dll', 'amd_fidelityfx_vk.dll'}}
                found = [api for api,names in evidence.items() if names & local]
                pe.update(graphics_apis=found, graphics_api=found[0] if len(found)==1 else 'Unknown' if not found else 'Multiple')
            appid = None
            for parent in exe.parents:
                if parent.name.lower() == 'common' and parent.parent.name.lower() == 'steamapps':
                    import re
                    for manifest in parent.parent.glob('appmanifest_*.acf'):
                        text = manifest.read_text(encoding='utf-8', errors='replace')
                        match = re.search(r'"installdir"\s+"([^"]+)"', text)
                        if match and Path(base).resolve().is_relative_to(parent / match[1]):
                            appid = manifest.stem.removeprefix('appmanifest_')
                            break
            return {'valid': True, 'target_exe': str(exe), 'target_dir': folder, 'base_dir': base,
                    **pe, 'steam_appid': appid,
                    'all_target_dirs': [folder], 'discovered_locations': locations, 'detected_upscalers': detected,
                    'game_name': GameDetector._extract_game_name(base, str(exe)), 'engine': engine,
                    'anti_cheat': GameDetector._detect_anti_cheat(folder, base), 'recommended_hook': 'dxgi.dll',
                    'is_unreal_shipping': '-shipping' in exe.name.lower()}
        except (OSError, ValueError, struct.error, subprocess.SubprocessError) as exc:
            return {'valid': False, 'error': str(exc)}

    @staticmethod
    def _extract_game_name(base_dir, exe_path):
        name = Path(exe_path).stem if exe_path else Path(base_dir).name
        for suffix in ('-Win64-Shipping', '-WinGDK-Shipping', '_Shipping', '_Win64'):
            name = name.replace(suffix, '')
        return name.replace('_', ' ').replace('-', ' ').title()
