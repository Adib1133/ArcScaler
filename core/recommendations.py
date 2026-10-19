"""One recommendation result for display, application, and installation preview."""
from core.settings import defaults
from core.game_support import capabilities
from core.install_plan import build_plan


def recommend(analysis, current, version_manager, hardware=None):
    caps=capabilities(analysis)
    values=defaults()
    values.update(optiscaler_version=current.get('optiscaler_version',''), xess_quality='Quality',
                  upscaler_enabled=bool((caps['dlss'] or caps['fsr']) and not caps['xess']),
                  frame_gen_enabled=bool((caps['dlssg'] or caps['fsrfg']) and not caps['xefg'] and analysis.get('graphics_api')=='DX12'))
    from core.native_runtimes import BUNDLED
    if current.get('runtime_method', BUNDLED) != BUNDLED:
        # A suggestion may tune quality, but must not replace an explicit route.
        for key in ('runtime_method', 'native_xess_path', 'native_xefg_path', 'native_xell_path',
                    'upscaler_enabled', 'frame_gen_enabled', 'starting_upscaler', 'fg_input',
                    'installation_mode', 'hook_method'):
            if key in current:
                values[key] = current[key]
    plan=build_plan(analysis,dict(values,hardware=hardware or {}),version_manager)
    notes=[]
    if caps['xess']:notes.append('XeSS runtime evidence is present. Check the native game setting first.')
    if not any(caps[k] for k in ('dlssg','fsrfg','xefg')):
        notes.append('No FG runtime detected with sufficient confidence. Upscaler-driven FG requires upscaling interception and HUD tuning; it is not offered by the current input controls.')
    if caps.get('fsrfg_uncertain') and not caps['fsrfg']:notes.append('FSR / FidelityFX files alone do not confirm a frame-generation input.')
    if values['frame_gen_enabled']:notes.append('FG requires DirectX 12 and in-game confirmation.')
    return dict(settings=plan['settings'], plan=plan, notes=notes, ready=not plan['errors'], errors=plan['errors'])
