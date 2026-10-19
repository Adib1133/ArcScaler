"""Reviewed game recipes. Network prose never becomes executable configuration."""
from pathlib import Path
import re
import unicodedata

WIKI = 'https://github.com/optiscaler/OptiScaler/wiki/'
REVIEWED = '2026-09-10'


def normalize_title(value):
    value = unicodedata.normalize('NFKD', str(value)).casefold()
    return ''.join(c for c in value if c.isalnum())


def recipes():
    import json
    from core.paths import resource_root
    data=json.loads((resource_root()/'assets/compatibility.json').read_text(encoding='utf-8'))
    return list({r['id']:r for r in data['exe'].values()}.values())



def match_recipe(names):
    identities = {normalize_title(n) for n in names if n}
    matches = [r for r in recipes() if identities & {normalize_title(n) for n in (r['title'], *r['aliases'])}]
    if len(matches) > 1:
        raise ValueError('Conflicting game identities. Select the actual game executable in its own installation folder.')
    return matches[0] if matches else None


def identity_names(analysis):
    exe = Path(analysis['target_exe'])
    stem = re.sub(r'(?i)[-_](win64|wingdk)[-_]shipping$', '', exe.stem)
    return [stem, analysis.get('game_name', ''), Path(analysis.get('base_dir', exe.parent)).name]


def recipe_for(analysis):
    return match_recipe(identity_names(analysis))


def validate_location(recipe, exe):
    suffix = recipe.get('suffix')
    if suffix and not Path(exe).parent.as_posix().casefold().endswith('/' + suffix.casefold()):
        raise ValueError(f"{recipe['title']}: select the executable in {suffix}; this selected folder is not the documented injection location.")
