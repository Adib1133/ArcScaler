"""Regression coverage for deliberate routes, compatibility gates and last-change undo."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from core.detector import GameDetector
from core.injector import Injector
from core.install_plan import build_plan
from core.installation_state import installation_health
from core.native_runtimes import REUSE
from core.recommendations import recommend
from core.runtime_status import runtime_status
from core.runtime_validation import inspect_runtime
from core.safety import SafetyManager
from core.settings import defaults
from core.workflows import GOALS, goal_settings, route_summary
from tests.fixtures import game, package, pe_bytes, runtime_bytes


class RefinementTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.release=package(self.root/'assets')
        self.exe=game(self.root/'game');self.folder=self.exe.parent
        self.injector=Injector(self.root/'assets')

    def install(self, **kwargs):
        return self.injector.apply_injection(str(self.folder),str(self.exe),
            **(dict(upscaler_enabled=True,installation_mode='manual')|kwargs))

    def native(self):
        for name in ('libxess.dll','libxess_fg.dll','libxell.dll'):
            (self.folder/name).write_bytes(runtime_bytes(name))

    def test_suggestions_preserve_explicit_reuse_and_paths(self):
        self.native()
        current=defaults()|dict(runtime_method=REUSE,upscaler_enabled=True,frame_gen_enabled=True,
            installation_mode='manual',native_xess_path=str(self.folder/'libxess.dll'))
        rec=recommend(GameDetector.analyze_game(str(self.exe)),current,self.injector.version_manager)
        self.assertTrue(rec['ready'],rec)
        for key in ('runtime_method','native_xess_path','upscaler_enabled','frame_gen_enabled'):
            self.assertEqual(rec['settings'][key],current[key])

    def test_reuse_does_not_require_unused_package_runtimes(self):
        self.native()
        for name in ('libxess.dll','libxess_dx11.dll','libxess_fg.dll','libxell.dll'):
            (self.release/name).unlink()
        self.assertIn('v0.9.4',self.injector.version_manager.get_installed_versions())
        result=self.install(runtime_method=REUSE,frame_gen_enabled=True)
        self.assertTrue(result['success'],result)
        self.assertEqual(set(result['injected_suite']),{'dxgi.dll','OptiScaler.ini','fakenvapi.dll','fakenvapi.ini'})

    def test_valid_pe_without_runtime_api_is_rejected_even_with_force(self):
        (self.folder/'libxess.dll').write_bytes(pe_bytes())
        result=self.install(runtime_method=REUSE,force=True)
        self.assertFalse(result['success']);self.assertIn('exports',result['error'])
        self.assertFalse(SafetyManager.has_active_backup(self.folder))

    def test_bundled_real_runtime_exports(self):
        for name in ('libxess.dll','libxess_fg.dll','libxell.dll'):
            path=Path(__file__).resolve().parents[1]/'assets/versions/v0.9.4'/name
            self.assertIn('api',inspect_runtime(path,name))

    def test_undo_restores_previous_proxy_settings_and_keeps_original_backup(self):
        original=pe_bytes(marker=b'original proxy');(self.folder/'dxgi.dll').write_bytes(original)
        self.assertTrue(self.install()['success'])
        old_ini=(self.folder/'OptiScaler.ini').read_bytes()
        self.assertTrue(self.install(hook_method='winmm.dll',sharpness=.7)['success'])
        self.assertTrue(installation_health(self.folder)['can_undo'])
        result=self.injector.undo_last_change(str(self.folder),str(self.exe))
        self.assertTrue(result['success'],result)
        self.assertFalse((self.folder/'winmm.dll').exists())
        self.assertEqual((self.folder/'OptiScaler.ini').read_bytes(),old_ini)
        self.assertEqual(installation_health(self.folder)['state'],'installed')
        self.assertFalse(installation_health(self.folder)['can_undo'])
        self.assertTrue(self.injector.revert_injection(str(self.folder))['success'])
        self.assertEqual((self.folder/'dxgi.dll').read_bytes(),original)

    def test_undo_failure_is_retryable(self):
        self.assertTrue(self.install()['success'])
        self.assertTrue(self.install(sharpness=.8)['success'])
        original_copy=SafetyManager._safe_copy
        copied=[]
        def partial_copy(source,destination):
            copied.append(destination)
            if len(copied)==2:raise OSError('disk failure after first restoration')
            original_copy(source,destination)
        with patch.object(SafetyManager,'_safe_copy',side_effect=partial_copy):
            self.assertFalse(self.injector.undo_last_change(str(self.folder))['success'])
        self.assertEqual(installation_health(self.folder)['state'],'recovery_required')
        self.assertTrue(installation_health(self.folder)['can_undo'])
        self.assertTrue(self.injector.undo_last_change(str(self.folder))['success'])

    def test_corrupt_undo_does_not_touch_game_and_original_restore_still_works(self):
        self.assertTrue(self.install()['success']);self.assertTrue(self.install(sharpness=.8)['success'])
        current=(self.folder/'OptiScaler.ini').read_bytes()
        manifest=SafetyManager.load_manifest(self.folder)
        item=next(i for entries in manifest['undo']['entries'].values() for i in entries.values() if i)
        Path(item['copy']).write_bytes(b'corrupted')
        self.assertFalse(self.injector.undo_last_change(str(self.folder))['success'])
        self.assertEqual((self.folder/'OptiScaler.ini').read_bytes(),current)
        self.assertTrue(self.injector.revert_injection(str(self.folder))['success'])

    def test_undo_does_not_overwrite_external_edits(self):
        self.assertTrue(self.install()['success']);self.assertTrue(self.install(sharpness=.8)['success'])
        (self.folder/'OptiScaler.ini').write_bytes(b'external edit')
        result=self.injector.undo_last_change(str(self.folder))
        self.assertFalse(result['success']);self.assertTrue(result['conflicts'])
        self.assertEqual((self.folder/'OptiScaler.ini').read_bytes(),b'external edit')

    def test_runtime_status_is_fresh_scoped_and_never_auto_confirms(self):
        self.assertTrue(self.install()['success'])
        log=self.folder/'OptiScaler.log';log.write_text('OptiScaler initialized')
        os.utime(log,(1,1))
        self.assertEqual(runtime_status(self.folder)['state'],'unverified')
        log.write_text('OptiScaler initialized')
        status=runtime_status(self.folder)
        self.assertEqual(status['state'],'detected')
        confirmation=dict(signature=status['signature'],confirmed_at=time.time())
        self.assertEqual(runtime_status(self.folder,confirmation=confirmation)['state'],'confirmed')
        self.assertTrue(self.install(sharpness=.6)['success'])
        self.assertNotEqual(runtime_status(self.folder,confirmation=confirmation)['state'],'confirmed')

    def test_modified_installation_cannot_report_confirmed_route(self):
        self.assertTrue(self.install()['success'])
        status=runtime_status(self.folder)
        confirmation=dict(signature=status['signature'],confirmed_at=time.time())
        (self.folder/'OptiScaler.ini').write_bytes(b'external change')
        self.assertEqual(runtime_status(self.folder,confirmation=confirmation)['state'],'unverified')

    def test_undo_secondary_directory_retains_native_original(self):
        secondary=self.folder/'plugin';secondary.mkdir()
        original=pe_bytes(marker=b'plugin original')
        (secondary/'dxgi.dll').write_bytes(original)
        self.assertTrue(self.install()['success'])
        self.assertTrue(self.install(additional_dirs=[str(secondary)])['success'])
        result=self.injector.undo_last_change(str(self.folder),additional_dirs=[str(secondary)])
        self.assertTrue(result['success'],result)
        self.assertEqual((secondary/'dxgi.dll').read_bytes(),original)
        self.assertFalse((secondary/'OptiScaler.ini').exists())

    def test_target_change_invalidates_preview(self):
        settings=defaults()|dict(upscaler_enabled=True,installation_mode='manual')
        plan=build_plan(GameDetector.analyze_game(str(self.exe)),settings,self.injector.version_manager)
        (self.folder/'dxgi.dll').write_bytes(pe_bytes(marker=b'new mod'))
        result=self.install(expected_plan=plan)
        self.assertFalse(result['success']);self.assertIn('plan changed',result['error'])

    def test_presets_and_summary_do_not_enable_unrequested_modes(self):
        selected=goal_settings(GOALS[0],defaults())
        self.assertFalse(selected['upscaler_enabled']);self.assertTrue(selected['frame_gen_enabled'])
        self.assertIn('Game setting unchanged',route_summary(selected))
        selected=goal_settings(GOALS[1],selected,native_sr=True)
        self.assertFalse(selected['frame_gen_enabled']);self.assertEqual(selected['runtime_method'],REUSE)
