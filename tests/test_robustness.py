"""Failure injection for recovery, evidence, recommendations and task lifecycle."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from core.injector import Injector
from core.safety import SafetyManager
from core.installation_state import installation_health
from core.game_support import capabilities, runtime_evidence
from core.detector import GameDetector
from core.recommendations import recommend
from core.tasks import TaskManager, checkpoint
from core.diagnostics import diagnostic_report
from tests.fixtures import game, package, pe_bytes


class RecoveryRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);package(self.root/'assets')
        self.exe=game(self.root/'ゲーム Space');self.folder=self.exe.parent
        self.injector=Injector(self.root/'assets')

    def install(self,**kwargs):
        return self.injector.apply_injection(str(self.folder),str(self.exe),upscaler_enabled=True,installation_mode='manual',**kwargs)

    def test_changed_files_require_preservation_before_restore(self):
        self.assertTrue(self.install()['success'])
        path=self.folder/'OptiScaler.ini';path.write_bytes(b'user configuration changes')
        self.assertEqual(installation_health(self.folder)['state'],'modified')
        self.assertFalse(self.install()['success'])
        result=self.injector.revert_injection(str(self.folder))
        self.assertIn(str(path),result['conflicts']);self.assertEqual(path.read_bytes(),b'user configuration changes')
        result=self.injector.revert_injection(str(self.folder),preserve_changes=True)
        self.assertTrue(result['success'],result)
        self.assertEqual(Path(result['preserved'][0]['copy']).read_bytes(),b'user configuration changes')
        self.assertFalse(path.exists())

    def test_missing_files_and_secondary_directory_are_not_healthy(self):
        second=self.folder/'plugins';second.mkdir()
        self.assertTrue(self.install(additional_dirs=[str(second)])['success'])
        self.assertEqual(installation_health(self.folder,[str(second)])['state'],'installed')
        (second/'libxess.dll').unlink()
        self.assertEqual(installation_health(self.folder,[str(second)])['state'],'incomplete')
        self.assertTrue(self.injector.revert_injection(str(self.folder),additional_dirs=[str(second)])['success'])

    def test_force_preserves_changed_files_and_restores_native_originals(self):
        native = self.folder / 'libxess.dll'
        original = pe_bytes(marker=b'native XeSS')
        native.write_bytes(original)
        self.assertTrue(self.install(force=True)['success'])
        changed = self.folder / 'OptiScaler.ini'
        changed.write_bytes(b'user configuration changes')
        native.write_bytes(pe_bytes(marker=b'external update'))
        expected = {str(changed): changed.read_bytes(), str(native): native.read_bytes()}
        result = self.install(force=True)
        self.assertTrue(result['success'], result)
        manifest = SafetyManager.load_manifest(self.folder)
        copies = manifest['preserved_files']
        self.assertEqual({p['original']: Path(p['copy']).read_bytes() for p in copies}, expected)
        self.assertTrue(self.injector.revert_injection(str(self.folder))['success'])
        self.assertEqual(native.read_bytes(), original)
        self.assertFalse(changed.exists())
        self.assertTrue(all(Path(p['copy']).exists() for p in copies))

    def test_force_preview_keeps_selected_input_and_hook(self):
        from core.install_plan import build_plan
        analysis = GameDetector.analyze_game(str(self.exe))
        plan = build_plan(analysis, {'force': True, 'upscaler_enabled': True,
            'starting_upscaler': 'XeSS', 'hook_method': 'winmm.dll'}, self.injector.version_manager)
        self.assertEqual(plan['settings']['starting_upscaler'], 'XeSS')
        self.assertIn('winmm.dll', plan['files'])

    def test_combined_mode_is_recorded_everywhere(self):
        self.assertTrue(self.install(frame_gen_enabled=True,fg_input='dlssg')['success'])
        manifest=SafetyManager.load_manifest(self.folder)
        self.assertEqual(manifest['metadata']['installed_mode'],'sr+fg')
        self.assertTrue(manifest['metadata']['effective_settings']['upscaler_enabled'])
        self.assertTrue(manifest['metadata']['effective_settings']['frame_gen_enabled'])

    def test_actual_process_exit_at_every_transaction_stage(self):
        script='''
import os,sys
from core.injector import Injector
import core.safety as safety
original=safety.write_json
def write(path,data):
    original(path,data)
    if data.get('operation',{}).get('state')==sys.argv[3]:os._exit(91)
safety.write_json=write
result=Injector(sys.argv[1]).apply_injection(sys.argv[2],sys.argv[4],upscaler_enabled=True,installation_mode='manual')
raise SystemExit(0 if result['success'] else 2)
'''
        for stage in ('backed_up','deploying','verifying','complete'):
            with self.subTest(stage=stage):
                exe=game(self.root/stage);folder=exe.parent
                native=folder/'dxgi.dll';native.write_bytes(b'original proxy')
                result=subprocess.run([sys.executable,'-X','utf8','-c',script,str(self.root/'assets'),str(folder),stage,str(exe)],capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,91,result.stderr)
                self.assertEqual(installation_health(folder)['state'],'installed' if stage=='complete' else 'recovery_required')
                result=self.injector.revert_injection(str(folder))
                self.assertTrue(result['success'],result);self.assertEqual(native.read_bytes(),b'original proxy')

    def test_fidelityfx_is_uncertain_not_confirmed_fg(self):
        (self.folder/'amd_fidelityfx_dx12.dll').write_bytes(pe_bytes())
        caps=capabilities(GameDetector.analyze_game(str(self.exe)))
        self.assertTrue(caps['fsr']);self.assertFalse(caps['fsrfg']);self.assertTrue(caps['fsrfg_uncertain'])

    def test_managed_files_have_explicit_origin(self):
        self.assertTrue(self.install()['success'])
        analysis=GameDetector.analyze_game(str(self.exe))
        self.assertFalse(capabilities(analysis)['xess'])
        rows=runtime_evidence(analysis)
        self.assertTrue(any(r['filename']=='libxess.dll' and r['origin']=='ArcScaler managed' for r in rows))

    def test_recommendation_matches_its_install_plan(self):
        for name in ('nvngx_dlss.dll','nvngx_dlssg.dll'):(self.folder/name).write_bytes(pe_bytes())
        rec=recommend(GameDetector.analyze_game(str(self.exe)),{'optiscaler_version':'v0.9.4'},self.injector.version_manager)
        self.assertTrue(rec['ready'],rec)
        self.assertTrue(rec['settings']['upscaler_enabled']);self.assertTrue(rec['settings']['frame_gen_enabled'])
        result=self.injector.apply_injection(str(self.folder),str(self.exe),expected_plan=rec['plan'],**rec['settings'])
        self.assertTrue(result['success'],result);self.assertEqual(result['plan']['settings'],rec['settings'])

    def test_report_redacts_paths_but_retains_evidence(self):
        report=diagnostic_report({'target_exe':r'C:\Users\Alice\Games\game.exe'}, {},None,None,None,[('now','error',r'Failed C:\Users\Alice\private\file')])
        self.assertNotIn('Alice',report);self.assertIn('<PATH>',report);json.loads(report)


class TaskLifecycle(unittest.TestCase):
    def test_superseded_queued_callback_never_runs(self):
        class Dispatcher:
            def __init__(self):self.items=[]
            def post(self,*args):self.items.append(args)
        dispatcher=Dispatcher();manager=TaskManager(dispatcher,workers=1)
        self.addCleanup(manager.close);seen=[]
        manager.submit('scan',lambda:'old',seen.append,seen.append)
        deadline=time.monotonic()+2
        while not dispatcher.items and time.monotonic()<deadline:time.sleep(.005)
        manager.submit('scan',lambda:'new',seen.append,seen.append)
        deadline=time.monotonic()+2
        while len(dispatcher.items)<2 and time.monotonic()<deadline:time.sleep(.005)
        for callback,*args in dispatcher.items:callback(*args)
        self.assertEqual(seen,['new'])

    def test_close_cancels_cooperative_worker_without_delivery(self):
        class Dispatcher:
            def post(self,*args):raise AssertionError('Cancelled tasks must not deliver')
        started=threading.Event();manager=TaskManager(Dispatcher(),workers=1)
        def work():
            started.set()
            while True:checkpoint();time.sleep(.005)
        manager.submit('scan',work,lambda _:None,lambda _:None)
        self.assertTrue(started.wait(2));manager.close()
        manager.threads[0].join(2);self.assertFalse(manager.threads[0].is_alive())
