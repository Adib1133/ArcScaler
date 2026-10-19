"""Configuration contract verified against OptiScaler v0.9.4 Config.cpp.

The selected release's template is checked before deployment. Hardware selection
belongs to Intel's runtime; NetworkModel is a network variant, not XMX/DP4a.
"""
import configparser
import io
from core.settings import QUALITY_RATIOS, SCHEMA, validate


class ConfigGenerator:
    MARKER_SIGNATURE = '; OPTISCALER_XESS_SUITE_INJECTED = true'
    QUALITY_RATIOS = QUALITY_RATIOS
    FG_INPUTS = SCHEMA['fg_input'].options

    @staticmethod
    def parser(text=''):
        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        cfg.optionxform = str
        if text:
            cfg.read_string(text)
        return cfg

    @staticmethod
    def generate_nvngx_ini(starting_upscaler='DLSS', hook_method='dxgi.dll', xess_quality='User Defined',
                           frame_gen_enabled=False, intercept_dlssg=True, intercept_fsr3=False,
                           reflex_to_xell_enabled=False, reflex_boost=False, xess_network_model=None,
                           sharpness=0.3, custom_scale=None, invert_depth=None, jitter_cancellation=None,
                           overlay_menu=True, log_level=2, fg_input=None, template=None, upscaler_enabled=False, gpu_spoofing=False):
        """Compatibility method name; output is deployed as OptiScaler.ini."""
        validate({k: v for k,v in locals().items() if k in SCHEMA and not (k == 'fg_input' and v is None)})
        if reflex_boost:
            raise ValueError('Reflex Boost is not a supported OptiScaler setting. Use the game control.')
        if log_level not in range(5):
            raise ValueError('Invalid logging level.')
        if frame_gen_enabled and not overlay_menu:
            raise ValueError('OptiScaler frame generation requires its overlay menu.')
        if fg_input is None:
            if frame_gen_enabled and intercept_dlssg and intercept_fsr3:
                raise ValueError('Choose one frame-generation input: DLSSG or FSR FG.')
            fg_input = 'dlssg' if intercept_dlssg else 'fsrfg' if intercept_fsr3 else 'upscaler'
        if fg_input not in ConfigGenerator.FG_INPUTS:
            raise ValueError('Unsupported frame-generation input.')
        boolean = lambda value: str(bool(value)).lower()
        override_ratio = upscaler_enabled and (custom_scale is not None or xess_quality != 'User Defined')
        ratio = 1 / float(custom_scale) if custom_scale is not None else ConfigGenerator.QUALITY_RATIOS.get(xess_quality, 1.5)
        values = {
            'Upscalers': {'Dx11Upscaler': 'xess', 'Dx12Upscaler': 'xess', 'VulkanUpscaler': 'xess'},
            'Inputs': {'EnableXeSSInputs': boolean(upscaler_enabled and starting_upscaler == 'XeSS'), 'UseFsr2Inputs': boolean(upscaler_enabled),
                       'UseFsr3Inputs': boolean(upscaler_enabled), 'UseFfxInputs': boolean(upscaler_enabled), 'EnableDlssInputs': boolean(upscaler_enabled and starting_upscaler == 'DLSS'),
                       'EnableFsr2Inputs': boolean(upscaler_enabled and starting_upscaler == 'FSR'),
                       'EnableFsr3Inputs': boolean((upscaler_enabled and starting_upscaler == 'FSR') or (frame_gen_enabled and fg_input == 'fsrfg30')),
                       'EnableFfxInputs': boolean((upscaler_enabled and starting_upscaler == 'FSR') or (frame_gen_enabled and fg_input == 'fsrfg'))},
            'FrameGen': {'Enabled': boolean(frame_gen_enabled), 'FGInput': fg_input if frame_gen_enabled else 'nofg',
                         'FGOutput': 'xefg' if frame_gen_enabled else 'nofg'},
            'XeSS': {'NetworkModel': 'auto' if xess_network_model is None else str(xess_network_model), 'BuildPipelines': 'true'},
            'NvApi': {'OverrideNvapiDll': boolean(frame_gen_enabled or gpu_spoofing or reflex_to_xell_enabled)},
            'Spoofing': {'Dxgi': boolean(gpu_spoofing), 'StreamlineSpoofing': boolean(gpu_spoofing)},
            'Sharpness': {'OverrideSharpness': boolean(upscaler_enabled), 'Sharpness': f'{float(sharpness):.3f}'},
            'CAS': {'Enabled': boolean(upscaler_enabled and float(sharpness) > 0)},
            'InitFlags': {'DepthInverted': 'auto' if invert_depth is None else boolean(invert_depth),
                          'JitterCancellation': 'auto' if jitter_cancellation is None else boolean(jitter_cancellation)},
            'UpscaleRatio': {'UpscaleRatioOverrideEnabled': boolean(override_ratio), 'UpscaleRatioOverrideValue': f'{ratio:.6f}'},
            'QualityOverrides': {'QualityRatioOverrideEnabled': 'false'},
            'DRS': {'DrsMinOverrideEnabled': 'false', 'DrsMaxOverrideEnabled': 'false'},
            'Menu': {'OverlayMenu': boolean(overlay_menu), 'ShortcutKey': '0x2D'},
            'Log': {'LogToFile': 'true', 'LogLevel': str(log_level), 'LogToConsole': 'false'},
        }
        cfg = ConfigGenerator.parser(template or '')
        if template is not None:
            missing = [f'{s}.{k}' for s, items in values.items() for k in items if not cfg.has_option(s, k)]
            if missing:
                raise ValueError('Selected release has an incompatible configuration schema: ' + ', '.join(missing))
            return ConfigGenerator.update_ini_text(template, values)

        for section, items in values.items():
            if not cfg.has_section(section):
                cfg.add_section(section)
            for key, value in items.items():
                cfg.set(section, key, value)
        out = io.StringIO()
        cfg.write(out)
        return ConfigGenerator.MARKER_SIGNATURE + '\n; Configured intent; verify activation in the game overlay.\n' + out.getvalue()

    @staticmethod
    def update_ini_text(ini_text: str, values: dict) -> str:
        """Update key-value pairs in INI text in-place, preserving all comments, sections, and formatting."""
        eol = '\r\n'
        lines = ini_text.splitlines()

        current_section = None
        section_lines = {}

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('[') and ']' in stripped:
                sec = stripped[1:stripped.find(']')].strip()
                current_section = sec
                if current_section not in section_lines:
                    section_lines[current_section] = []
            elif current_section is not None:
                section_lines[current_section].append(idx)

        for section, items in values.items():
            if section not in section_lines:
                lines.append(f'[{section}]')
                section_lines[section] = []
                for k, v in items.items():
                    lines.append(f'{k}={v}')
                    section_lines[section].append(len(lines) - 1)
                continue

            sec_indices = section_lines[section]
            remaining = dict(items)

            for idx in sec_indices:
                line = lines[idx]
                stripped = line.strip()
                if not stripped or stripped.startswith(';') or stripped.startswith('#'):
                    continue
                if '=' in line:
                    key, _ = line.split('=', 1)
                    key = key.strip()
                    if key in remaining:
                        val = remaining.pop(key)
                        lines[idx] = f'{key}={val}'

            if remaining:
                insert_pos = sec_indices[-1] + 1 if sec_indices else len(lines)
                for key, val in remaining.items():
                    lines.insert(insert_pos, f'{key}={val}')
                    insert_pos += 1

        marker = ConfigGenerator.MARKER_SIGNATURE
        header = marker + eol + '; Configured intent; verify activation in the game overlay.' + eol
        body = eol.join(lines) + eol
        if marker in body:
            return body
        return header + body

    @staticmethod
    def generate_fakenvapi_ini(template, reflex_enabled):
        cfg = ConfigGenerator.parser(template)
        if not cfg.has_option('fakenvapi', 'force_reflex'):
            raise ValueError('FakeNvapi configuration lacks force_reflex support.')
        val = '2' if reflex_enabled else '0'
        if template:
            return ConfigGenerator.update_ini_text(template, {'fakenvapi': {'force_reflex': val}})
        cfg.set('fakenvapi', 'force_reflex', val)
        out = io.StringIO()
        cfg.write(out)
        return out.getvalue()
