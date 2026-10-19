"""Build ArcScaler's standalone Windows executable from portable project paths."""
import json
from pathlib import Path
import shutil
import subprocess
import sys


def build():
    root=Path(__file__).resolve().parent
    import customtkinter
    import PyInstaller
    from core.version_manager import VersionManager
    from core.files import sha256
    VersionManager(root/'assets/versions').resolve_package('v0.9.4')
    from core.bundle import pack
    bundle=root/'build/offline-runtime.zip'
    bundle.parent.mkdir(exist_ok=True)
    pack(root/'assets/versions/v0.9.4',bundle)
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
             '--specpath',str(root/'build'),
             '--name','ArcScaler','--icon',str(root/'assets/arcscaler.ico'),
             '--version-file',str(root/'assets/windows-version.txt'),
             '--add-data',f'{Path(customtkinter.__file__).parent/"assets"};customtkinter/assets',
             '--add-data',f'{root/"assets/arcscaler.ico"};assets',
             '--add-data',f'{root/"assets/arcscaler-icon.png"};assets',
             '--add-data',f'{root/"assets/compatibility.json"};assets',
             '--add-data',f'{bundle};assets',
             '--exclude-module','numpy','--exclude-module','py7zr',
             '--exclude-module','PIL.AvifImagePlugin','--exclude-module','PIL._avif',
             '--hidden-import','psutil','--hidden-import','PIL','--hidden-import','tkinter',str(root/'main.py')]
    (root/'audit').mkdir(exist_ok=True)
    with (root/'audit/arcscaler-build-log.txt').open('w',encoding='utf-8') as log:
        subprocess.run(command,cwd=root,check=True,stdout=log,stderr=subprocess.STDOUT)
    output=root/'dist/ArcScaler.exe';shutil.copy2(output,root/'ArcScaler.exe')
    metadata={'python':sys.version,'pyinstaller':PyInstaller.__version__,'executable':str(output),
              'sha256':sha256(output),'size':output.stat().st_size}
    (root/'audit/arcscaler-build-result.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print(json.dumps(metadata,indent=2));return True

if __name__=='__main__':build()
