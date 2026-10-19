"""Conservative runtime evidence, independent of successful file deployment."""
import hashlib
import json
from pathlib import Path
import time
from core.files import safe_path
from core.safety import SafetyManager


def runtime_status(target_dir, additional_dirs=None, confirmation=None, health=None):
    result = {'state': 'unverified', 'message': 'Runtime not verified', 'signature': '', 'log': None}
    if not SafetyManager.has_active_backup(target_dir):
        return result
    try:
        from core.installation_state import installation_health
        health = health or installation_health(target_dir, additional_dirs)
        if health['state'] != 'installed':
            result['message'] = 'Runtime confirmation unavailable until installed files verify'
            return result
        manifest = SafetyManager.load_manifest(target_dir, additional_dirs)
        metadata = manifest.get('metadata', {})
        signature = hashlib.sha256(json.dumps({k: metadata.get(k) for k in
            ('deployed_files', 'reused_files', 'effective_settings', 'installed_at')}, sort_keys=True).encode()).hexdigest()
        result['signature'] = signature
        installed = metadata.get('installed_at', time.time())
        log = safe_path(target_dir, 'OptiScaler.log')
        if log.is_file() and installed <= log.stat().st_mtime <= time.time()+60:
            with log.open('rb') as stream:
                stream.seek(max(0, log.stat().st_size - 256*1024))
                text = stream.read(256*1024).decode('utf-8', errors='replace')
            if 'optiscaler' in text.lower():
                result.update(state='detected', message='OptiScaler mentioned in a log updated since installation; route unverified', log=str(log))
        if confirmation and confirmation.get('signature') == signature and confirmation.get('confirmed_at', 0) >= installed:
            result.update(state='confirmed', message='Installed route confirmed by you in the game overlay')
    except (OSError, ValueError, KeyError, TypeError):
        result['message'] = 'Runtime evidence unavailable; review installation health'
    return result
