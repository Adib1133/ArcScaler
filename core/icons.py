"""Disk-cached Windows executable icons, resolved entirely off the UI thread."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import subprocess
import ctypes
import uuid
from PIL import Image, ImageDraw, ImageFont
from core.detector import GameDetector

def placeholder(name, size=64):
    digest = hashlib.sha256(name.encode()).digest()
    color = tuple(45 + c % 100 for c in digest[:3])
    image = Image.new('RGBA', (size, size))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0,0,size-1,size-1), radius=size//4, fill=color)
    letters = ''.join(w[0] for w in name.split()[:2]).upper() or '?'
    try:
        font = ImageFont.truetype(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/segoeuib.ttf'),size//3)
    except OSError:
        font = ImageFont.load_default(size=size//3)
    draw.text((size/2,size/2), letters, fill='white', font=font, anchor='mm')
    return image

class IconCache:
    def __init__(self, directory, dispatcher):
        self.directory, self.dispatcher = Path(directory), dispatcher
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='game-icons')
        self.closed = False
        self.callbacks = {}
        self.serial = 0

    def resolve(self, executable, name):
        path = Path(GameDetector.resolve_shortcut_if_needed(executable))
        stamp = str(path.resolve()) + str(path.stat().st_mtime_ns if path.exists() else 0) + name
        key = hashlib.sha256(stamp.encode()).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / (key+'.png')
        if target.exists():
            try:
                with Image.open(target) as im: return im.convert('RGBA')
            except OSError:
                target.unlink(missing_ok=True)
        temporary = target.with_name(key+'.'+uuid.uuid4().hex+'.tmp.png')
        has_icon = False
        if os.name == 'nt' and path.is_file():
            extract = ctypes.windll.shell32.ExtractIconExW
            extract.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
            extract.restype = ctypes.c_uint
            has_icon = 0 < extract(str(path), -1, None, None, 0) < 10000
        if has_icon:
            env = dict(os.environ, ARCSCALER_ICON_EXE=str(path), ARCSCALER_ICON_OUT=str(temporary))
            script = ('Add-Type -AssemblyName System.Drawing; '
                      '$icon=[System.Drawing.Icon]::ExtractAssociatedIcon($env:ARCSCALER_ICON_EXE); '
                      'if ($null -eq $icon) { exit 1 }; '
                      '$bmp=$icon.ToBitmap(); '
                      'try {$bmp.Save($env:ARCSCALER_ICON_OUT,[System.Drawing.Imaging.ImageFormat]::Png)} '
                      'finally {$bmp.Dispose();$icon.Dispose()}')
            try:
                subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                               env=env, capture_output=True, timeout=12,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
                with Image.open(temporary) as im: result=im.convert('RGBA')
            except (OSError, subprocess.SubprocessError):
                result=placeholder(name)
        else:
            result=placeholder(name)
        result.save(temporary, format='PNG')
        os.replace(temporary,target)
        return result

    def request(self, executable, name, callback):
        if self.closed:
            return
        self.serial += 1
        token = self.serial
        self.callbacks[token] = callback
        def worker():
            try: image=self.resolve(executable,name)
            except (OSError,ValueError): image=placeholder(name)
            dispatcher = self.dispatcher
            if not self.closed and dispatcher:
                dispatcher.post(self._deliver,token,image)
        self.pool.submit(worker)

    def _deliver(self, token, image):
        callback = self.callbacks.pop(token,None)
        if callback and not self.closed:
            callback(image)

    def close(self):
        self.closed=True
        self.callbacks.clear()
        self.dispatcher=None
        self.pool.shutdown(wait=False, cancel_futures=True)
