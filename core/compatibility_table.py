"""Local, user-overridable compatibility data; no remote executable rules."""
import json
from pathlib import Path
from core.paths import resource_root, data_root
from core.settings import HOOKS

def lookup(analysis, user_path=None):
    paths = [resource_root() / 'assets/compatibility.json',
             Path(user_path) if user_path else data_root() / 'compatibility.json']
    result = {}
    for path in paths:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        exe = Path(analysis['target_exe']).name.lower()
        result.update(data.get('steam', {}).get(str(analysis.get('steam_appid')), {}))
        result.update({k.lower():v for k,v in data.get('exe',{}).items()}.get(exe, {}))
    if result.get('hook') and result['hook'] not in HOOKS:
        raise ValueError('Compatibility table contains an unsupported proxy.')
    overrides = result.get('ini_overrides', {})
    allowed = {'InitFlags': {'DepthInverted', 'JitterCancellation'},
               'Spoofing': {'Dxgi', 'StreamlineSpoofing'}}
    for section, values in overrides.items():
        if section not in allowed or not isinstance(values,dict):
            raise ValueError('Compatibility INI overrides must target depth, jitter or spoofing.')
        for key,value in values.items():
            if key not in allowed[section] or str(value).lower() not in ('true','false','auto'):
                raise ValueError('Unsupported compatibility INI override.')
    return result or None
