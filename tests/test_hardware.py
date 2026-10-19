import unittest
import tempfile
from pathlib import Path
from core.hardware import HardwareDetector
from core.install_plan import build_plan
from core.version_manager import VersionManager
from tests.fixtures import game, package


class HardwareTests(unittest.TestCase):
    def test_get_system_info_structure(self):
        info = HardwareDetector.get_system_info()
        self.assertIn('primary_gpu', info)
        self.assertIn('all_gpus', info)
        self.assertIn('is_arc_detected', info)
        self.assertIn('cpu', info)
        self.assertIn('ram_gb', info)
        self.assertIn('os', info)
        self.assertIn('recommendations', info)
        self.assertIn('badge_text', info)
        self.assertIsInstance(info['recommendations']['gpu_spoofing'], bool)
        self.assertFalse(info['recommendations']['gpu_spoofing'])

    def test_arc_classification_discrete_and_integrated(self):
        cases = [
            ("Intel(R) Arc(TM) A770 Graphics", True, True, 'Intel'),
            ("Intel(R) Arc(TM) A750 Graphics", True, True, 'Intel'),
            ("Intel(R) Arc(TM) A580 Graphics", True, True, 'Intel'),
            ("Intel(R) Arc(TM) A380 Graphics", True, True, 'Intel'),
            ("Intel(R) Arc(TM) B580 Graphics", True, True, 'Intel'),
            ("Intel(R) Core Ultra 7 155H Arc Graphics", True, False, 'Intel'),
            ("Intel(R) HD Graphics 5500", False, False, 'Intel'),
            ("Intel(R) UHD Graphics 630", False, False, 'Intel'),
            ("NVIDIA GeForce RTX 4070", False, False, 'NVIDIA'),
            ("AMD Radeon RX 7900 XTX", False, False, 'AMD'),
        ]
        for name, expected_arc, expected_xmx, expected_vendor in cases:
            res = HardwareDetector._classify_gpu(name)
            self.assertEqual(res['is_arc'], expected_arc, f"Failed Arc check for {name}")
            self.assertEqual(res['has_xmx'], expected_xmx, f"Failed XMX check for {name}")
            self.assertEqual(res['vendor'], expected_vendor, f"Failed vendor check for {name}")

    def test_manual_mode_preserves_user_upscaler_intent_with_native_xess(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = package(Path(td) / 'assets')
            versions = VersionManager(fixture.parent)
            exe = game(Path(td) / 'NativeGame')
            (exe.parent / 'libxess.dll').write_bytes(b'native xess')
            analysis = {'target_dir': str(exe.parent), 'target_exe': str(exe), 'game_name': 'NativeGame'}
            
            # In automatic mode, unlisted game yields error
            auto_plan = build_plan(analysis, {'installation_mode': 'automatic', 'upscaler_enabled': True}, versions)
            self.assertTrue(any('No supported upscaler input' in e for e in auto_plan['errors']))
            
            # In manual mode, user choice to install XeSS upscaler succeeds and is not blocked
            manual_plan = build_plan(analysis, {'installation_mode': 'manual', 'upscaler_enabled': True, 'hook_method': 'dxgi.dll'}, versions)
            self.assertEqual(manual_plan['errors'], [])
            self.assertTrue(manual_plan['settings']['upscaler_enabled'])
            self.assertIn('dxgi.dll', manual_plan['files'])
            self.assertIn('libxess.dll', manual_plan['files'])


if __name__ == '__main__':
    unittest.main()
