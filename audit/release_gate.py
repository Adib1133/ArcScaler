"""Run regressions, build, then smoke-test the actual executable in isolation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def run():
    env=dict(os.environ)
    if (ROOT/'.audit-deps').exists():env['PYTHONPATH']=str(ROOT/'.audit-deps')+os.pathsep+env.get('PYTHONPATH','')
    for script in ('audit/run_checks.py','build.py'):
        subprocess.run([sys.executable,'-X','utf8',str(ROOT/script)],cwd=ROOT,env=env,check=True)
    with tempfile.TemporaryDirectory(prefix='arcscaler-smoke-') as temp:
        env['ARCSCALER_DATA_DIR']=temp
        result=subprocess.run([str(ROOT/'dist/ArcScaler.exe'),'--smoke-test'],cwd=ROOT,env=env,timeout=120,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        report=dict(executable=str(ROOT/'dist/ArcScaler.exe'),exit_code=result.returncode,
                    log=(Path(temp)/'application.log').read_text(encoding='utf-8') if (Path(temp)/'application.log').exists() else '')
        (ROOT/'audit/packaged-smoke-result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        if result.returncode:raise SystemExit(result.returncode)


if __name__=='__main__':run()
