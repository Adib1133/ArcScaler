import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.config_generator import ConfigGenerator
from core.detector import GameDetector
from core.game_rules import match_recipe
from core.install_plan import build_plan
from core.injector import Injector
from core.safety import SafetyManager
from tests.fixtures import game, package, pe_bytes


class GameRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.version = package(self.root / 'assets')
        self.injector = Injector(self.root / 'assets')

    def spider(self):
        return game(self.root / 'Spider-Man 2', 'Spider-Man2.exe')

    def test_spider_aliases_and_sequels_do_not_collide(self):
        for title in ['Spider-Man2', 'SPIDER MAN 2', "Marvel’s Spider‐Man 2"]:
            self.assertEqual(match_recipe([title])['id'], 'spider-man-2')
        self.assertEqual(match_recipe(['Spider-Man'])['id'], 'spider-man-remastered')
        self.assertIsNone(match_recipe(['Spider-Man 20']))
        with self.assertRaises(ValueError):
            match_recipe(['Spider-Man2', 'MilesMorales'])

    def test_spider_corrects_saved_spoofing_and_uses_exact_preview_files(self):
        exe = self.spider()
        original = exe.parent / 'libxess.dll'
        original.write_bytes(pe_bytes(marker=b'game xess'))
        settings = dict(upscaler_enabled=False, frame_gen_enabled=True, gpu_spoofing=True,
                        fg_input='dlssg', starting_upscaler='DLSS', hook_method='version.dll')
        preview = build_plan(GameDetector.analyze_game(str(exe)), settings, self.injector.version_manager)
        result = self.injector.apply_injection(str(exe.parent), str(exe), **settings)
        self.assertTrue(result['success'], result)
        self.assertEqual(set(preview['files']), set(result['injected_suite']))
        self.assertEqual(result['hook_method'], 'dxgi.dll')
        cfg = ConfigGenerator.parser((exe.parent / 'OptiScaler.ini').read_text())
        self.assertEqual(cfg['Spoofing']['Dxgi'], 'false')
        self.assertEqual(cfg['NvApi']['OverrideNvapiDll'], 'true')
        self.assertEqual(cfg['FrameGen']['FGInput'], 'fsrfg')
        self.assertEqual(cfg['Inputs']['EnableXeSSInputs'], 'false')
        self.assertEqual(cfg['Inputs']['EnableFfxInputs'], 'true')
        self.assertEqual(cfg['Inputs']['UseFfxInputs'], 'false')
        fake = ConfigGenerator.parser((exe.parent / 'fakenvapi.ini').read_text())
        self.assertEqual(fake['fakenvapi']['force_reflex'], '0')
        self.assertNotIn('libxess.dll', result['injected_suite'])
        self.assertIn('fakenvapi.dll', result['injected_suite'])
        manifest = SafetyManager.load_manifest(exe.parent)
        self.assertEqual(manifest['metadata']['recipe_id'], 'spider-man-2')
        self.assertTrue(self.injector.revert_injection(str(exe.parent))['success'])
        self.assertEqual(original.read_bytes(), pe_bytes(marker=b'game xess'))

    def test_spider_manual_cannot_restore_known_crash_setting(self):
        exe = self.spider()
        result = self.injector.apply_injection(str(exe.parent), str(exe), frame_gen_enabled=True,
                                               gpu_spoofing=True, installation_mode='manual', fg_input='dlssg')
        self.assertTrue(result['success'], result)
        self.assertFalse(result['plan']['settings']['gpu_spoofing'])

    def test_unknown_requires_manual_review_before_mutation(self):
        exe = game(self.root / 'unlisted')
        result = self.injector.apply_injection(str(exe.parent), str(exe), upscaler_enabled=True)
        self.assertFalse(result['success'])
        self.assertIn('No supported upscaler input', result['error'])
        self.assertFalse(SafetyManager.has_active_backup(exe.parent))
        self.assertTrue(self.injector.apply_injection(str(exe.parent), str(exe), upscaler_enabled=True,
                                                     installation_mode='manual')['success'])

    def test_existing_unmanaged_proxy_is_preserved_with_another_hook(self):
        exe = self.spider()
        proxy = exe.parent / 'version.dll'
        proxy.write_bytes(b'other mod')
        result = self.injector.apply_injection(str(exe.parent), str(exe), frame_gen_enabled=True)
        self.assertTrue(result['success'],result)
        self.assertNotEqual(result['hook_method'],'version.dll')
        self.assertEqual(proxy.read_bytes(), b'other mod')
        self.assertTrue(SafetyManager.has_active_backup(exe.parent))

    def test_automatic_update_recognizes_its_own_dependencies(self):
        exe = self.spider()
        for _ in range(2):
            result = self.injector.apply_injection(str(exe.parent), str(exe), frame_gen_enabled=True)
            self.assertTrue(result['success'], result)
            self.assertIn('libxess_fg.dll', result['injected_suite'])

    def test_missing_fg_fakenvapi_blocks_before_snapshot(self):
        exe = self.spider()
        (self.version / 'fakenvapi.dll').unlink()
        result = self.injector.apply_injection(str(exe.parent), str(exe), optiscaler_version='v0.9.4', frame_gen_enabled=True)
        self.assertFalse(result['success'])
        self.assertIn('fakenvapi.dll', result['error'])
        self.assertFalse(SafetyManager.has_active_backup(exe.parent))

    def test_midnight_suns_chooses_actual_location_and_d3d12(self):
        root = self.root / 'Midnight Suns'
        game(root, 'Launcher.exe')
        exe = game(root / 'MidnightSuns/Binaries/Win64', 'MidnightSuns-Win64-Shipping.exe')
        detected = GameDetector.analyze_game(str(root))
        self.assertEqual(detected['target_exe'], str(exe))
        result = self.injector.apply_injection(str(exe.parent), str(exe), base_dir=str(root), upscaler_enabled=True)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['hook_method'], 'd3d12.dll')
        self.assertFalse((root / 'd3d12.dll').exists())
        bad = self.injector.apply_injection(str(exe.parent), str(exe), base_dir=str(root), frame_gen_enabled=True)
        self.assertFalse(bad['success'])
        self.assertIn('No reviewed XeSS FG', bad['error'])

    def test_dcs_prefers_bin_not_larger_bin_mt_exe(self):
        root = self.root / 'DCS World'
        correct = game(root / 'bin', 'DCS.exe')
        wrong = game(root / 'bin-mt', 'DCS.exe')
        wrong.write_bytes(wrong.read_bytes() + b'x' * 5000)
        self.assertEqual(GameDetector.analyze_game(str(root))['target_exe'], str(correct))
        plan = build_plan(GameDetector.analyze_game(str(wrong)), {'upscaler_enabled': True})
        self.assertTrue(any('documented injection location' in e for e in plan['errors']))


