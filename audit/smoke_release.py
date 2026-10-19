"""Launch the rebuilt EXE alone, with fresh isolated user data and no network."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.audit-deps')]
from core.files import sha256


with tempfile.TemporaryDirectory(prefix='ArcScaler standalone ') as tmp:
    folder = Path(tmp)
    exe = folder / 'ArcScaler.exe'
    shutil.copy2(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT / 'ArcScaler.exe', exe)
    user_data = folder / 'user-data'
    cache = user_data / 'assets/versions/releases_cache.json'
    cache.parent.mkdir(parents=True)
    cache.write_text('[{"tag_name":"v0.9.4","name":"v0.9.4","assets":[]}]', encoding='utf-8')
    env = dict(os.environ, ARCSCALER_DATA_DIR=str(user_data))
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    start = time.monotonic()
    result = subprocess.run([str(exe), '--smoke-test'], cwd=folder, env=env,
                            startupinfo=startup, capture_output=True, timeout=90)
    logs = (user_data / 'application.log').read_text(encoding='utf-8') if (user_data / 'application.log').exists() else ''
    report = {'exit_code': result.returncode, 'elapsed_seconds': round(time.monotonic() - start, 2),
              'standalone_no_sibling_assets': not (folder / 'assets').exists(),
              'bundled_package_seeded': (user_data / 'assets/versions/v0.9.4/libxess_dx11.dll').is_file(),
              'application_log': logs, 'stderr': result.stderr.decode('utf-8', errors='replace')}
    if report['bundled_package_seeded']:
        report['seed_hash_matches'] = sha256(user_data / 'assets/versions/v0.9.4/OptiScaler.dll') == sha256(ROOT / 'assets/versions/v0.9.4/OptiScaler.dll')
        reference = ROOT / 'assets/versions/v0.9.4'
        seeded = user_data / 'assets/versions/v0.9.4'
        expected = {p.relative_to(reference).as_posix(): sha256(p) for p in reference.rglob('*') if p.is_file()}
        actual = {p.relative_to(seeded).as_posix(): sha256(p) for p in seeded.rglob('*') if p.is_file()}
        report['all_bundled_files_match'] = expected == actual
        report['verified_file_count'] = len(expected)
    (ROOT / 'audit/arcscaler-smoke-result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    assert result.returncode == 0 and report.get('all_bundled_files_match') and 'Unhandled error' not in logs, report
