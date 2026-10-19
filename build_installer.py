"""Build the installer; optionally sign app, setup and uninstaller via Windows SDK."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile
from core.files import sha256

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iscc', default=str(ROOT / 'build/tools/inno/ISCC.exe'))
    parser.add_argument('--certificate', help='SHA-1 thumbprint in CurrentUser/My; never a private key')
    parser.add_argument('--signtool', default='signtool.exe')
    parser.add_argument('--timestamp-url', help='RFC3161 timestamp service URL, required for signing')
    args = parser.parse_args()
    exe = ROOT / 'dist/ArcScaler.exe'
    if not exe.is_file():
        parser.error('Run build.py first.')
    command = [args.iscc]
    if args.certificate:
        if not re.fullmatch('[A-Fa-f0-9]{40}', args.certificate):
            parser.error('Invalid certificate thumbprint.')
        if not args.timestamp_url or not re.fullmatch(r'https?://[A-Za-z0-9./:_-]+', args.timestamp_url):
            parser.error('A valid RFC3161 timestamp URL is required.')
        sign = [args.signtool, 'sign', '/sha1', args.certificate, '/fd', 'SHA256', '/td', 'SHA256', '/tr', args.timestamp_url]
        subprocess.run(sign + [str(exe)], check=True)
        subprocess.run([args.signtool, 'verify', '/pa', '/all', str(exe)], check=True)
        # Inno's $f is the compiler-controlled setup/uninstaller filename.
        command += ['/DSignBuild', '/Sarcscaler=' + subprocess.list2cmdline(sign).replace('$', '$$') + ' $f']
    with zipfile.ZipFile(ROOT / 'dist/ArcScaler-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        files = [p for folder in ('core', 'gui', 'tests') for p in (ROOT / folder).rglob('*.py')]
        files += [ROOT / name for name in ('main.py', 'build.py', 'build_installer.py', 'ArcScaler.iss', 'requirements.txt', 'requirements-build.txt', 'README.md', 'LICENSE.txt', 'THIRD_PARTY_NOTICES.txt')]
        files += [ROOT / 'assets' / name for name in ('arcscaler.ico', 'arcscaler-icon.png', 'compatibility.json', 'windows-version.txt')]
        for file in files:
            archive.write(file, file.relative_to(ROOT).as_posix())
    subprocess.run(command + [str(ROOT / 'ArcScaler.iss')], cwd=ROOT, check=True)
    setup = ROOT / 'dist/ArcScaler-Setup.exe'
    if args.certificate:
        subprocess.run([args.signtool, 'verify', '/pa', '/all', str(setup)], check=True)
        shutil.copy2(exe, ROOT / 'ArcScaler.exe')
    report = {'installer': str(setup), 'size': setup.stat().st_size, 'sha256': sha256(setup), 'signed': bool(args.certificate)}
    (ROOT / 'audit/installer-build-result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
