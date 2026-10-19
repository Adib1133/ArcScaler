from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from core.detector import GameDetector
from core.injector import Injector
from core.library import GameLibrary
from tests.fixtures import game


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_unreal_shipping_over_engine_helper(self):
        game(self.root / 'Engine/Binaries/Win64', 'Engine-Win64-Shipping.exe')
        exe = game(self.root / 'MyGame/Binaries/Win64', 'MyGame-Win64-Shipping.exe')
        result = GameDetector.analyze_game(str(self.root))
        self.assertEqual(result['target_exe'], str(exe))
        self.assertEqual(result['all_target_dirs'], [str(exe.parent)])

    def test_redengine_and_uppercase(self):
        exe = game(self.root / 'bin/x64', 'GAME.EXE')
        self.assertEqual(GameDetector.analyze_game(str(self.root))['target_exe'], str(exe))

    def test_corrupt_executable_rejected(self):
        exe = self.root / 'game.exe'
        exe.write_bytes(b'fake')
        self.assertFalse(GameDetector.analyze_game(str(exe))['valid'])

    def test_process_identity_and_descendants(self):
        exe = game(self.root / 'game')
        unrelated = SimpleNamespace(info={'name': exe.name, 'exe': str(self.root / 'other' / exe.name)})
        child = SimpleNamespace(info={'name': 'child.exe', 'exe': str(exe.parent / 'bin/child.exe')})
        with patch('core.injector.psutil.process_iter', return_value=[unrelated]):
            self.assertFalse(Injector.process_in_directory(str(exe), str(exe.parent)))
        with patch('core.injector.psutil.process_iter', return_value=[child]):
            self.assertTrue(Injector.process_in_directory(str(exe), str(exe.parent)))

    def test_profile_roundtrip_and_rediscovery(self):
        exe = game(self.root / 'game')
        lib = GameLibrary(str(self.root / 'profiles.json'))
        profile = lib.add_game_by_path(str(exe))
        settings = {'custom_scale': 0.7, 'optiscaler_version': 'v0.9.4', 'fg_input': 'fsrfg30',
                    'invert_depth': True, 'jitter_cancellation': False, 'settings_schema': 3}
        lib.update_profile(profile['id'], settings)
        lib.add_game_by_path(str(exe))
        restored = GameLibrary(str(self.root / 'profiles.json')).get_all_profiles()[0]
        for key, value in settings.items():
            self.assertEqual(restored[key], value)

    def test_profile_corruption_preserved(self):
        path = self.root / 'profiles.json'
        path.write_text('{broken')
        with self.assertRaises(ValueError):
            GameLibrary(str(path))
        self.assertEqual(path.read_text(), '{broken')

    def test_shortcut_apostrophe_is_data(self):
        shortcut = self.root / "player's game.lnk"
        with patch('core.detector.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = str(self.root / 'game.exe')
            GameDetector.resolve_shortcut_if_needed(str(shortcut))
        args = run.call_args
        self.assertNotIn("player's", args.args[0][-1])
        self.assertEqual(args.kwargs['env']['OPTISCALER_SHORTCUT'], str(shortcut))

    def test_anticheat_detection(self):
        game(self.root / 'game')
        (self.root / 'game/start_protected_game.exe').write_bytes(b'indicator')
        self.assertEqual(GameDetector.analyze_game(str(self.root / 'game'))['anti_cheat'], 'Easy Anti-Cheat (EAC)')
