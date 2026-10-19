"""ArcScaler acceptance: independent fixtures for decisions, migration and recovery."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core.detector import GameDetector
from core.injector import Injector
from core.install_plan import build_plan
from core.settings import defaults,validate,select_mode,migrate
from core.config_generator import ConfigGenerator
from core.pe import inspect_pe
from core.paths import migrate_legacy
from core.icons import IconCache
from tests.fixtures import game,package,pe_bytes

class ArcScalerAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.release=package(self.root/'assets');self.injector=Injector(self.root/'assets')
        env=patch.dict(os.environ,{'ARCSCALER_DATA_DIR':str(self.root/'app')});env.start();self.addCleanup(env.stop)

    def plan(self,exe,**settings):
        return build_plan(GameDetector.analyze_game(str(exe)),defaults()|{'upscaler_enabled':True}|settings,self.injector.version_manager)

    def test_two_auto_fixtures_choose_distinct_folder_proxy_input(self):
        launcher=game(self.root/'unreal','Launcher.exe')
        shipping=game(self.root/'unreal/Game/Binaries/Win64','Game-Win64-Shipping.exe',imports=('d3d12.dll','version.dll'))
        (shipping.parent/'nvngx_dlss.dll').write_bytes(pe_bytes())
        detected=GameDetector.analyze_game(str(launcher));self.assertEqual(detected['target_exe'],str(shipping))
        a=self.plan(shipping)
        other=game(self.root/'dx11','second.exe',imports=('d3d11.dll','winmm.dll'))
        (other.parent/'ffx_fsr2api_x64.dll').write_bytes(pe_bytes())
        b=self.plan(other)
        self.assertFalse(a['errors'],a);self.assertFalse(b['errors'],b)
        self.assertEqual(a['settings']['hook_method'],'version.dll');self.assertEqual(b['settings']['hook_method'],'winmm.dll')
        self.assertEqual(a['settings']['starting_upscaler'],'DLSS');self.assertEqual(b['settings']['starting_upscaler'],'FSR')
        self.assertNotEqual(a['target_dir'],b['target_dir'])

    def test_existing_proxy_skipped_and_anticheat_blocked(self):
        exe=game(self.root/'game',imports=('d3d12.dll','version.dll'))
        (exe.parent/'nvngx_dlss.dll').write_bytes(pe_bytes());(exe.parent/'version.dll').write_bytes(b'ReShade')
        plan=self.plan(exe);self.assertFalse(plan['errors'],plan);self.assertNotEqual(plan['settings']['hook_method'],'version.dll')
        (exe.parent/'EasyAntiCheat').mkdir()
        result=self.injector.apply_injection(str(exe.parent),str(exe),upscaler_enabled=True)
        self.assertFalse(result['success']);self.assertIn('Anti-Cheat',result['error'])
        self.assertFalse((exe.parent/'OptiScaler.ini').exists())

    def test_pe_apis_architecture_and_malformed_import(self):
        for dll,api in [('d3d11.dll','DX11'),('d3d12.dll','DX12'),('vulkan-1.dll','Vulkan')]:
            exe=game(self.root/api,imports=(dll,));info=inspect_pe(exe)
            self.assertEqual(info['graphics_api'],api);self.assertEqual(info['architecture'],'x64')
        exe=game(self.root/'mixed',imports=('d3d11.dll','d3d12.dll'))
        self.assertEqual(inspect_pe(exe)['graphics_api'],'Multiple')
        import struct
        data=bytearray(exe.read_bytes());struct.pack_into('<I',data,152+112+8,0xffffffff);exe.write_bytes(data)
        self.assertFalse(GameDetector.analyze_game(str(exe))['valid'])

    def test_fg_refuses_dx11_unknown_and_force(self):
        for imports in [('d3d11.dll',),()]:
            exe=game(self.root/str(len(imports)),imports=imports)
            result=self.injector.apply_injection(str(exe.parent),str(exe),frame_gen_enabled=True,force=True,installation_mode='manual')
            self.assertFalse(result['success']);self.assertIn('DirectX 12',result['error'])

    def test_independent_modes_and_combined_ini(self):
        sr=select_mode(defaults(),'upscaler_enabled',True)
        fg=select_mode(sr,'frame_gen_enabled',True)
        self.assertTrue(fg['upscaler_enabled']);self.assertTrue(fg['frame_gen_enabled'])
        validate(fg,require_mode=True)
        cfg=ConfigGenerator.parser(ConfigGenerator.generate_nvngx_ini(upscaler_enabled=True,frame_gen_enabled=True,fg_input='dlssg'))
        self.assertEqual(cfg['FrameGen']['Enabled'],'true');self.assertEqual(cfg['Inputs']['EnableDlssInputs'],'true')
        cfg=ConfigGenerator.parser(ConfigGenerator.generate_nvngx_ini(frame_gen_enabled=True,fg_input='dlssg'))
        self.assertEqual(cfg['FrameGen']['Enabled'],'true');self.assertEqual(cfg['Inputs']['EnableDlssInputs'],'false')
        cfg=ConfigGenerator.parser(ConfigGenerator.generate_nvngx_ini(upscaler_enabled=True,custom_scale=.5))
        self.assertEqual(cfg['FrameGen']['Enabled'],'false');self.assertEqual(cfg['UpscaleRatio']['UpscaleRatioOverrideValue'],'2.000000')

    def test_dynamic_release_runtime_and_exact_revert(self):
        extra=self.release/'libxess_future.dll';extra.write_bytes(pe_bytes())
        exe=game(self.root/'restore');folder=exe.parent
        (folder/'dxgi.dll').write_bytes(b'original proxy')
        (folder/'OptiScaler.ini').write_text('[Custom]\nKept=yes\n')
        before={p.name:p.read_bytes() for p in folder.iterdir() if p.is_file()}
        result=self.injector.apply_injection(str(folder),str(exe),upscaler_enabled=True,installation_mode='manual')
        self.assertTrue(result['success'],result);self.assertIn(extra.name,result['injected_suite'])
        self.assertTrue(self.injector.verify_installation(folder)['success'])
        self.assertTrue(self.injector.revert_injection(str(folder))['success'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir() if p.is_file()})
        self.assertEqual(sorted(before),sorted(p.name for p in folder.iterdir()))

    def test_local_override_and_ini_applied(self):
        exe=game(self.root/'rules','custom.exe')
        (exe.parent/'nvngx_dlss.dll').write_bytes(pe_bytes())
        user=self.root/'rules.json';user.write_text(json.dumps({'exe':{'custom.exe':{'hook':'winhttp.dll','ini_overrides':{'InitFlags':{'DepthInverted':'true'}}}}}))
        result=self.injector.apply_injection(str(exe.parent),str(exe),upscaler_enabled=True,compatibility_path=str(user))
        self.assertTrue(result['success'],result);self.assertEqual(result['hook_method'],'winhttp.dll')
        cfg=ConfigGenerator.parser((exe.parent/'OptiScaler.ini').read_text());self.assertEqual(cfg['InitFlags']['DepthInverted'],'true')

    def test_stale_preview_rejected_before_mutation(self):
        exe=game(self.root/'stale');settings={'upscaler_enabled':True,'installation_mode':'manual','optiscaler_version':'v0.9.4'}
        plan=self.plan(exe,**settings)
        (self.release/'libxess.dll').write_bytes(pe_bytes(marker=b'changed'))
        result=self.injector.apply_injection(str(exe.parent),str(exe),expected_plan=plan,**settings)
        self.assertFalse(result['success']);self.assertIn('plan changed',result['error']);self.assertFalse((exe.parent/'OptiScaler.ini').exists())

    def test_migration_merges_profiles_without_losing_conflicts(self):
        local=self.root/'local';roaming=self.root/'roaming';new=local/'ArcScaler';new.mkdir(parents=True)
        for base,profiles in [(local,{'a':{'name':'old'},'b':{'name':'second'}}),(roaming,{'c':{'name':'roaming'}})]:
            old=base/'OptiScalerXeSS';old.mkdir(parents=True);(old/'profiles.json').write_text(json.dumps(profiles))
        (new/'profiles.json').write_text(json.dumps({'a':{'name':'new'}}))
        with patch.dict(os.environ,{'LOCALAPPDATA':str(local),'APPDATA':str(roaming)}):
            migrate_legacy(new);migrate_legacy(new)
        merged=json.loads((new/'profiles.json').read_text());self.assertEqual(set(merged),{'a','b','c'});self.assertEqual(merged['a']['name'],'new')
        self.assertTrue((new/'migration-originals/0/profiles.json').is_file());self.assertTrue((local/'OptiScalerXeSS/profiles.json').is_file())
        converted=migrate({'upscaler_enabled':True,'frame_gen_enabled':True});self.assertTrue(converted['upscaler_enabled']);self.assertTrue(converted['frame_gen_enabled'])

    def test_icon_fallback_cached_for_missing_executable(self):
        cache=IconCache(self.root/'icons',None)
        try:
            first=cache.resolve(str(self.root/'absent.exe'),'Example Game')
            second=cache.resolve(str(self.root/'absent.exe'),'Example Game')
            self.assertEqual(first.tobytes(),second.tobytes());self.assertEqual(len(list((self.root/'icons').glob('*.png'))),1)
        finally:cache.close()
