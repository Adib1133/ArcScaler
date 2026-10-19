"""Local diagnostic reports. Personal filesystem paths are redacted by default."""
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import platform


def diagnostic_report(profile, hardware, analysis, plan, health, logs, redact=True, evidence=None):
    from core.game_support import runtime_evidence
    try:evidence=runtime_evidence(analysis or {}) if evidence is None else evidence
    except (OSError,ValueError,KeyError) as exc:evidence=[{'error':str(exc)}]
    from core.settings import SCHEMA
    report=dict(format_version=1, app='ArcScaler', app_version='1.1.0', generated_at=datetime.now(timezone.utc).isoformat(),
                python=platform.python_version(), game={k:(profile or {}).get(k) for k in ('name','target_exe','base_dir')},
                hardware=hardware, draft={k:(profile or {}).get(k) for k in SCHEMA}, evidence=evidence,
                installation_health=health, installation_plan=plan, recent_activity=list(logs)[-150:])
    def clean(value):
        if isinstance(value,dict):return {clean(k):clean(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [clean(v) for v in value]
        if isinstance(value,str):
            value=value.replace(str(Path.home()),'<USER>')
            value=re.sub(r'(?i)[A-Z]:[\\/][^\n\r"\x27]*', '<PATH>', value)
            value=re.sub(r'\\\\[^\n\r"\x27]+', '<NETWORK_PATH>',value)
            return value
        return value
    return json.dumps(clean(report) if redact else report,ensure_ascii=False,indent=2)
