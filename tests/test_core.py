import configparser
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core.injector import Injector
from core.safety import SafetyManager
from core.config_generator import ConfigGenerator
from core.files import operation_lock, validate_pe, safe_path, atomic_write
from tests.fixtures import package, game, pe_bytes, ROOT


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.version = package(self.root / 'assets')
        self.exe = game(self.root / 'game')
        self.folder = self.exe.parent
        self.injector = Injector(self.root / 'assets')

    def apply(self, **kw):
        kw.setdefault('upscaler_enabled', not kw.get('frame_gen_enabled',False))
        kw.setdefault('installation_mode', 'manual')
        return self.injector.apply_injection(str(self.folder), str(self.exe), **kw)

    def test_all_eight_hooks_and_fake_nvapi_restore(self):
        for hook in Injector.DEFAULT_HOOKS:
            with self.subTest(hook=hook):
                (self.folder / hook).write_bytes(b'original mod')
                (self.folder / 'fakenvapi.ini').write_bytes(b'user settings')
                result = self.apply(hook_method=hook, frame_gen_enabled=True, fg_input='dlssg')
                self.assertTrue(result['success'], result)
                self.assertEqual((self.folder / hook).read_bytes(), (self.version / 'OptiScaler.dll').read_bytes())
                self.assertFalse((self.folder / 'libxess_dx11.dll').is_file())
                if hook != 'nvngx.dll':
                    self.assertFalse((self.folder / 'nvngx.dll').exists())
                result = self.injector.revert_injection(str(self.folder))
                self.assertTrue(result['success'], result)
                self.assertEqual((self.folder / hook).read_bytes(), b'original mod')
                self.assertEqual((self.folder / 'fakenvapi.ini').read_bytes(), b'user settings')
                (self.folder / hook).unlink()

    def test_repeat_and_hook_switch_preserve_original(self):
        (self.folder / 'dxgi.dll').write_bytes(b'original')
        for hook in ['dxgi.dll', 'dxgi.dll', 'winmm.dll']:
            self.assertTrue(self.apply(hook_method=hook)['success'])
        self.assertEqual((self.folder / 'dxgi.dll').read_bytes(), b'original')
        self.assertTrue(self.injector.revert_injection(str(self.folder))['success'])
        self.assertEqual((self.folder / 'dxgi.dll').read_bytes(), b'original')
        self.assertFalse((self.folder / 'winmm.dll').exists())

    def test_partial_backup_failure_never_mutates_game(self):
        (self.folder / 'dxgi.dll').write_bytes(b'original')
        with patch('core.files.shutil.copy2', side_effect=OSError('disk full')):
            self.assertFalse(self.apply()['success'])
        self.assertEqual((self.folder / 'dxgi.dll').read_bytes(), b'original')

    def test_restore_failure_retry(self):
        (self.folder / 'dxgi.dll').write_bytes(b'original')
        self.assertTrue(self.apply()['success'])
        with patch('core.files.shutil.copy2', side_effect=PermissionError('locked')), patch('core.safety.time.sleep'):
            self.assertFalse(self.injector.revert_injection(str(self.folder))['success'])
        self.assertTrue(SafetyManager.has_active_backup(self.folder))
        self.assertTrue(self.injector.revert_injection(str(self.folder))['success'])
        self.assertEqual((self.folder / 'dxgi.dll').read_bytes(), b'original')

    def test_mid_deploy_failure_restores_original(self):
        (self.folder / 'dxgi.dll').write_bytes(b'original')
        from core.files import atomic_write
        def fail(path, *args, **kw):
            if Path(path).name == 'libxess.dll':
                raise OSError('simulated failure')
            return atomic_write(path, *args, **kw)
        with patch('core.injector.atomic_write', side_effect=fail):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['recovery']['success'], result)
        self.assertEqual((self.folder / 'dxgi.dll').read_bytes(), b'original')

    def test_no_manifest_no_deletion(self):
        (self.folder / 'libxess.dll').write_bytes(b'native runtime')
        self.assertFalse(self.injector.revert_injection(str(self.folder))['success'])
        self.assertEqual((self.folder / 'libxess.dll').read_bytes(), b'native runtime')

    def test_unmanaged_overlay_survives(self):
        (self.folder / 'imgui.ini').write_bytes(b'overlay')
        self.assertTrue(self.apply()['success'])
        self.assertTrue(self.injector.revert_injection(str(self.folder))['success'])
        self.assertEqual((self.folder / 'imgui.ini').read_bytes(), b'overlay')

    def test_multi_dir_rollback(self):
        other = self.folder / 'plugins'
        other.mkdir()
        (other / 'fakenvapi.dll').write_bytes(b'native')
        result = self.apply(additional_dirs=[str(other)])
        self.assertTrue(result['success'], result)
        self.assertTrue((other / 'OptiScaler.ini').exists())
        self.assertTrue(self.injector.revert_injection(str(self.folder), additional_dirs=[str(other)])['success'])
        self.assertEqual((other / 'fakenvapi.dll').read_bytes(), b'native')

    def test_unknown_hook_and_missing_executable_rejected(self):
        for hook in ['bad.dll', '../game.exe', 'DXGI.dll', 'C:/test.dll']:
            self.assertFalse(self.apply(hook_method=hook)['success'])
        self.exe.unlink()
        self.assertFalse(self.apply()['success'])

    def test_corrupt_and_missing_runtimes_rejected(self):
        (self.version / 'libxess.dll').write_bytes(b'x' * 200000)
        self.assertFalse(self.apply()['success'])
        self.assertFalse(SafetyManager.has_active_backup(self.folder))

    def test_explicit_version_never_falls_back(self):
        self.assertFalse(self.apply(optiscaler_version='v-missing')['success'])

    def test_legacy_proxy_name(self):
        (self.version / 'OptiScaler.dll').rename(self.version / 'dxgi.dll')
        self.assertTrue(self.apply()['success'])
        self.assertEqual((self.folder / 'dxgi.dll').read_bytes(), (self.version / 'dxgi.dll').read_bytes())

    def test_corrupt_backup_retained(self):
        (self.folder / 'dxgi.dll').write_bytes(b'original')
        self.assertTrue(self.apply()['success'])
        manifest = SafetyManager.load_manifest(self.folder)
        original = manifest['folders'][str(self.folder)]['overwritten_files'][0]['backup_path']
        Path(original).write_bytes(b'corrupt')
        self.assertFalse(self.injector.revert_injection(str(self.folder))['success'])
        self.assertTrue(SafetyManager.has_active_backup(self.folder))

    def test_concurrent_operation_rejected(self):
        with operation_lock(self.folder):
            self.assertFalse(self.apply()['success'])

    def test_pe_validation_and_path_containment(self):
        self.assertTrue(validate_pe(self.exe, False))
        with self.assertRaises(ValueError):
            validate_pe(self.exe, True)
        for name in ['../escape', '/absolute', 'C:/escape', 'a\\b', 'a/../b']:
            with self.assertRaises(ValueError):
                safe_path(self.folder, name)

    def test_config_contract_and_resolution_reciprocal(self):
        official = ConfigGenerator.parser((self.version / 'OptiScaler.ini').read_text(encoding='utf-8-sig'))
        for source in ('DLSS', 'FSR'):
            for fg in ConfigGenerator.FG_INPUTS:
                generated = ConfigGenerator.generate_nvngx_ini(starting_upscaler=source, fg_input=fg,
                    frame_gen_enabled=True, custom_scale=0.5, upscaler_enabled=False, xess_quality='Quality')
                cfg = ConfigGenerator.parser(generated)
                self.assertEqual(cfg['FrameGen']['FGInput'], fg)
                self.assertEqual(cfg['FrameGen']['FGOutput'], 'xefg')
                self.assertEqual(float(cfg['UpscaleRatio']['UpscaleRatioOverrideValue']), 2)
                for section in cfg.sections():
                    for key in cfg[section]:
                        self.assertTrue(official.has_option(section, key), f'{section}.{key}')

    def test_config_invalid_inputs(self):
        for args in [{'sharpness': float('nan')}, {'custom_scale': 0}, {'reflex_boost': True},
                     {'frame_gen_enabled': True, 'intercept_dlssg': True, 'intercept_fsr3': True},
                     {'frame_gen_enabled': True, 'overlay_menu': False}]:
            with self.assertRaises(ValueError):
                ConfigGenerator.generate_nvngx_ini(**args)

    def test_disable_fg_removes_managed_fg_runtime_on_update(self):
        self.assertTrue(self.apply(frame_gen_enabled=True, fg_input='dlssg')['success'])
        self.assertTrue((self.folder / 'libxess_fg.dll').exists())
        self.assertTrue(self.apply(frame_gen_enabled=False)['success'])
        self.assertFalse((self.folder / 'libxess_fg.dll').exists())

    def test_schema_mismatch_fails_before_backup(self):
        (self.version / 'OptiScaler.ini').write_text('[Upscalers]\nDx11Upscaler=auto\n')
        self.assertFalse(self.apply()['success'])
        self.assertFalse(SafetyManager.has_active_backup(self.folder))

    def test_read_only_original_can_be_snapshotted(self):
        import stat
        original = self.folder / 'dxgi.dll'
        original.write_bytes(b'original readonly')
        original.chmod(stat.S_IREAD)
        try:
            snapshot = SafetyManager.create_pre_injection_snapshot(self.folder, ['dxgi.dll'], {})
            item = snapshot['folders'][str(self.folder)]['overwritten_files'][0]
            self.assertEqual(Path(item['backup_path']).read_bytes(), b'original readonly')
        finally:
            original.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_optiscaler_ini_preserves_original_comments_and_full_size(self):
        res = self.apply(frame_gen_enabled=True, fg_input='dlssg')
        self.assertTrue(res['success'])
        installed_ini = self.folder / 'OptiScaler.ini'
        self.assertTrue(installed_ini.exists())
        content = installed_ini.read_text(encoding='utf-8-sig')
        # Check that comments and full documentation are preserved (>40KB, not 7KB)
        self.assertGreater(len(content.encode('utf-8')), 40000)
        self.assertIn('; Select Upscaler for Dx12 games', content)
        self.assertIn('; -------------------------------------------------------', content)

