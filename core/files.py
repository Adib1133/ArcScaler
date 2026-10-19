"""Validated paths, durable replacement, PE validation and operation locks."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import tempfile


def canonical(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def inside(path, root):
    try:
        return os.path.commonpath([canonical(path), canonical(root)]) == canonical(root)
    except ValueError:
        return False


def safe_path(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError(f'Invalid relative filename: {name!r}')
    if any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in name.split('/')):
        raise ValueError(f'Invalid relative filename: {name!r}')
    path = Path(root) / name
    if not inside(path, root):
        raise ValueError(f'Path escapes target: {path}')
    for parent in [path, *path.parents]:
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError(f'Linked path is not supported: {parent}')
        if canonical(parent) == canonical(root):
            break
    return path


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path, data=None, source=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.opti-write-', dir=path.parent)
    try:
        os.close(fd)
        if source is not None:
            shutil.copy2(source, temp)
        else:
            Path(temp).write_bytes(data)
        mode = os.stat(temp).st_mode
        os.chmod(temp, mode | stat.S_IWRITE)
        with open(temp, 'r+b') as stream:
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.chmod(temp, os.stat(temp).st_mode | stat.S_IWRITE)
            os.unlink(temp)


def write_json(path, data):
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8'))


class OperationBusy(RuntimeError):
    """An OS lock is held by another operation."""


@contextmanager
def operation_lock(directory):
    """OS-released lock. Persistent file prevents unlink/reopen lock races."""
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f'Directory does not exist: {root}')
    lock_root = Path(tempfile.gettempdir()) / 'ArcScaler-locks'
    lock_root.mkdir(exist_ok=True)
    path = safe_path(lock_root, hashlib.sha256(canonical(root).encode()).hexdigest() + '.lock')
    with open(path, 'a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise OperationBusy('Another operation is using this directory.') from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def validate_pe(path, dll=True):
    """Bounded AMD64 PE32+ structure check; never executes the file."""
    try:
        path = Path(path)
        size = path.stat().st_size
        with path.open('rb') as stream:
            dos = stream.read(64)
            if len(dos) != 64 or dos[:2] != b'MZ':
                raise ValueError('Missing DOS header')
            offset = struct.unpack_from('<I', dos, 60)[0]
            if offset < 64 or offset + 24 > size:
                raise ValueError('Invalid PE offset')
            stream.seek(offset)
            header = stream.read(24)
            machine, sections = struct.unpack_from('<HH', header, 4)
            optional_size, flags = struct.unpack_from('<HH', header, 20)
            if header[:4] != b'PE\0\0' or machine != 0x8664 or not 1 <= sections <= 96:
                raise ValueError('An AMD64 Windows PE is required')
            if bool(flags & 0x2000) != dll or not flags & 2:
                raise ValueError('Wrong executable/DLL type')
            if optional_size < 112 or offset + 24 + optional_size + sections * 40 > size:
                raise ValueError('Truncated headers')
            optional = stream.read(optional_size)
            if struct.unpack_from('<H', optional)[0] != 0x20b:
                raise ValueError('PE32+ required')
            for _ in range(sections):
                section = stream.read(40)
                raw_size, raw_offset = struct.unpack_from('<II', section, 16)
                if raw_size and (raw_offset == 0 or raw_offset + raw_size > size):
                    raise ValueError('Truncated section')
        return True
    except (OSError, ValueError, struct.error) as exc:
        raise ValueError(f'Invalid {"DLL" if dll else "game executable"} {path}: {exc}') from exc
