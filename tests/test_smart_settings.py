from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core.config_generator import ConfigGenerator
from core.detector import GameDetector
from core.game_support import capabilities
from core.injector import Injector
from core.library import GameLibrary
from tests.fixtures import package, game, pe_bytes


class SmartSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.release = package(self.root / 'assets')
        self.exe = game(self.root / 'game')
        self.injector = Injector(self.root / 'assets')

    def apply(self, **settings):
        settings.setdefault('installation_mode', 'manual')
        return self.injector.apply_injection(str(self.exe.parent), str(self.exe), **settings)

    def test_default_requires_explicit_selection(self):
        self.assertFalse(self.apply()['success'])
        self.assertFalse((self.exe.parent / 'OptiScaler.ini').exists())
        profile = GameLibrary(str(self.root / 'profiles.json')).add_game_by_path(str(self.exe))
        self.assertFalse(profile['upscaler_enabled'])
        self.assertFalse(profile['frame_gen_enabled'])
        self.assertEqual(profile['xess_quality'], 'User Defined')

    def test_fg_only_preserves_native_upscaler_and_disables_interception(self):
        native = self.exe.parent / 'libxess.dll'
        native.write_bytes(pe_bytes(marker=b'native'))
        result = self.apply(upscaler_enabled=False, frame_gen_enabled=True, fg_input='dlssg')
        self.assertTrue(result['success'], result)
        self.assertNotIn('libxess.dll', result['injected_suite'])
        self.assertIn('fakenvapi.dll', result['injected_suite'])
        cfg = ConfigGenerator.parser((self.exe.parent / 'OptiScaler.ini').read_text())
        self.assertEqual(cfg['Inputs']['EnableXeSSInputs'], 'false')
        self.assertEqual(cfg['Inputs']['EnableDlssInputs'], 'false')
        self.assertEqual(cfg['Spoofing']['Dxgi'], 'false')
        self.assertEqual(cfg['FrameGen']['FGOutput'], 'xefg')
        self.assertTrue(self.injector.revert_injection(str(self.exe.parent))['success'])
        self.assertEqual(native.read_bytes(), pe_bytes(marker=b'native'))

    def test_upscaler_only_and_update_to_fg_only(self):
        first = self.apply(upscaler_enabled=True)
        self.assertTrue(first['success'], first)
        self.assertNotIn('libxess_fg.dll', first['injected_suite'])
        self.assertFalse(capabilities(GameDetector.analyze_game(str(self.exe)))['xess'])
        second = self.apply(frame_gen_enabled=True, fg_input='fsrfg')
        self.assertTrue(second['success'], second)
        self.assertFalse((self.exe.parent / 'libxess.dll').exists())
        self.assertTrue((self.exe.parent / 'libxess_fg.dll').exists())

    def test_user_defined_disables_all_resolution_overrides(self):
        cfg = ConfigGenerator.parser(ConfigGenerator.generate_nvngx_ini(
            upscaler_enabled=True, custom_scale=None))
        for section, key in [('UpscaleRatio', 'UpscaleRatioOverrideEnabled'),
                             ('QualityOverrides', 'QualityRatioOverrideEnabled'),
                             ('DRS', 'DrsMinOverrideEnabled'), ('DRS', 'DrsMaxOverrideEnabled')]:
            self.assertEqual(cfg[section][key], 'false')

    def test_both_native_no_modification(self):
        for name in ['libxess.dll', 'libxess_fg.dll']:
            (self.exe.parent / name).write_bytes(pe_bytes())
        self.assertFalse(self.apply(upscaler_enabled=False, frame_gen_enabled=True)['success'])
        self.assertFalse((self.exe.parent / 'OptiScaler.ini').exists())

    def test_xell_is_not_evidence_of_xess_or_fg(self):
        (self.exe.parent / 'libxell.dll').write_bytes(pe_bytes())
        caps = capabilities(GameDetector.analyze_game(str(self.exe)))
        self.assertFalse(caps['xess'])
        self.assertFalse(caps['xefg'])

    def test_fg_only_does_not_require_upscaler_package_files(self):
        (self.release / 'libxess.dll').unlink()
        (self.release / 'libxess_dx11.dll').unlink()
        self.assertTrue(self.apply(optiscaler_version='v0.9.4', frame_gen_enabled=True)['success'])


    def test_upscaler_driven_fg_requires_selected_upscaler(self):
        result = self.apply(frame_gen_enabled=True, fg_input='upscaler')
        self.assertFalse(result['success'])
        self.assertFalse((self.exe.parent / 'OptiScaler.ini').exists())

    def test_native_detection_uses_selected_installation_root(self):
        exe = game(self.root / 'whole/game/bin', 'game.exe')
        native = self.root / 'whole/plugins/libxess.dll'
        native.parent.mkdir()
        native.write_bytes(pe_bytes())
        result = self.injector.apply_injection(str(exe.parent), str(exe),
            base_dir=str(self.root / 'whole'), installation_mode='manual', upscaler_enabled=False, frame_gen_enabled=True)
        self.assertTrue(result['success'], result)
        self.assertNotIn('libxess.dll', result['injected_suite'])

