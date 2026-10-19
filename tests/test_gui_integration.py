"""Withdrawn real Tk workflows with isolated storage and no network."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from tests.fixtures import package, game
from gui.main_window import MainWindow


class GUIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        package(self.root / 'assets')
        package(self.root / 'assets', 'v0.9.3')
        patches = [patch('gui.main_window.prepare_assets', return_value=self.root / 'assets'),
                   patch('gui.main_window.data_root', return_value=self.root),
                   patch('core.version_manager.urllib.request.urlopen', side_effect=OSError('offline')),
                   patch('tkinter.messagebox.showerror'), patch('tkinter.messagebox.showinfo')]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.app = MainWindow()
        if self._testMethodName != 'test_launch_uses_full_desktop_layout_without_manual_resize':
            self.app.withdraw()
        self.addCleanup(self.close)
        self.tab = self.app.config_tab
        self.profile = self.app.library.add_game_by_path(str(game(self.root / 'game')))
        self.app._on_game_selected_from_library(self.profile)

    def close(self):
        self.pump()
        self.app.config_tab.shutdown()
        self.app.version_tab.shutdown()
        self.app.library_tab.shutdown();self.app.tasks.close()
        self.app.log_console.dispatcher.close()
        if self.app.hardware_worker:self.app.hardware_worker.join(timeout=20)
        self.app.icons.close()
        self.app.dispatcher.close()
        self.app.cancel_timers()
        self.app.destroy()
        import customtkinter as ctk
        ctk.set_widget_scaling(1.0)
        self.app=None
        self.tab=None
        import gc
        gc.collect()

    def pump(self):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.app.update()
            if not self.tab.busy and not self.app.version_tab.busy:
                # Let queued callbacks and initial release timer execute.
                for _ in range(5):
                    time.sleep(0.02)
                    self.app.update()
                if not self.tab.busy and not self.app.version_tab.busy:
                    return
            time.sleep(0.01)
        import faulthandler
        faulthandler.dump_traceback()
        self.fail('GUI operation did not finish within 30 seconds')

    def confirm_preview(self, text='Confirm installation'):
        import customtkinter as ctk
        def visit(widget):
            for child in widget.winfo_children():
                if isinstance(child,ctk.CTkButton) and child.cget('text')==text:
                    child.invoke();return True
                if visit(child):return True
            return False
        self.assertTrue(visit(self.app),'Installation preview must precede mutation')

    def test_force_requires_separate_risk_acknowledgement(self):
        import customtkinter as ctk
        self.tab.mode_var.set('manual')
        self.tab.upscaler_var.set(True)
        self.tab.force_injection();self.pump()
        self.confirm_preview('Confirm force injection');self.pump()
        folder=Path(self.profile['target_dir'])
        self.assertFalse((folder/'OptiScaler.ini').exists())
        def acknowledge(widget):
            for child in widget.winfo_children():
                if isinstance(child,ctk.CTkCheckBox) and child.cget('text').startswith('I accept'):
                    child.select();return True
                if acknowledge(child):return True
            return False
        self.assertTrue(acknowledge(self.app))
        self.confirm_preview('Confirm force injection');self.pump()
        self.assertTrue((folder/'OptiScaler.ini').exists())
        self.assertEqual(self.app.library.profiles[self.profile['id']]['installed_mode'],'sr')

    def test_native_runtime_section_saves_method_and_installs_fg_only(self):
        from core.native_runtimes import ADD_FG
        from tests.fixtures import runtime_bytes
        folder=Path(self.profile['target_dir'])
        native=folder/'libxess.dll'
        original=runtime_bytes(marker=b'native XeSS')
        native.write_bytes(original)
        self.tab.variables['runtime_method'].set(ADD_FG)
        self.tab._native_method_changed(ADD_FG)
        self.assertFalse(self.tab.upscaler_var.get())
        self.assertTrue(self.tab.frame_gen_var.get())
        self.assertEqual(self.tab.native_paths.winfo_manager(),'pack')
        self.tab.apply_injection();self.pump();self.confirm_preview();self.pump()
        self.assertEqual(native.read_bytes(),original)
        self.assertTrue((folder/'libxess_fg.dll').exists())
        saved=self.app.library.profiles[self.profile['id']]
        self.assertEqual(saved['runtime_method'],ADD_FG)

    def test_independent_modes_disable_only_their_own_controls(self):
        self.tab.upscaler_var.set(True);self.tab._toggle_mode('upscaler_enabled')
        self.assertEqual(self.tab.controls['fg_input'].cget('state'),'disabled')
        self.assertEqual(self.tab.controls['starting_upscaler'].cget('state'),'normal')
        self.tab.frame_gen_var.set(True);self.tab._toggle_mode('frame_gen_enabled')
        self.assertTrue(self.tab.upscaler_var.get())
        self.assertEqual(self.tab.controls['starting_upscaler'].cget('state'),'normal')
        self.assertEqual(self.tab.controls['fg_input'].cget('state'),'normal')
        self.assertEqual(self.tab.controls['hook_method'].cget('state'),'normal')
        self.tab.frame_gen_var.set(False);self.tab._toggle_mode('frame_gen_enabled')
        self.assertEqual(self.tab.apply_btn.cget('state'),'normal')
        self.tab.upscaler_var.set(False);self.tab._toggle_mode('upscaler_enabled')
        self.assertEqual(self.tab.apply_btn.cget('state'),'disabled')
        self.assertEqual(self.tab.force_btn.cget('state'),'disabled')
        stored=self.app.library.profiles[self.profile['id']]
        self.assertFalse(stored['upscaler_enabled']);self.assertFalse(stored['frame_gen_enabled'])

    def test_evidence_status_and_stable_library_selection(self):
        self.pump()
        self.tab._installation_status(False)
        self.assertEqual(self.tab.status_lbl.cget('text_color'),'#FF7887')
        self.tab._installation_status(True)
        self.assertEqual(self.tab.status_lbl.cget('text_color'),'#42D9A0')
        analysis=dict(valid=True,graphics_api='DX12',discovered_locations=[{'details':[{'filename':'nvngx_dlss.dll'}]}])
        self.tab._analysis=analysis
        self.tab._evidence=None
        self.tab.support=dict(dlss=True,fsr=False,xess=False,dlssg=False,fsrfg=False,xefg=False)
        self.tab._show_evidence(analysis)
        self.assertEqual(self.tab.evidence_rows['dlss'][1].cget('text_color'),'#42D9A0')
        self.assertIn('nvngx_dlss.dll',self.tab.evidence_files.cget('text'))
        self.assertIn('No FG runtime detected',self.tab.suggestion_lbl.cget('text'))
        self.app.library_tab.refresh_library()
        row=self.app.library_tab.rows[self.profile['id']]
        self.app.library_tab.set_active_game(self.profile)
        self.assertIs(row,self.app.library_tab.rows[self.profile['id']])

    def test_install_update_revert_workflow(self):
        self.tab.mode_var.set('manual')
        self.tab.upscaler_var.set(True)
        self.tab.starting_upscaler_var.set('FSR')
        self.tab.apply_injection()
        self.pump()
        self.confirm_preview()
        self.pump()
        folder = Path(self.profile['target_dir'])
        self.assertTrue((folder / 'OptiScaler.ini').exists())
        self.assertTrue((folder / 'libxess_dx11.dll').exists())
        self.tab.hook_var.set('winmm.dll')
        self.tab.apply_injection()
        self.pump()
        self.confirm_preview()
        self.pump()
        self.assertTrue((folder / 'winmm.dll').exists())
        self.assertFalse((folder / 'dxgi.dll').exists())
        with patch('tkinter.messagebox.askyesno', return_value=True):
            self.tab.revert_changes()
            self.pump()
        self.assertFalse((folder / 'OptiScaler.ini').exists())
        self.assertFalse((folder / 'fakenvapi.ini').exists())

    def test_components_start_unselected_and_user_defined_ignores_custom_scale(self):
        self.assertFalse(self.tab.upscaler_var.get())
        self.assertFalse(self.tab.frame_gen_var.get())
        self.assertFalse(self.tab.spoof_var.get())
        self.assertEqual(self.tab.quality_var.get(), 'User Defined')
        self.tab.custom_scale_enabled_var.set(True)
        self.assertAlmostEqual(self.tab._settings()['custom_scale'],0.67)

    def test_every_saved_control_restored_on_profile_switch(self):
        self.tab.version_var.set('v0.9.3')
        self.tab.invert_depth_var.set('true')
        self.tab.jitter_var.set('false')
        self.tab.fg_input_var.set('fsrfg30')
        self.tab.quality_var.set('Quality')
        self.tab.custom_scale_enabled_var.set(True)
        self.tab.scale_slider.set(0.7)
        self.tab._on_pipeline_param_changed()
        other = self.app.library.add_game_by_path(str(game(self.root / 'other')))
        self.tab.load_game(other)
        restored = self.app.library.profiles[self.profile['id']]
        self.tab.load_game(restored)
        self.assertEqual(self.tab.version_var.get(), 'v0.9.3')
        self.assertEqual(self.tab.invert_depth_var.get(), 'true')
        self.assertEqual(self.tab.jitter_var.get(), 'false')
        self.assertEqual(self.tab.fg_input_var.get(), 'fsrfg30')
        self.assertAlmostEqual(self.tab.scale_slider.get(), 0.7)

    def test_crash_callback_captures_game_and_respects_failed_recovery(self):
        with patch('gui.config_tab.CrashWatchdog') as cls, patch.object(self.tab.injector, 'is_injected', return_value=True), patch.object(self.tab.injector, 'is_game_running', return_value=False):
            self.tab.launch_and_protect()
            callback = cls.call_args.kwargs['on_crash_detected']
        other = self.app.library.add_game_by_path(str(game(self.root / 'other')))
        self.tab.load_game(other)
        with patch.object(self.app.library, 'update_profile', wraps=self.app.library.update_profile) as update:
            callback('crash in A', {'success': False, 'error': 'Still running; backups retained'})
            self.pump()
        self.assertTrue(any(call.args[0]==self.profile['id'] for call in update.call_args_list))
        self.assertEqual(self.tab.current_profile['id'], other['id'])

    def test_dispatcher_worker_queues_without_touching_tk(self):
        import threading
        seen = []
        thread = threading.Thread(target=lambda: self.tab.dispatcher.post(lambda: seen.append(threading.get_ident())))
        thread.start()
        thread.join()
        self.pump()
        self.assertEqual(seen, [threading.get_ident()])

    def wait_for_scan(self):
        deadline=time.monotonic()+15
        while self.tab._scan_pending and time.monotonic()<deadline:
            self.app.update();time.sleep(.02)
        self.assertFalse(self.tab._scan_pending)

    def test_installed_draft_state_changes_primary_action(self):
        self.tab.mode_var.set('manual');self.tab.upscaler_var.set(True)
        self.tab._on_pipeline_param_changed()
        self.tab.apply_injection();self.pump();self.confirm_preview();self.pump();self.wait_for_scan()
        self.assertEqual(self.tab.apply_btn.cget('text'),'Up to date')
        self.assertEqual(self.tab.apply_btn.cget('state'),'disabled')
        self.tab.quality_var.set('Performance');self.tab._on_pipeline_param_changed()
        self.assertEqual(self.tab.draft_lbl.cget('text'),'Changes not installed')
        self.assertEqual(self.tab.apply_btn.cget('text'),'Update installation')

    def test_expanded_panels_survive_game_switch(self):
        self.tab._toggle_panel(self.tab.evidence_details)
        self.tab._toggle_panel(self.tab.sr_details)
        other=self.app.library.add_game_by_path(str(game(self.root/'other')))
        self.tab.load_game(other)
        self.assertFalse(self.tab.evidence_details.winfo_manager())
        self.tab.load_game(self.profile)
        self.assertTrue(self.tab.evidence_details.winfo_manager())
        self.assertTrue(self.tab.sr_details.winfo_manager())

    def test_ui_scales_keep_primary_controls_in_the_window(self):
        # Map off-screen so Tk assigns real child geometry; no desktop input.
        self.app.geometry('1100x720+10000+10000');self.app.deiconify();self.app.update()
        for scale in ('100%','125%','150%','200%'):
            self.app._apply_scale(scale)
            self.app.geometry('1100x720+10000+10000')
            deadline=time.monotonic()+1
            while time.monotonic()<deadline:self.app.update();time.sleep(.02)
            self.app._responsive(None);self.app.update()
            for widget in (self.tab.apply_btn,self.tab.launch_btn,self.tab.menu_button,self.tab.revert_btn,self.tab.goal_menu):
                left=widget.winfo_rootx()-self.app.winfo_rootx()
                self.assertGreaterEqual(left,0,(scale,type(widget).__name__))
                self.assertLessEqual(left+widget.winfo_width(),self.app.winfo_width()+2,(scale,type(widget).__name__))
            if scale=='200%':self.assertTrue(self.app.compact_nav.winfo_manager())

    def test_goal_compactness_and_confirmation_scope(self):
        import gc
        from core.workflows import GOALS
        from tests.fixtures import pe_bytes
        (Path(self.profile['target_dir'])/'nvngx_dlss.dll').write_bytes(pe_bytes())
        self.assertFalse(gc.isenabled(), 'Tk cyclic collection must be scheduled on the UI thread')
        self.assertFalse(self.tab.sr_body.winfo_manager())
        self.assertFalse(self.tab.fg_body.winfo_manager())
        self.tab._choose_goal(GOALS[1])
        self.assertTrue(self.tab.upscaler_var.get())
        self.assertFalse(self.tab.frame_gen_var.get())
        self.assertTrue(self.tab.sr_body.winfo_manager())
        self.tab.apply_injection();self.pump();self.confirm_preview();self.pump();self.wait_for_scan()
        self.assertEqual(self.tab.confirm_route_btn.cget('state'),'normal')
        self.tab.sharpness_slider.set(.8);self.tab._on_pipeline_param_changed()
        self.assertEqual(self.tab.confirm_route_btn.cget('state'),'disabled')

    def test_preview_uses_file_table_and_fits_scaled_screen(self):
        from gui.install_preview import show_preview
        from core.install_plan import build_plan
        from core.detector import GameDetector
        from tkinter import ttk
        self.tab.upscaler_var.set(True)
        plan=build_plan(GameDetector.analyze_game(self.profile['target_exe']),self.tab._settings(),self.tab.injector.version_manager)
        self.app._apply_scale('200%')
        dialog=show_preview(self.tab,plan,False,lambda:None)
        self.app.update()
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        table=next(w for w in descendants(dialog) if isinstance(w,ttk.Treeview))
        self.assertEqual(len(table.get_children()),len(plan['file_actions']))
        self.assertLessEqual(dialog.winfo_height(),dialog.winfo_screenheight())
        dialog.destroy()

    def test_launch_uses_full_desktop_layout_without_manual_resize(self):
        # Do not set geometry: this must exercise MainWindow's actual launch size.
        # mainloop performs Windows titlebar initialization that update() skips.
        self.app.after(1300,self.app.quit)
        self.app.mainloop()
        self.assertEqual(self.app.state(),'normal')
        self.assertTrue(self.app.winfo_viewable())
        self.assertGreaterEqual(self.app.winfo_width()/self.app._get_widget_scaling(),980)
        self.assertTrue(self.app.sidebar_frame.winfo_manager())
        self.assertTrue(self.app.library_tab.winfo_manager())
        self.assertFalse(self.app.compact_nav.winfo_manager())

    def test_bad_backup_still_offers_recovery(self):
        folder=Path(self.profile['target_dir'])/'.optiscaler_backup';folder.mkdir()
        (folder/'manifest.json').write_text('{invalid json')
        self.tab.rescan();self.wait_for_scan()
        self.assertEqual(self.tab.health['state'],'recovery_required')
        self.assertTrue(self.tab.recovery_btn.winfo_manager())
