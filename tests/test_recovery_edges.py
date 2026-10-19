import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core.files import sha256, write_json
from core.safety import SafetyManager
from core.injector import Injector
from tests.fixtures import package, game


class RecoveryEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        package(self.root / 'assets')
        self.exe = game(self.root / 'game')
        self.folder = self.exe.parent
        self.injector = Injector(self.root / 'assets')

    def test_legacy_snapshot_can_restore_but_cannot_overwrite_origins(self):
        backup = self.folder / '.optiscaler_backup'
        backup.mkdir()
        original = backup / 'dxgi.dll.orig'
        original.write_bytes(b'vanilla')
        (self.folder / 'dxgi.dll').write_bytes(b'old proxy')
        manifest = {'primary_dir': str(self.folder), 'folders': {str(self.folder): {
            'created_files': [], 'overwritten_files': [{'filename': 'dxgi.dll', 'backup_path': str(original), 'original_hash': sha256(original)}]}}}
        write_json(backup / 'manifest.json', manifest)
        result = self.injector.apply_injection(str(self.folder), str(self.exe), upscaler_enabled=True, installation_mode='manual')
        self.assertFalse(result['success'])
        self.assertIn('legacy', result['error'])
        self.assertEqual(original.read_bytes(), b'vanilla')
        self.assertFalse(self.injector.revert_injection(str(self.folder))['success'])
        self.assertTrue(self.injector.revert_injection(str(self.folder),preserve_changes=True)['success'])
        self.assertEqual((self.folder / 'dxgi.dll').read_bytes(), b'vanilla')

    def test_manifest_cannot_delete_game_executable(self):
        SafetyManager.create_pre_injection_snapshot(self.folder, ['dxgi.dll'], {})
        manifest_path = self.folder / '.optiscaler_backup/manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['folders'][str(self.folder)]['created_files'].append(self.exe.name)
        write_json(manifest_path, manifest)
        result = self.injector.revert_injection(str(self.folder))
        self.assertFalse(result['success'])
        self.assertTrue(self.exe.is_file())

    def test_removed_target_directory_retains_recovery_manifest(self):
        plugin = self.folder / 'plugin'
        plugin.mkdir()
        self.assertTrue(self.injector.apply_injection(str(self.folder), str(self.exe), upscaler_enabled=True, installation_mode='manual', additional_dirs=[str(plugin)])['success'])
        missing = self.folder / 'renamed-plugin'
        plugin.rename(missing)
        self.assertFalse(self.injector.revert_injection(str(self.folder), additional_dirs=[str(plugin)])['success'])
        self.assertTrue(SafetyManager.has_active_backup(self.folder))
        missing.rename(plugin)
        self.assertTrue(self.injector.revert_injection(str(self.folder), additional_dirs=[str(plugin)])['success'])

    def test_failed_second_snapshot_does_not_rollback_previous_install(self):
        self.assertTrue(self.injector.apply_injection(str(self.folder), str(self.exe), upscaler_enabled=True, installation_mode='manual')['success'])
        before = (self.folder / 'dxgi.dll').read_bytes()
        with patch('core.safety.write_json', side_effect=OSError('disk full')):
            result = self.injector.apply_injection(str(self.folder), str(self.exe), upscaler_enabled=True, installation_mode='manual', hook_method='winmm.dll')
        self.assertFalse(result['success'])
        self.assertEqual((self.folder / 'dxgi.dll').read_bytes(), before)
        self.assertTrue(SafetyManager.has_active_backup(self.folder))
        self.assertTrue(self.injector.revert_injection(str(self.folder))['success'])
