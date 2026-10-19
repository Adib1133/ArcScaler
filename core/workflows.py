"""Small intent presets and route summaries shared by the UI and tests."""
from collections import Counter
from core.native_runtimes import BUNDLED, REUSE, ADD_FG

GOALS = ('Keep native XeSS; add frame generation', 'DLSS input to XeSS upscaling',
         'Reuse native XeSS upscaling + FG', 'Advanced configuration')


def current_goal(settings):
    sr, fg = settings.get('upscaler_enabled'), settings.get('frame_gen_enabled')
    method = settings.get('runtime_method', BUNDLED)
    if method == ADD_FG and fg and not sr and settings.get('fg_input') == 'dlssg':return GOALS[0]
    if sr and not fg and settings.get('starting_upscaler') == 'DLSS':return GOALS[1]
    if method == REUSE and sr and fg and settings.get('starting_upscaler') == 'DLSS' and settings.get('fg_input') == 'dlssg':return GOALS[2]
    return GOALS[3]


def goal_settings(goal, current, native_sr=False):
    result = dict(current)
    if goal == GOALS[0]:
        result.update(runtime_method=ADD_FG, upscaler_enabled=False, frame_gen_enabled=True, fg_input='dlssg')
    elif goal == GOALS[1]:
        result.update(runtime_method=REUSE if native_sr else BUNDLED, upscaler_enabled=True,
                      frame_gen_enabled=False, starting_upscaler='DLSS')
    elif goal == GOALS[2]:
        result.update(runtime_method=REUSE, upscaler_enabled=True, frame_gen_enabled=True,
                      starting_upscaler='DLSS', fg_input='dlssg')
    return result


def route_summary(settings, plan=None):
    sr = settings.get('upscaler_enabled')
    fg = settings.get('frame_gen_enabled')
    native = settings.get('runtime_method', BUNDLED) != BUNDLED
    lines = ['Upscaling: '+(settings.get('starting_upscaler','DLSS')+' input → XeSS output' if sr else 'Game setting unchanged')]
    labels = {'dlssg':'DLSSG', 'fsrfg':'FSR 3.1 FG', 'fsrfg30':'FSR 3.0 FG'}
    lines.append('Frame generation: '+(labels.get(settings.get('fg_input'),'Selected input')+' → XeSS FG' if fg else 'Game setting unchanged'))
    if plan:
        counts = Counter(row['action'] for row in plan.get('file_actions', []))
        lines.append('Files: '+' · '.join(f'{key} {counts[key]}' for key in ('Reuse','Add','Replace','Restore','Remove') if counts[key]))
    steps = []
    if sr:steps.append('select '+settings.get('starting_upscaler','DLSS')+' upscaling')
    if fg:steps.append('enable '+labels.get(settings.get('fg_input'),'the selected FG input'))
    if steps:lines.append('In game: '+', '.join(steps)+'. Verify XeSS output in the OptiScaler overlay.')
    if native and fg:lines.append('Disable native XeSS FG while testing this route.')
    return '\n'.join(lines)
