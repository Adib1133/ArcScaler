"""Separate local runtime evidence from confirmed in-game feature availability."""
from pathlib import Path
from core.files import canonical
from core.safety import SafetyManager


def runtime_evidence(analysis):
    managed = {}
    target = analysis.get('target_dir')
    if target and SafetyManager.has_active_backup(target):
        manifest = SafetyManager.load_manifest(target, analysis.get('all_target_dirs', []))
        for folder, record in manifest['folders'].items():
            for name in record['created_files']:managed[canonical(Path(folder)/name)]=None
            for item in record['overwritten_files']:managed[canonical(Path(folder)/item['filename'])]=item['backup_path']
    rows=[]
    for loc in analysis.get('discovered_locations', []):
        for detail in loc.get('details', []):
            path=detail.get('full_path',str(Path(loc.get('folder',''))/detail['filename']))
            key=canonical(path)
            rows.append(dict(filename=detail['filename'], path=path, origin='ArcScaler managed' if key in managed else 'Game / unmanaged', confidence='DLL presence only', native_candidate=key not in managed))
            if managed.get(key):rows.append(dict(filename=detail['filename'],path=managed[key],origin='Original backup',confidence='Original DLL presence',native_candidate=True))
    return rows


def capabilities(analysis):
    names={r['filename'].lower() for r in runtime_evidence(analysis) if r['native_candidate']}
    return {'xess': bool(names & {'libxess.dll', 'libxess_dx11.dll'}),
            'xefg': bool(names & {'libxess_fg.dll', 'libxessfg.dll'}),
            'dlss': 'nvngx_dlss.dll' in names,
            'dlssg': 'nvngx_dlssg.dll' in names,
            'fsr': any(n.startswith(('ffx_fsr2', 'ffx_fsr3', 'amd_fidelityfx')) for n in names),
            'fsrfg': any('frameinterpolation' in n or 'framegeneration' in n for n in names),
            'fsrfg_uncertain': any(n.startswith(('ffx_fsr3', 'amd_fidelityfx')) for n in names)}

