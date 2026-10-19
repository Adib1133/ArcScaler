"""Separate read-only bundled resources from writable per-user state."""
import os
from pathlib import Path
import shutil
import sys
from core.files import operation_lock


def resource_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


def data_root():
    override = os.environ.get('ARCSCALER_DATA_DIR') or os.environ.get('OPTISCALER_GUI_DATA_DIR')
    root = Path(override) if override else Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'ArcScaler'
    root.mkdir(parents=True, exist_ok=True)
    if not override:
        migrate_legacy(root)
    return root


def migrate_legacy(root):
    """Copy legacy state once; never remove originals or overwrite new settings."""
    from core.files import atomic_write, write_json
    import json
    marker = root / '.migration-complete.json'
    if marker.exists():
        return
    with operation_lock(root):
        if marker.exists():
            return
        sources = list(dict.fromkeys(Path(os.environ[k]) / 'OptiScalerXeSS'
                                    for k in ('LOCALAPPDATA', 'APPDATA') if os.environ.get(k)))
        for index, source in enumerate(sources):
            if not source.is_dir():
                continue
            for path in source.rglob('*'):
                if not path.is_file() or path.is_symlink() or path.name.endswith('.lock'):
                    continue
                relative = path.relative_to(source)
                destination = root / relative
                if not destination.exists():
                    atomic_write(destination, source=path)
                elif destination.read_bytes() != path.read_bytes():
                    atomic_write(root / 'migration-originals' / str(index) / relative, source=path)
                    if relative.as_posix() == 'profiles.json':
                        current = json.loads(destination.read_text(encoding='utf-8'))
                        previous = json.loads(path.read_text(encoding='utf-8'))
                        write_json(destination, previous | current)
        write_json(marker, {'sources': [str(s) for s in sources]})


def prepare_assets():
    root = data_root() / 'assets'
    versions = root / 'versions'
    versions.mkdir(parents=True, exist_ok=True)
    bundled = resource_root() / 'assets/versions'
    with operation_lock(versions):
        compact = resource_root() / 'assets/offline-runtime.zip'
        if compact.is_file() and not (versions / 'v0.9.4').exists():
            from core.bundle import unpack
            from core.version_manager import VersionManager
            stage = versions / '.seed-v0.9.4'
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir()
            try:
                unpack(compact, stage / 'v0.9.4')
                VersionManager(stage).resolve_package('v0.9.4')
                os.replace(stage / 'v0.9.4', versions / 'v0.9.4')
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        if bundled.is_dir() and bundled.resolve() != versions.resolve():
            from core.version_manager import VersionManager
            source_vm = VersionManager(bundled)
            for tag in source_vm.get_installed_versions():
                target = versions / tag
                if not target.exists():
                    stage = versions / ('.seed-' + tag)
                    if stage.exists():
                        # Resume a interrupted seed by validating it; discard only our staging dir.
                        shutil.rmtree(stage)
                    shutil.copytree(bundled / tag, stage)
                    os.replace(stage, target)
    return root
