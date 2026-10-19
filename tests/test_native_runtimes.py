"""Selective deployment must never acquire or overwrite native runtime files."""
from pathlib import Path
import tempfile
import unittest
from core.config_generator import ConfigGenerator
from core.detector import GameDetector
from core.injector import Injector
from core.install_plan import build_plan
from core.installation_state import installation_health
from core.native_runtimes import REUSE, ADD_FG
from core.safety import SafetyManager
from tests.fixtures import game, package, pe_bytes, runtime_bytes


class NativeRuntimeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        package(self.root / 'assets')
        self.exe = game(self.root / 'game')
        self.folder = self.exe.parent
        self.injector = Injector(self.root / 'assets')
        self.settings = dict(upscaler_enabled=True, frame_gen_enabled=True,
                             runtime_method=REUSE, installation_mode='manual')

    def native(self, name, folder=None):
        path = (folder or self.folder) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(runtime_bytes(name, marker=('native ' + name).encode()))
        return path

    def plan(self, **extra):
        return build_plan(GameDetector.analyze_game(str(self.exe)), self.settings | extra,
                          self.injector.version_manager)

    def install(self, **extra):
        return self.injector.apply_injection(str(self.folder), str(self.exe), **(self.settings | extra))

    def test_reuse_both_and_restore_preserves_all_native_bytes(self):
        natives = [self.native(n) for n in ('libxess.dll', 'libxess_fg.dll', 'libxell.dll')]
        before = {p: p.read_bytes() for p in natives}
        plan = self.plan()
        self.assertFalse(plan['errors'], plan)
        self.assertEqual(set(plan['files']), {'dxgi.dll', 'OptiScaler.ini', 'fakenvapi.dll', 'fakenvapi.ini'})
        result = self.install(expected_plan=plan)
        self.assertTrue(result['success'], result)
        self.assertEqual(set(result['injected_suite']), set(plan['files']))
        ini = ConfigGenerator.parser((self.folder / 'OptiScaler.ini').read_text())
        self.assertEqual(ini['Libraries']['XeFGPath'], str(self.folder))
        self.assertEqual(ini['Inputs']['EnableDlssInputs'], 'true')
        self.assertEqual(ini['FrameGen']['FGInput'], 'dlssg')
        self.assertEqual(ini['FrameGen']['FGOutput'], 'xefg')
        self.assertTrue(self.install()['success'])
        self.assertTrue(self.injector.revert_injection(str(self.folder))['success'])
        self.assertEqual({p: p.read_bytes() for p in natives}, before)

    def test_add_missing_fg_keeps_native_sr_and_existing_xell(self):
        sr = self.native('libxess.dll')
        xell = self.native('libxell.dll')
        before = {p: p.read_bytes() for p in (sr, xell)}
        result = self.install(runtime_method=ADD_FG, upscaler_enabled=False)
        self.assertTrue(result['success'], result)
        self.assertIn('libxess_fg.dll', result['injected_suite'])
        self.assertNotIn('libxell.dll', result['injected_suite'])
        self.assertNotIn('libxess.dll', result['injected_suite'])
        self.assertTrue(self.install(runtime_method=ADD_FG, upscaler_enabled=False)['success'])
        self.assertTrue(self.injector.revert_injection(str(self.folder))['success'])
        self.assertFalse((self.folder / 'libxess_fg.dll').exists())
        self.assertEqual({p: p.read_bytes() for p in (sr, xell)}, before)

    def test_sr_only_installs_exactly_proxy_and_ini(self):
        self.native('libxess.dll')
        result = self.install(frame_gen_enabled=False)
        self.assertTrue(result['success'], result)
        self.assertEqual(set(result['injected_suite']), {'dxgi.dll', 'OptiScaler.ini'})

    def test_missing_or_invalid_required_runtime_blocks_even_force(self):
        self.native('libxess.dll')
        for invalid in (False, True):
            if invalid:
                (self.folder / 'libxess_fg.dll').write_bytes(b'bad DLL')
            result = self.install(force=True)
            self.assertFalse(result['success'], result)
            self.assertFalse(SafetyManager.has_active_backup(self.folder))
            self.assertFalse((self.folder / 'OptiScaler.ini').exists())

    def test_preview_change_is_rejected_and_native_updates_are_not_rolled_back(self):
        sr = self.native('libxess.dll')
        plan = self.plan(frame_gen_enabled=False)
        sr.write_bytes(runtime_bytes(marker=b'game update'))
        result = self.install(frame_gen_enabled=False, expected_plan=plan)
        self.assertFalse(result['success'])
        self.assertIn('plan changed', result['error'])
        self.assertTrue(self.install(frame_gen_enabled=False)['success'])
        sr.write_bytes(runtime_bytes(marker=b'another game update'))
        latest = sr.read_bytes()
        self.assertEqual(installation_health(self.folder)['state'], 'modified')
        self.assertTrue(self.injector.revert_injection(str(self.folder))['success'])
        self.assertEqual(sr.read_bytes(), latest)

    def test_subfolder_and_ambiguous_selection(self):
        first = self.native('libxess.dll', self.folder / 'plugin A')
        self.native('libxess.dll', self.folder / 'plugin B')
        result = self.install(frame_gen_enabled=False)
        self.assertFalse(result['success'])
        self.assertIn('Multiple copies', result['error'])
        result = self.install(frame_gen_enabled=False, native_xess_path=str(first))
        self.assertTrue(result['success'], result)
        ini = ConfigGenerator.parser((self.folder / 'OptiScaler.ini').read_text())
        self.assertEqual(ini['Libraries']['XeSSPath'], str(first.parent))

    def test_managed_runtime_requires_restore_before_reuse(self):
        result = self.install(runtime_method='Bundled runtimes', frame_gen_enabled=False)
        self.assertTrue(result['success'], result)
        result = self.install(frame_gen_enabled=False, force=True)
        self.assertFalse(result['success'])
        self.assertIn('Restore original files', result['error'])

    def test_outside_game_path_is_rejected(self):
        external = self.native('libxess.dll', self.root / 'external')
        result = self.install(frame_gen_enabled=False, native_xess_path=str(external), force=True)
        self.assertFalse(result['success'])
        self.assertIn('inside this game', result['error'])
