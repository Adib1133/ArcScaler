"""Single settings contract shared by profiles, controls, planning and INI output."""
from dataclasses import dataclass
import math

QUALITY_RATIOS = {'Ultra Quality Plus': 1.2, 'Ultra Quality': 1.3, 'Quality': 1.5,
                  'Balanced': 1.7, 'Performance': 2., 'Ultra Performance': 3.}
HOOKS = ('dxgi.dll', 'winmm.dll', 'version.dll', 'dbghelp.dll', 'd3d12.dll',
         'wininet.dll', 'winhttp.dll', 'OptiScaler.asi', 'nvngx.dll')

@dataclass(frozen=True)
class Setting:
    label: str
    kind: type
    default: object
    mode: str = 'compatibility'
    options: tuple = ()
    bounds: tuple = ()

SCHEMA = {
    'upscaler_enabled': Setting('Super Resolution', bool, False, 'sr'),
    'frame_gen_enabled': Setting('Frame Generation', bool, False, 'fg'),
    'xess_quality': Setting('Quality', str, 'User Defined', 'sr', ('User Defined', *QUALITY_RATIOS)),
    'custom_scale': Setting('Custom render scale', float, None, 'sr', bounds=(.25, 1.)),
    'sharpness': Setting('Sharpness', float, .3, 'sr', bounds=(0., 1.)),
    'starting_upscaler': Setting('Upscaler input', str, 'DLSS', 'sr', ('DLSS', 'FSR', 'XeSS')),
    'xess_network_model': Setting('XeSS network', int, None, 'sr', (None, 0, 1, 2, 3, 4, 5)),
    'fg_input': Setting('Frame-generation input', str, 'dlssg', 'fg', ('dlssg', 'fsrfg', 'fsrfg30')),
    'installation_mode': Setting('Install mode', str, 'automatic', options=('automatic', 'manual')),
    'runtime_method': Setting('Runtime method', str, 'Bundled runtimes', 'native',
                              ('Bundled runtimes', 'Reuse native XeSS + FG', 'Keep native XeSS; add missing FG')),
    'native_xess_path': Setting('XeSS upscaling DLL', str, '', 'native'),
    'native_xefg_path': Setting('XeSS frame-generation DLL', str, '', 'native'),
    'native_xell_path': Setting('XeLL DLL', str, '', 'native'),
    'optiscaler_version': Setting('Release', str, ''),
    'hook_method': Setting('Proxy filename', str, 'dxgi.dll', options=HOOKS),
    'invert_depth': Setting('Depth inverted', bool, None, options=(None, True, False)),
    'jitter_cancellation': Setting('Jitter cancellation', bool, None, options=(None, True, False)),
    'gpu_spoofing': Setting('GPU identity spoofing', bool, False),
    'reflex_to_xell_enabled': Setting('Reflex through FakeNvapi', bool, False),
}

def defaults():
    return {key: spec.default for key, spec in SCHEMA.items()}

def validate(values, require_mode=False):
    result = defaults() | {k: v for k, v in values.items() if k in SCHEMA}
    for key, spec in SCHEMA.items():
        value = result[key]
        if value is None and spec.default is None:
            continue
        if spec.kind is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{spec.label}: expected a finite number.')
        elif type(value) is not spec.kind:
            raise ValueError(f'{spec.label}: invalid type.')
        if spec.options and value not in spec.options:
            raise ValueError(f'{spec.label}: unsupported value.')
        if spec.bounds and not spec.bounds[0] <= value <= spec.bounds[1]:
            raise ValueError(f'{spec.label}: outside supported range.')
    if require_mode and not (result['upscaler_enabled'] or result['frame_gen_enabled']):
        raise ValueError('Turn on Super Resolution or Frame Generation before installing.')
    return result

def select_mode(values, key, enabled):
    result = dict(values)
    result[key] = bool(enabled)
    return validate(result)

def migrate(values):
    result = defaults() | values
    if result.get('fg_input') == 'upscaler':
        result['legacy_fg_input'] = 'upscaler'
        result['fg_input'] = 'dlssg'
    result['settings_schema'] = 3
    return result
