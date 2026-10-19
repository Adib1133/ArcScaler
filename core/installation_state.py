"""Read-only installation health, including interrupted transactions."""
from pathlib import Path
from pathlib import Path
from core.files import safe_path, sha256
from core.safety import SafetyManager


def installation_health(target_dir, additional_dirs=None):
    if not SafetyManager.has_active_backup(target_dir):
        return dict(state='not_installed', message='Not installed', files=[])
    try:
        manifest = SafetyManager.load_manifest(target_dir, additional_dirs)
        operation = manifest.get('operation', {})
        if operation.get('state') not in (None, 'complete'):
            return dict(state='recovery_required', message='Interrupted operation: '+operation['state'], files=[], can_undo=operation['state']=='undoing')
        hashes = SafetyManager.deployed_hashes(manifest)
        if not hashes or not any(hashes.values()):
            return dict(state='unverified', message='Legacy installation needs verification or reinstallation.', files=[])
        files = []
        for filename, expected in manifest.get('metadata', {}).get('reused_files', {}).items():
            path = Path(filename)
            state = 'missing' if not path.is_file() else 'modified' if sha256(path) != expected else 'verified'
            files.append(dict(path=str(path), state=state, origin='Reused native runtime'))
        for folder, entries in hashes.items():
            for name, expected in entries.items():
                path = safe_path(folder, name)
                state = 'missing' if not path.is_file() else 'modified' if sha256(path) != expected else 'verified'
                files.append(dict(path=str(path), state=state))
        state = 'incomplete' if any(f['state']=='missing' for f in files) else 'modified' if any(f['state']=='modified' for f in files) else 'installed'
        return dict(state=state, message={'installed':'Installed files verified', 'incomplete':'Installed files are missing', 'modified':'Installed files have changed'}[state], files=files,
                    can_undo=bool(manifest.get('undo')),
                    settings=manifest.get('metadata',{}).get('effective_settings',{}),
                    requested_settings=manifest.get('metadata',{}).get('requested_settings',{}))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return dict(state='recovery_required', message='Backup needs attention: '+str(exc), files=[])
