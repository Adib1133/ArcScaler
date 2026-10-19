"""Release-backed controls and asynchronous per-game deployment workflows."""
import os
import threading
from pathlib import Path
from core.detector import GameDetector
from core.game_support import capabilities
from core.install_plan import build_plan, format_plan
from tkinter import messagebox
import customtkinter as ctk
from gui.theme import divider
from gui.theme import ContentFrame
from core.config_generator import ConfigGenerator
from core.safety import SafetyManager, CrashWatchdog
from gui.dispatch import UIDispatcher


class ConfigTab(ctk.CTkFrame):
    def __init__(self, master, injector, game_library, log_callback, on_profile_updated=None, hw_info=None, **kwargs):
        super().__init__(master, **kwargs)
        self.injector, self.library, self.log = injector, game_library, log_callback
        self.on_profile_updated = on_profile_updated
        self.hw_info = hw_info
        self.current_profile = None
        self.active_watchdog = None
        self.busy = False
        self.dispatcher = UIDispatcher(self)
        self._loading = False
        self._analysis = None
        self.preview_plan = None
        self.workers = []
        from core.tasks import TaskManager
        self.tasks = TaskManager(self.dispatcher, workers=2)
        self.recommendation = None
        self.health = None
        self._evidence = None
        self.runtime_state = None
        self._plan_timer = None
        self._view_timer = None
        self._scan_pending = False
        self._closed = False
        self._build_ui()

    def _build_ui(self):
        from gui.theme import BG,PANEL,BORDER,MUTED,BLUE,label,button,card
        from core.settings import SCHEMA
        import tkinter as tk
        self.configure(fg_color=BG,corner_radius=0)
        self.variables={}
        for key,spec in SCHEMA.items():
            value='auto' if spec.default is None else spec.default
            self.variables[key]=(ctk.BooleanVar(value=value) if spec.kind is bool and spec.default is not None else ctk.StringVar(value=str(value)))
        aliases={'mode_var':'installation_mode','version_var':'optiscaler_version','hook_var':'hook_method',
                 'quality_var':'xess_quality','upscaler_var':'upscaler_enabled','frame_gen_var':'frame_gen_enabled',
                 'spoof_var':'gpu_spoofing','reflex_var':'reflex_to_xell_enabled','fg_input_var':'fg_input',
                 'starting_upscaler_var':'starting_upscaler','model_var':'xess_network_model',
                 'invert_depth_var':'invert_depth','jitter_var':'jitter_cancellation'}
        for alias,key in aliases.items():setattr(self,alias,self.variables[key])
        self.custom_scale_enabled_var=ctk.BooleanVar(value=False)
        bar=ctk.CTkFrame(self,fg_color=PANEL,corner_radius=0)
        bar.pack(side='bottom',fill='x')
        divider(bar).pack(fill='x')
        self.action_hint_lbl=label(bar,'Select a game to configure.',muted=True,anchor='w',wraplength=480,justify='left')
        self.action_hint_lbl.pack(fill='x',padx=18,pady=(3,0))
        actions=ctk.CTkFrame(bar,fg_color='transparent');actions.pack(fill='x',padx=18,pady=(3,12))
        self.revert_btn=button(actions,'Restore original files',self.revert_changes,width=150)
        self.revert_btn.pack(side='left',padx=(0,8))
        self.progress=ctk.CTkProgressBar(bar,height=3,mode='indeterminate');self.progress.pack(fill='x');self.progress.set(0)
        self.apply_btn=button(actions,'Install to game',self.apply_injection,primary=True,width=110);self.apply_btn.pack(side='right')
        self.launch_btn=button(actions,'Launch',self.launch_and_protect,width=95);self.launch_btn.pack(side='right',padx=8)
        self.draft_lbl=label(bar,'Draft saved',muted=True);self.draft_lbl.pack(anchor='w',padx=18,before=actions)
        self.scroll=ContentFrame(self,fg_color=BG,corner_radius=0)
        self.scroll.pack(fill='both',expand=True)
        header=ctk.CTkFrame(self.scroll,fg_color='transparent');header.pack(fill='x',padx=22,pady=(20,14))
        self.game_icon=label(header,'',width=48,height=48);self.game_icon.pack(side='left',padx=(0,12))
        heading=ctk.CTkFrame(header,fg_color='transparent');heading.pack(side='left',fill='x',expand=True)
        self.title_lbl=label(heading,'Select a game',wraplength=300,justify='left');self.title_lbl.configure(font=ctk.CTkFont(size=22,weight='bold'));self.title_lbl.pack(anchor='w')
        self.info_lbl=label(heading,'Add a game from the library.',muted=True,anchor='w');self.info_lbl.pack(anchor='w')
        self.menu_button=button(header,'More',self._game_menu,width=60)
        self.menu_button.pack(side='right')
        overview=ctk.CTkFrame(self.scroll,fg_color='transparent');overview.pack(fill='x',padx=22,pady=(0,12))
        status=ctk.CTkFrame(overview,fg_color=PANEL,border_color=BORDER,border_width=1,corner_radius=14);status.pack(side='left')
        self.status_lbl=label(status,'●  Not installed');self.status_lbl.configure(text_color='#FF7887');self.status_lbl.pack(padx=10)
        self.controls={}
        self.health_lbl=label(self.scroll,'',muted=True,wraplength=480,justify='left',anchor='w');self.health_lbl.pack(fill='x',padx=22,pady=(0,8))
        self.recovery_btn=button(self.scroll,'Review recovery / restore originals',self.revert_changes)
        self.section_switches=[]
        self.section_notes={}
        def section(title,subtitle,variable,mode):
            box=card(self.scroll);box.pack(fill='x',padx=22,pady=(0,14))
            top=ctk.CTkFrame(box,fg_color='transparent');top.pack(fill='x',padx=18,pady=14)
            titles=ctk.CTkFrame(top,fg_color='transparent');titles.pack(side='left')
            title_widget=label(titles,title);title_widget.configure(font=ctk.CTkFont(size=14,weight='bold'));title_widget.pack(anchor='w')
            note=label(titles,subtitle,muted=True,wraplength=300,justify='left');note.pack(anchor='w')
            self.section_notes[mode]=note
            switch=ctk.CTkSwitch(top,text='On',width=60,variable=variable,progress_color=BLUE,command=lambda:self._toggle_mode(mode))
            switch.pack(side='right');self.section_switches.append(switch)
            divider(box).pack(fill='x')
            body=ctk.CTkFrame(box,fg_color='transparent');body.pack(fill='x',padx=18,pady=16)
            return body
        suggestions=card(self.scroll);suggestions.pack(fill='x',padx=22,pady=(0,14))
        heading=label(suggestions,'Choose setup');heading.configure(font=ctk.CTkFont(size=15,weight='bold'));heading.pack(anchor='w',padx=18,pady=(14,4))
        from core.workflows import GOALS
        self.goal_menu=ctk.CTkOptionMenu(suggestions,values=list(GOALS),command=self._choose_goal)
        self.goal_menu.set('Advanced configuration');self.goal_menu.pack(fill='x',padx=18,pady=6)
        self.route_lbl=label(suggestions,'Select a game to choose a route.',muted=True,wraplength=470,justify='left',anchor='w')
        self.route_lbl.pack(fill='x',padx=18,pady=6)
        button(suggestions,'Suggested settings',lambda:self._toggle_panel(self.suggestion_details)).pack(anchor='w',padx=18,pady=(0,10))
        self.suggestion_details=ctk.CTkFrame(suggestions,fg_color='transparent')
        self.suggestion_lbl=label(self.suggestion_details,'Select a game to see recommendations.',muted=True,wraplength=470,justify='left')
        self.suggestion_lbl.pack(anchor='w',padx=18,pady=6)
        self.suggest_btn=button(self.suggestion_details,'Apply suggestion to draft',self._use_suggestions)
        self.suggest_btn.pack(anchor='w',padx=18,pady=(4,14))
        self.sr_body=section('Super Resolution','Improve image detail with XeSS',self.upscaler_var,'upscaler_enabled')
        self.gauge=tk.Canvas(self.sr_body,width=135,height=155,bg=PANEL,highlightthickness=0)
        self.gauge.pack(side='left',anchor='n',padx=(0,12))
        options=ctk.CTkFrame(self.sr_body,fg_color='transparent');options.pack(side='left',fill='x',expand=True)
        self.sr_options=options
        def menu(parent,key):
            spec=SCHEMA[key]
            row=ctk.CTkFrame(parent,fg_color='transparent');row.pack(fill='x',pady=6)
            label(row,spec.label).pack(side='left')
            values=['auto' if v is None else str(v).lower() if type(v) is bool else str(v) for v in spec.options]
            widget=ctk.CTkOptionMenu(row,variable=self.variables[key],values=values or ['No installed releases'],
                width=145,height=30,corner_radius=12,fg_color='#242830',button_color='#30363F',button_hover_color=BORDER,
                command=self._on_pipeline_param_changed)
            widget.pack(side='right');self.controls[key]=widget
            return widget
        menu(options,'xess_quality')
        self.quality_segments=ctk.CTkSegmentedButton(options,
            values=[v for v in SCHEMA['xess_quality'].options if v in ('Ultra Quality','Quality','Balanced','Performance')],
            variable=self.quality_var,command=self._on_pipeline_param_changed,
            selected_color='#344A64',unselected_color='#22262D',corner_radius=10,font=ctk.CTkFont(size=11))
        self.quality_segments.pack(fill='x',pady=(0,8))
        self.sr_details_button=button(options,'Fine-tune resolution and sharpness',lambda:self._toggle_panel(self.sr_details))
        self.sr_details_button.pack(fill='x',pady=8)
        self.sr_details=ctk.CTkFrame(options,fg_color='transparent')
        options=self.sr_details
        ctk.CTkSwitch(options,text='Custom render scale',variable=self.custom_scale_enabled_var,
            command=self._on_pipeline_param_changed,progress_color=BLUE).pack(anchor='w',pady=10)
        self.scale_val_lbl=label(options,'Render scale',muted=True,anchor='w');self.scale_val_lbl.pack(fill='x')
        self.scale_slider=ctk.CTkSlider(options,from_=.25,to=1,progress_color=BLUE,command=self._on_scale_slider)
        self.scale_slider.set(.67);self.scale_slider.pack(fill='x',pady=8)
        self.sharpness_val_lbl=label(options,'Sharpness',muted=True,anchor='w');self.sharpness_val_lbl.pack(fill='x')
        self.sharpness_slider=ctk.CTkSlider(options,from_=0,to=1,progress_color=BLUE,command=self._on_sharpness_slider)
        self.sharpness_slider.set(.3);self.sharpness_slider.pack(fill='x',pady=8)
        menu(options,'starting_upscaler')
        label(options,'Hardware is chosen automatically',muted=True).pack(anchor='w')
        self.fg_body=section('Frame Generation','DirectX 12 only',self.frame_gen_var,'frame_gen_enabled')
        menu(self.fg_body,'fg_input')
        self.fg_description=label(self.fg_body,'',muted=True,wraplength=480,justify='left');self.fg_description.pack(anchor='w',pady=6)
        self.native_body=card(self.scroll);self.native_body.pack(fill='x',padx=22,pady=(0,14))
        label(self.native_body,'Reuse native XeSS').pack(anchor='w',padx=18,pady=(14,4))
        self.native_note=label(self.native_body,
            'DirectX 12: reuse existing XeSS libraries, or keep native upscaling and add missing FG libraries. '
            'Only missing runtimes and required helpers are installed. Compatibility must be verified in game.',
            muted=True,wraplength=470,justify='left')
        self.native_note.pack(fill='x',padx=18,pady=6)
        self.controls['runtime_method']=ctk.CTkOptionMenu(self.native_body,
            variable=self.variables['runtime_method'],values=list(SCHEMA['runtime_method'].options),
            command=self._native_method_changed)
        self.controls['runtime_method'].pack(fill='x',padx=18,pady=8)
        self.native_paths=ctk.CTkFrame(self.native_body,fg_color='transparent')
        self.native_rows={}
        for key in ('native_xess_path','native_xefg_path','native_xell_path'):
            group=ctk.CTkFrame(self.native_paths,fg_color='transparent');group.pack(fill='x')
            self.native_rows[key]=group
            label(group,SCHEMA[key].label,muted=True).pack(anchor='w',padx=18)
            row=ctk.CTkFrame(group,fg_color='transparent');row.pack(fill='x',padx=18,pady=(2,8))
            entry=ctk.CTkEntry(row,textvariable=self.variables[key],placeholder_text='Auto-detect when blank')
            entry.pack(side='left',fill='x',expand=True)
            entry.bind('<FocusOut>',self._on_pipeline_param_changed)
            entry.bind('<Return>',self._on_pipeline_param_changed)
            self.controls[key]=entry
            button(row,'Browse',lambda k=key:self._browse_native_runtime(k),width=65).pack(side='right',padx=(6,0))
        label(self.native_paths,'Blank paths use detected files. Multiple matches require a selection. '
            'Use Install to game below to review the exact file list; Restore original files reverses deployment.',
            muted=True,wraplength=470,justify='left').pack(fill='x',padx=18,pady=(0,14))
        evidence=card(self.scroll);evidence.pack(fill='x',padx=22,pady=(0,14))
        label(evidence,'Runtime evidence').pack(anchor='w',padx=18,pady=(14,4))
        self.runtime_lbl=label(evidence,'Runtime not verified',muted=True,wraplength=470,justify='left')
        self.runtime_lbl.pack(fill='x',padx=18,pady=6)
        self.confirm_route_btn=button(evidence,'Confirm route seen in overlay',self._confirm_runtime)
        self.confirm_route_btn.pack(anchor='w',padx=18,pady=6)
        self.evidence_rows={}
        for key,title in [('dlss','DLSS'),('fsr','FSR / FidelityFX'),('xess','XeSS upscaling'),('dlssg','DLSS Frame Generation'),('fsrfg','FSR Frame Generation'),('xefg','XeSS Frame Generation')]:
            item=label(evidence,title+' · Awaiting scan',anchor='w')
            item.pack(fill='x',padx=18,pady=2);self.evidence_rows[key]=(title,item)
        button(evidence,'View detected files and origins',lambda:self._toggle_panel(self.evidence_details)).pack(fill='x',padx=18,pady=8)
        self.evidence_details=ctk.CTkFrame(evidence,fg_color='transparent')
        self.evidence_files=label(self.evidence_details,'',muted=True,wraplength=470,justify='left')
        self.evidence_files.pack(anchor='w',padx=18,pady=8)
        label(evidence,'DLL presence is evidence, not proof of native feature availability or the active graphics API.',muted=True,wraplength=470,justify='left').pack(anchor='w',padx=18,pady=(0,14))
        compatibility=card(self.scroll);compatibility.pack(fill='x',padx=22,pady=(0,14))
        button(compatibility,'Advanced settings',self._toggle_compatibility).pack(fill='x',padx=10,pady=10)
        self.compatibility_body=ctk.CTkFrame(compatibility,fg_color='transparent')
        self.force_btn=button(self.compatibility_body,'Force inject (advanced)',self.force_injection,width=170)
        self.force_btn.pack(anchor='w',pady=8)
        self.undo_btn=button(self.compatibility_body,'Undo last change',self.undo_last_change,width=170)
        self.undo_btn.pack(anchor='w',pady=8)
        menu(self.compatibility_body,'xess_network_model')
        for key,spec in SCHEMA.items():
            if spec.mode!='compatibility':continue
            if spec.kind is bool and spec.default is not None:
                ctk.CTkSwitch(self.compatibility_body,text=spec.label,variable=self.variables[key],command=self._on_pipeline_param_changed).pack(anchor='w',pady=8)
            else:menu(self.compatibility_body,key)
        self.version_dropdown=self.controls['optiscaler_version']
        self.plan_lbl=label(self.compatibility_body,'',muted=True,wraplength=500,justify='left');self.plan_lbl.pack(fill='x',pady=10)
        self.research=None;self.support=None;self.research_generation=0
        self._refresh_labels();self._update_pipeline_view()
        self.bind('<Configure>',self._resize_labels)

    def _resize_labels(self,event):
        if event.widget is not self:return
        width=max(220,int(event.width/self._get_widget_scaling())-88)
        for widget in (self.info_lbl,self.health_lbl,self.suggestion_lbl,self.evidence_files,self.plan_lbl,self.fg_description,self.action_hint_lbl,self.native_note,self.route_lbl,self.runtime_lbl):widget.configure(wraplength=width)
        self.title_lbl.configure(wraplength=max(160,width-130))
        compact=width<510
        if compact!=getattr(self,'_compact_sr',None):
            self._compact_sr=compact
            self.gauge.pack_forget();self.quality_segments.pack_forget()
            if not compact:
                self.gauge.pack(side='left',anchor='n',padx=(0,12),before=self.sr_options)
                self.quality_segments.pack(fill='x',pady=(0,8),before=self.sr_details_button)

    def _toggle_panel(self,panel):
        if panel.winfo_manager():panel.pack_forget()
        else:panel.pack(fill='x',padx=8,pady=(0,12))
        self._remember_view()

    def _remember_view(self):
        if not self.current_profile or self._loading:return
        view=dict(scroll=self.scroll._parent_canvas.yview()[0],advanced=bool(self.compatibility_body.winfo_manager()),
                  evidence=bool(self.evidence_details.winfo_manager()),fine_tune=bool(self.sr_details.winfo_manager()))
        self.library.update_profile(self.current_profile['id'],{'ui_view':view})

    def _restore_view(self,profile):
        view=profile.get('ui_view',{})
        for key,panel in [('advanced',self.compatibility_body),('evidence',self.evidence_details),('fine_tune',self.sr_details)]:
            panel.pack_forget()
            if view.get(key):panel.pack(fill='x',padx=8,pady=(0,12))
        game_id=profile['id']
        def restore():
            if not self._closed and self.current_profile and self.current_profile['id']==game_id:
                self.scroll._parent_canvas.yview_moveto(float(view.get('scroll',0)))
        if self._view_timer:self.after_cancel(self._view_timer)
        self._view_timer=self.after(100,restore)

    def _toggle_compatibility(self):
        self._toggle_panel(self.compatibility_body)

    def _installation_status(self,installed):
        from gui.theme import GREEN,RED
        self.status_lbl.configure(text='●  Installed' if installed else '●  Not installed',text_color=GREEN if installed else RED)

    def _suggested_settings(self):
        return dict(self.recommendation['settings']) if self.recommendation else {}

    def _show_evidence(self,analysis=None):
        from gui.theme import GREEN,MUTED
        from core.game_support import runtime_evidence
        for key,(title,item) in self.evidence_rows.items():
            present=bool(self.support and self.support.get(key))
            uncertain=key=='fsrfg' and self.support and self.support.get('fsrfg_uncertain') and not present
            suffix='Detected' if present else 'Uncertain' if uncertain else 'Not detected' if analysis else 'Scanning…'
            item.configure(text=('●  ' if present else '○  ')+title+' · '+suffix,text_color=GREEN if present else MUTED)
        self.suggest_btn.configure(state='normal' if self.recommendation and self.recommendation['ready'] and not self.busy else 'disabled')
        if not analysis:
            self.evidence_files.configure(text='Scanning local runtime files…')
            self.suggestion_lbl.configure(text='Checking this game for a suggested preset…');return
        rows=self._evidence if self._evidence is not None else runtime_evidence(analysis)
        self.evidence_files.configure(text='\n\n'.join(r['filename']+' · '+r['origin']+'\n'+r['path']+'\n'+r['confidence'] for r in rows) or 'No runtime DLLs found.')
        if self.support is None:
            self.suggestion_lbl.configure(text=analysis.get('error','Could not analyze this game. Use More → Rescan.'));return
        if not self.recommendation:
            from core.recommendations import recommend
            self.recommendation=recommend(analysis,self._settings(),self.injector.version_manager,self.hw_info)
        rec=self.recommendation;values=rec['settings']
        summary='Super Resolution: '+('On · '+values['starting_upscaler']+' · '+values['xess_quality'] if values['upscaler_enabled'] else 'Use native XeSS' if self.support['xess'] else 'Off')
        summary+='\nFrame Generation: '+('On · '+values['fg_input'] if values['frame_gen_enabled'] else 'Off')
        summary+='\n'+'\n'.join(note.split(' Upscaler-driven')[0] for note in rec['notes'])
        self.evidence_files.configure(text=self.evidence_files.cget('text')+'\n\n'+'\n'.join(rec['notes']))
        if rec['errors']:summary+='\nNeeds attention: '+'; '.join(rec['errors'])
        self.suggestion_lbl.configure(text=summary.strip())
        self.suggest_btn.configure(state='normal' if rec['ready'] and not self.busy else 'disabled')

    def _toggle_mode(self,key):
        self._on_pipeline_param_changed()

    def show_empty(self,library_tab):
        from gui.theme import BG,BLUE,label,button
        if hasattr(self,'empty_state'):self.empty_state.destroy()
        self.empty_state=ctk.CTkFrame(self,fg_color=BG,corner_radius=0)
        self.empty_state.place(relx=0,rely=0,relwidth=1,relheight=1)
        center=ctk.CTkFrame(self.empty_state,fg_color='transparent')
        center.place(relx=.5,rely=.47,anchor='center')
        import tkinter as tk
        mark=tk.Canvas(center,width=42,height=42,bg=BG,highlightthickness=0);mark.pack()
        mark.create_arc(7,6,35,34,start=-35,extent=250,style='arc',outline='#22BDF2',width=4)
        mark.create_oval(18,23,24,29,fill='#22BDF2',outline='')
        heading=label(center,'Add your first game');heading.configure(font=ctk.CTkFont(size=20,weight='bold'));heading.pack(pady=(12,6))
        label(center,"Scan Steam and Epic, add a folder, or pick a game's\n.exe. ArcScaler only changes a game when you press\nInstall.",muted=True,justify='center').pack()
        actions=ctk.CTkFrame(center,fg_color='transparent');actions.pack(pady=22)
        button(actions,'Auto-scan Steam & Epic',library_tab._auto_scan,primary=True,width=180).pack(side='left',padx=4)
        button(actions,'+  Add game (.exe / .lnk)',library_tab._add_game_file,width=174).pack(side='left',padx=4)
        button(actions,'Add folder',library_tab._add_game_folder,width=100).pack(side='left',padx=4)

    def _game_menu(self):
        from gui.theme import BORDER,button
        if not self.current_profile or self.busy:return
        if hasattr(self,'game_menu') and self.game_menu.winfo_exists():
            self.game_menu.destroy();return
        menu=ctk.CTkFrame(self,fg_color='#171C24',border_color=BORDER,border_width=1,corner_radius=14)
        self.game_menu=menu
        y=self.menu_button.winfo_rooty()-self.winfo_rooty()+self.menu_button.winfo_height()+8
        menu.place(relx=1,x=-24,y=max(8,y),anchor='ne');menu.lift()
        def invoke(action):
            menu.destroy();action()
        for text,action in [('Open game folder',self.open_game_folder),('Rescan',self.rescan),('Verify installed files',self.verify),('Undo last change',self.undo_last_change),('Restore original files',self.revert_changes),('Export diagnostics',self.export_diagnostics),('Force inject (advanced)',self.force_injection),('Remove from library',self.remove_game)]:
            item=button(menu,text,lambda fn=action:invoke(fn),width=240,anchor='w')
            item.configure(fg_color='transparent',border_width=0,text_color='#FFB52E' if action==self.remove_game else '#EEF3FA')
            if action in (self.verify,self.revert_changes) and (self.health or {}).get('state')=='not_installed':item.configure(state='disabled')
            item.pack(fill='x',padx=10,pady=5)
        menu.bind('<Escape>',lambda _:menu.destroy());menu.focus_set()
        menu.bind('<FocusOut>',lambda _:self.after(100,lambda:menu.destroy() if menu.winfo_exists() and not str(self.focus_get()).startswith(str(menu)) else None))

    def verify(self):
        if self.current_profile:
            folder=self.current_profile['target_dir']
            dirs=self.current_profile.get('recovery_dirs',self.current_profile.get('all_target_dirs',[]))
            def done(result):
                self.health=result;self._update_pipeline_view()
                messagebox.showinfo('Installation health',result.get('message',result.get('error','')))
            self._run(lambda:self.injector.verify_installation(folder,dirs),done)

    def remove_game(self):
        if self.current_profile and not self.busy and messagebox.askyesno('Remove game','Remove this profile from the library? Installed game files will be retained.'):
            self.research_generation+=1;self.tasks.cancel('profile-scan')
            if self._plan_timer:self.after_cancel(self._plan_timer);self._plan_timer=None
            self.library.remove_game(self.current_profile['id']);self.current_profile=None
            self.title_lbl.configure(text='Select a game');self._update_pipeline_view()
            if self.on_profile_updated:self.on_profile_updated()

    def update_version_list(self,refresh=True):
        if refresh or not hasattr(self,'_release_values'):
            self._release_values=self.injector.version_manager.get_installed_versions()
        installed=self._release_values
        self.version_dropdown.configure(values=installed or ['No installed releases'])
        if not self.version_var.get() and installed:
            self.version_var.set(installed[0])

    def load_game(self, profile):
        self._remember_view()
        profile=dict(self.library.profiles.get(profile['id'],profile))
        self.tasks.cancel('profile-scan')
        if self._plan_timer:self.after_cancel(self._plan_timer);self._plan_timer=None
        if hasattr(self,'empty_state'):self.empty_state.destroy()
        if hasattr(self,'game_menu') and self.game_menu.winfo_exists():self.game_menu.destroy()
        self._loading = True
        self.current_profile = dict(profile)
        self._analysis = None
        self.preview_plan = None
        self.recommendation=None;self.health=None;self._evidence=None;self._draft_error=None
        self.update_version_list(refresh=False)
        self.title_lbl.configure(text=profile.get('name', 'Game'))
        info = f"{profile.get('engine', '')}\n{profile.get('target_dir', '')}"
        if profile.get('anti_cheat'):
            info += f"\n{profile['anti_cheat']} detected. Use only a supported offline game configuration."
        self.info_lbl.configure(text='Selected release: '+(profile.get('optiscaler_version') or self.version_var.get() or 'None'))
        self._installation_status(profile.get('is_injected'))
        if hasattr(self,'icons'):
            game_id=profile['id']
            def received(image):
                if self.current_profile and self.current_profile['id']==game_id:
                    artwork=ctk.CTkImage(image,size=(48,48));self.game_icon.configure(image=artwork);self.game_icon.artwork=artwork
            self.icons.request(profile['target_exe'],profile['name'],received)
        from core.settings import migrate, SCHEMA
        restored=migrate(profile)
        for key,spec in SCHEMA.items():
            value=restored[key]
            if key=='optiscaler_version' and not value:value=self.version_var.get()
            if spec.default is None:value='auto' if value is None else str(value).lower() if isinstance(value,bool) else str(value)
            self.variables[key].set(value)
        scale = profile.get('custom_scale')
        self.custom_scale_enabled_var.set(scale is not None)
        self.scale_slider.set(scale if scale is not None else 0.67)
        self.sharpness_slider.set(profile.get('sharpness', 0.3))
        self._loading = False
        self._refresh_labels()
        self.support = None
        self.research = None
        self._update_pipeline_view()
        self._show_evidence()
        self._restore_view(profile)
        self._research_game(dict(profile))

    def _refresh_labels(self):
        self.scale_val_lbl.configure(text=f'Render scale: {self.scale_slider.get():.2f}x')
        self.sharpness_val_lbl.configure(text=f'Sharpness: {self.sharpness_slider.get():.2f}')
        scale=self.scale_slider.get() if self.custom_scale_enabled_var.get() else 1/ConfigGenerator.QUALITY_RATIOS.get(self.quality_var.get(),1.5)
        signature=(round(scale,4),self.upscaler_var.get())
        if signature==getattr(self,'_gauge_signature',None):return
        self._gauge_signature=signature
        self.gauge.delete('all')
        from PIL import ImageTk
        from gui.theme import gauge_image
        self.gauge_art=ImageTk.PhotoImage(gauge_image(scale,self.upscaler_var.get()))
        self.gauge.create_image(0,0,image=self.gauge_art,anchor='nw')
        self.gauge.create_text(68,64,text=f'{scale:.2f}×',fill='#EEF3FA' if self.upscaler_var.get() else '#515967',font=('Segoe UI',24,'bold'))
        self.gauge.create_text(68,89,text='render scale',fill='#8B9CB6' if self.upscaler_var.get() else '#515967',font=('Segoe UI',10))

    def _settings(self):
        from core.settings import SCHEMA
        values={}
        for key,spec in SCHEMA.items():
            value=self.variables[key].get()
            if value=='auto' and spec.default is None:value=None
            elif spec.kind is bool and isinstance(value,str):value=value=='true'
            elif spec.kind is int and value is not None:value=int(value)
            elif spec.kind is float and value is not None:value=float(value)
            values[key]=value
        values['custom_scale']=self.scale_slider.get() if self.custom_scale_enabled_var.get() else None
        values['sharpness']=self.sharpness_slider.get()
        return dict(values,settings_schema=3)

    def _save_current_settings_to_profile(self):
        if self.current_profile and not self._loading:
            settings = self._settings()
            try:
                self.library.update_profile(self.current_profile['id'], settings)
                self.current_profile.update(settings);self._draft_error=None
            except OSError as exc:
                self._draft_error=str(exc);self.log('Draft could not be saved: '+str(exc),'error')

    def _on_pipeline_param_changed(self, *args):
        self._save_current_settings_to_profile()
        self._schedule_research()
        self._update_pipeline_view()

    def _native_method_changed(self, value):
        from core.native_runtimes import BUNDLED, REUSE
        if value != BUNDLED:
            self.upscaler_var.set(value == REUSE)
            self.frame_gen_var.set(True)
            self.starting_upscaler_var.set('DLSS')
            self.fg_input_var.set('dlssg')
        self._on_pipeline_param_changed()

    def _choose_goal(self, goal):
        if not self.current_profile or self.busy:return
        from core.workflows import goal_settings, GOALS
        if goal == GOALS[-1]:
            if not self.compatibility_body.winfo_manager():self._toggle_compatibility()
            return
        values=goal_settings(goal,self._settings(),bool((self.support or {}).get('xess')))
        for key in ('runtime_method','upscaler_enabled','frame_gen_enabled','starting_upscaler','fg_input'):
            self.variables[key].set(values[key])
        self._on_pipeline_param_changed()

    def _confirm_runtime(self):
        if self.busy or not self.current_profile or (self.health or {}).get('state') != 'installed':return
        if not getattr(self, '_draft_matches', False):return
        signature=(self.runtime_state or {}).get('signature')
        if not signature:return
        if messagebox.askyesno('Confirm active route','Have you checked the OptiScaler overlay in this game and verified the input and XeSS output shown in the route summary?'):
            import time
            value={'signature':signature,'confirmed_at':time.time()}
            self.library.update_profile(self.current_profile['id'],{'runtime_confirmation':value})
            self.current_profile['runtime_confirmation']=value
            self.rescan()

    def undo_last_change(self):
        if self.busy or not self.current_profile:return
        profile=dict(self.current_profile)
        if not messagebox.askyesno('Undo last change','Restore the previous deployment? Original-game backups remain available. This does not certify the previous setup worked in game.'):return
        def done(result):
            if result.get('success'):
                health=self.injector.verify_installation(profile['target_dir'],profile.get('recovery_dirs',[]))
                values=health.get('requested_settings') or health.get('settings') or {}
                from core.settings import SCHEMA
                self.library.update_profile(profile['id'],{k:v for k,v in values.items() if k in SCHEMA})
                self.load_game(self.library.profiles[profile['id']])
            else:messagebox.showerror('Undo failed',result.get('error','Unknown error'))
            self.log(result.get('message',result.get('error','')), 'success' if result.get('success') else 'error')
        self._run(lambda:self.injector.undo_last_change(profile['target_dir'],profile['target_exe'],
            profile.get('recovery_dirs',profile.get('all_target_dirs',[]))),done)

    def _browse_native_runtime(self, key):
        from tkinter import filedialog
        if not self.current_profile or self.busy:return
        path=filedialog.askopenfilename(parent=self.winfo_toplevel(),title='Select existing game runtime',
            initialdir=self.current_profile.get('base_dir') or self.current_profile['target_dir'],
            filetypes=[('Windows DLL','*.dll')])
        if path:
            self.variables[key].set(path)
            self._on_pipeline_param_changed()

    def _update_pipeline_view(self):
        from gui.theme import enabled_tree
        sr,fg=self.upscaler_var.get(),self.frame_gen_var.get()
        enabled_tree(self.sr_body,sr and not self.busy)
        enabled_tree(self.fg_body,fg and not self.busy)
        for body, enabled in ((self.sr_body,sr),(self.fg_body,fg)):
            if enabled and not body.winfo_manager():body.pack(fill='x',padx=18,pady=16)
            elif not enabled:body.pack_forget()
        enabled_tree(self.compatibility_body,not self.busy)
        enabled_tree(self.native_body,not self.busy and bool(self.current_profile))
        self.goal_menu.configure(state='normal' if self.current_profile and not self.busy else 'disabled')
        from core.workflows import current_goal
        self.goal_menu.set(current_goal(self._settings()))
        from core.workflows import route_summary
        self.route_lbl.configure(text=route_summary(self._settings(),self.preview_plan if not self._scan_pending else None))
        for key, group in self.native_rows.items():
            visible=key=='native_xess_path' or fg or (key=='native_xell_path' and (self.spoof_var.get() or self.reflex_var.get()))
            if visible and not group.winfo_manager():group.pack(fill='x')
            elif not visible:group.pack_forget()
        if self.variables['runtime_method'].get() != 'Bundled runtimes':
            if not self.native_paths.winfo_manager():self.native_paths.pack(fill='x')
        else:self.native_paths.pack_forget()
        for switch in self.section_switches:switch.configure(state='disabled' if self.busy else 'normal',text='On' if switch.get() else 'Off')
        self.scale_slider.configure(state='normal' if sr and self.custom_scale_enabled_var.get() and not self.busy else 'disabled')
        if not self.custom_scale_enabled_var.get():
            self.scale_slider.configure(progress_color='#444B57',button_color='#555D69')
        self.fg_description.configure(text={'dlssg':"Native DLSSG. Uses the game's frame-generation input.",
            'fsrfg':'FSR 3.1 frame-generation input.', 'fsrfg30':'FSR 3.0 frame-generation input.'}.get(self.fg_input_var.get(),''))
        self.section_notes['upscaler_enabled'].configure(text='XeSS upscaling · selected for installation' if sr else 'Turn on to edit quality and resolution.')
        api=(self._analysis or {}).get('graphics_api','Unknown')
        self.section_notes['frame_gen_enabled'].configure(text='Requires DirectX 12 · current API evidence: '+api if api!='DX12' else 'DirectX 12 evidence present · verify in game')
        if not fg:self.fg_description.configure(text='Turn on Frame Generation to edit its input. SR can stay enabled at the same time.')
        self.apply_btn.configure(state='normal' if self.current_profile and (sr or fg) and not self.busy else 'disabled')
        self.force_btn.configure(state='normal' if self.current_profile and (sr or fg) and not self.busy else 'disabled')
        self.action_hint_lbl.configure(text='' if sr or fg else 'Turn on Super Resolution or Frame Generation to install.')
        self._refresh_action_state()
        self.runtime_lbl.configure(text=(self.runtime_state or {}).get('message','Runtime not verified') if (self.health or {}).get('state')=='installed' else 'Runtime confirmation unavailable until installed files verify')
        self.confirm_route_btn.configure(state='normal' if not self.busy and (self.health or {}).get('state')=='installed' and getattr(self,'_draft_matches',False) else 'disabled')
        self._refresh_labels()

    def _refresh_action_state(self):
        if not self.current_profile:return
        state=(self.health or {}).get('state','checking')
        titles={'installed':'Installed','modified':'Modified','incomplete':'Incomplete','recovery_required':'Recovery required','unverified':'Unverified','not_installed':'Not installed','checking':'Checking files…'}
        self.status_lbl.configure(text=('●  ' if state=='installed' else '○  ')+titles[state],text_color='#42D9A0' if state=='installed' else '#FF7887' if state=='not_installed' else '#FFBF69')
        installed=(self.health or {}).get('settings',{})
        baseline=(self.health or {}).get('requested_settings') or installed
        selected=self._settings()
        def equal(a,b):
            return abs(a-b)<0.00001 if type(a) in (float,int) and type(b) in (float,int) else a==b
        from core.settings import SCHEMA
        changed=not baseline or any(not equal(selected[k],baseline.get(k)) for k in SCHEMA)
        self._draft_matches = not changed
        self.draft_lbl.configure(text='Unsaved changes' if getattr(self,'_draft_error',None) else 'Changes not installed' if changed else 'Draft matches install',text_color='#FFBF69' if changed else '#42D9A0')
        blocked=state in ('recovery_required','modified','incomplete')
        self.apply_btn.configure(text='Up to date' if state=='installed' and not changed else 'Update installation' if state not in ('not_installed','checking') else 'Install to game',
                                 state='normal' if not self.busy and not blocked and (self.upscaler_var.get() or self.frame_gen_var.get()) and (changed or state!='installed') else 'disabled')
        self.launch_btn.configure(state='normal' if not self.busy and state=='installed' else 'disabled')
        self.force_btn.configure(state='normal' if not self.busy and state not in ('recovery_required','checking') and (self.upscaler_var.get() or self.frame_gen_var.get()) else 'disabled')
        self.revert_btn.configure(state='normal' if not self.busy and state not in ('not_installed','checking') else 'disabled')
        self.undo_btn.configure(state='normal' if not self.busy and (self.health or {}).get('can_undo') else 'disabled')
        self.suggest_btn.configure(state='normal' if not self.busy and self.recommendation and self.recommendation['ready'] else 'disabled')
        self.recovery_btn.pack_forget()
        if blocked:
            self.recovery_btn.pack(fill='x',padx=22,pady=(0,12),after=self.health_lbl)
            self.recovery_btn.configure(state='disabled' if self.busy else 'normal')
        detail=(self.health or {}).get('message','Checking installation health…')
        if installed:detail+=' · Installed release: '+installed.get('optiscaler_version','Unknown')
        if self._analysis:detail+='\nAPI evidence: '+self._analysis.get('graphics_api','Unknown')+' · DLLs do not prove active features.'
        self.health_lbl.configure(text=detail)
        self.info_lbl.configure(text='Selected release: '+(self.version_var.get() or 'None')+' · Draft saved automatically')
        if self.busy:self.action_hint_lbl.configure(text='Working… Please wait for file operations to finish.')
        elif getattr(self,'_draft_error',None):self.action_hint_lbl.configure(text='Draft not saved: '+self._draft_error)
        elif blocked:self.action_hint_lbl.configure(text='Review recovery before installing or launching.')
        elif self.upscaler_var.get() or self.frame_gen_var.get():self.action_hint_lbl.configure(text='Install applies the draft to game files.' if changed else 'Files verified. Confirm active features in the game overlay.')

    def _research_game(self,profile,refresh=True):
        from core.installation_state import installation_health
        from core.recommendations import recommend
        self.research_generation+=1;generation=self.research_generation
        self._scan_pending=True
        settings=self._settings();hardware=dict(self.hw_info or {})
        cached=None if refresh else self._analysis
        cached_health=None if refresh else self.health
        def operation():
            from core.game_support import runtime_evidence
            from core.runtime_status import runtime_status
            health=cached_health or installation_health(profile['target_dir'],profile.get('recovery_dirs',profile.get('all_target_dirs',[])))
            runtime=runtime_status(profile['target_dir'],profile.get('recovery_dirs',profile.get('all_target_dirs',[])),profile.get('runtime_confirmation'),health)
            try:
                analysis=cached or GameDetector.analyze_game(profile['target_exe'],profile.get('base_dir'))
                if not analysis.get('valid'):return analysis,None,None,None,health,[],runtime
                plan=build_plan(analysis,dict(settings,hardware=hardware),self.injector.version_manager)
                return analysis,plan,capabilities(analysis),recommend(analysis,settings,self.injector.version_manager,hardware),health,runtime_evidence(analysis),runtime
            except (OSError,ValueError,KeyError,TypeError) as exc:
                return {'valid':False,'error':str(exc)},None,None,None,health,[],runtime
        def done(result):
            if generation!=self.research_generation or not self.current_profile or self.current_profile['id']!=profile['id']:return
            analysis,plan,support,rec,health,evidence,runtime=result
            self.runtime_state=runtime
            self._scan_pending=False;self._analysis=analysis if analysis.get('valid') else None
            self.preview_plan=plan;self.support=support;self.recommendation=rec;self.health=health
            self._evidence=evidence
            self.plan_lbl.configure(text=format_plan(plan) if plan else analysis.get('error','Scan failed'))
            self._show_evidence(analysis);self._update_pipeline_view()
            self.library.update_profile(profile['id'],{'installation_health':health['state'],'is_injected':health['state']=='installed'})
            if self.on_profile_updated:self.on_profile_updated()
        def failed(error):
            if generation!=self.research_generation:return
            self._scan_pending=False;self.support=None;self.recommendation=None;self.preview_plan=None
            self.suggestion_lbl.configure(text='Scan failed: '+error+'\nChoose More → Rescan to retry.')
            self.suggest_btn.configure(state='disabled');self.log('Game analysis failed: '+error,'error')
            self._update_pipeline_view()
        self.tasks.submit('profile-scan',operation,done,failed)

    def _schedule_research(self):
        if not self.current_profile or self._loading or self.busy:return
        if self._plan_timer:self.after_cancel(self._plan_timer)
        self.research_generation+=1;self.tasks.cancel('profile-scan')
        self._scan_pending=True
        self._plan_timer=self.after(250,lambda:self._research_game(dict(self.current_profile),refresh=False))

    def rescan(self):
        if self.current_profile and not self.busy:self._research_game(dict(self.current_profile))

    def _use_suggestions(self):
        if not self.current_profile or self.busy:return
        if not self.recommendation or not self.recommendation['ready']:return
        values=self._suggested_settings()
        self.library.update_profile(self.current_profile['id'],values)
        self.load_game(self.library.profiles[self.current_profile['id']])

    def _on_scale_slider(self, value):
        self._on_pipeline_param_changed()

    def _on_sharpness_slider(self, value):
        self._on_pipeline_param_changed()

    def _run(self, operation, done):
        if self.busy:return
        self.busy=True
        self.tasks.cancel('profile-scan');self.research_generation+=1;self._scan_pending=False
        if self._plan_timer:self.after_cancel(self._plan_timer);self._plan_timer=None
        self.progress.start();self._update_pipeline_view()
        self.action_hint_lbl.configure(text='Working… Please wait for file operations to finish.')
        def finish(result):
            self.busy=False;self.progress.stop();self.progress.set(0)
            done(result)
            self._update_pipeline_view()
        self.tasks.submit('file-operation',operation,finish,lambda error:finish({'success':False,'error':error}),cancellable=False)

    def _record_result(self, profile, result):
        updates = {'is_injected': self.injector.is_injected(profile['target_dir']),
                   'has_backup': SafetyManager.has_active_backup(profile['target_dir'])}
        if result.get('success') and result.get('plan'):
            updates['last_installation_plan'] = result['plan']
            updates['installed_mode'] = 'sr+fg' if all(result['plan']['settings'][k] for k in ('upscaler_enabled','frame_gen_enabled')) else 'fg' if result['plan']['settings']['frame_gen_enabled'] else 'sr'
        self.library.update_profile(profile['id'], updates)
        if self.current_profile and self.current_profile['id'] == profile['id']:
            self.current_profile.update(updates)
            self._installation_status(updates['is_injected'])
            self.rescan()
        if self.on_profile_updated:
            self.on_profile_updated()

    def apply_injection(self):
        self._preview_install(False)

    def force_injection(self):
        self._preview_install(True)

    def _preview_install(self,force):
        if not self.current_profile or self.busy:return
        from core.settings import validate
        try:
            settings=self._settings();settings.pop('settings_schema')
            validate(settings,require_mode=True)
        except ValueError as exc:
            messagebox.showerror('Choose a mode',str(exc));return
        self._save_current_settings_to_profile()
        profile=dict(self.current_profile)
        settings.update(force=force,hardware=self.hw_info or {})
        def prepare():
            analysis=GameDetector.analyze_game(profile['target_exe'],profile.get('base_dir'))
            if not analysis.get('valid'):raise ValueError(analysis.get('error'))
            return build_plan(analysis,settings,self.injector.version_manager)
        def ready(plan):
            if 'error' in plan:messagebox.showerror('Preview failed',plan['error']);return
            self.preview_plan=plan
            preview=format_plan(plan)
            self.log(preview,'warning' if plan['errors'] else 'info')
            if plan.get('blocking_error') or (plan['errors'] and not force):messagebox.showerror('Installation not ready',preview);return
            def install():
                def done(result):
                    self._record_result(profile,result)
                    self.log(result.get('message',result.get('error','')), 'success' if result.get('success') else 'error')
                    if not result.get('success'):messagebox.showerror('Installation failed',result.get('error','')+'\n'+str(result.get('recovery','')))
                self._run(lambda:self.injector.apply_injection(profile['target_dir'],profile['target_exe'],base_dir=profile.get('base_dir'),expected_plan=plan,**settings),done)
            from gui.install_preview import show_preview
            show_preview(self, plan, force, install)
        self._run(prepare,ready)

    def launch_and_protect(self):
        if not self.current_profile or self.busy:
            return
        if self.active_watchdog and self.active_watchdog._monitor_thread and self.active_watchdog._monitor_thread.is_alive():
            messagebox.showinfo('Monitoring active', 'Wait for the current startup observation to finish.')
            return
        profile = dict(self.current_profile)
        if not self.injector.is_injected(profile['target_dir']):
            messagebox.showinfo('Install first', 'Install the selected configuration before launching.')
            return
        if self.injector.is_game_running(profile['target_exe'], profile['target_dir']):
            messagebox.showinfo('Game running', 'This game is already running.')
            return
        def crash(reason, result):
            self._record_result(profile, result)
            self.log(reason, 'error')
            messagebox.showerror('Startup failure', reason + '\n' + result.get('message', result.get('error', '')))
        self.active_watchdog = CrashWatchdog(profile['target_exe'], profile['target_dir'], SafetyManager(),
            additional_dirs=profile.get('recovery_dirs', profile.get('all_target_dirs', [])),
            on_status_update=lambda msg, level: self.dispatcher.post(self.log, msg, level),
            on_crash_detected=lambda reason, result: self.dispatcher.post(crash, reason, result),
            on_launch_success=lambda: self.dispatcher.post(self.log, f"{profile['name']}: startup observation complete; verify the in-game overlay.", 'info'))
        if not self.active_watchdog.launch_and_monitor():
            messagebox.showerror('Launch failed', 'Could not launch the game. See the application log.')

    def revert_changes(self):
        if not self.current_profile or self.busy:
            return
        profile = dict(self.current_profile)
        if not messagebox.askyesno('Revert installation', f"Restore the saved original files for {profile['name']}?"):
            return
        def done(result):
            if result.get('conflicts'):
                detail='\n'.join(result['conflicts'])
                if messagebox.askyesno('Preserve changed files',detail+'\n\nThese files changed outside ArcScaler. Save separate copies and restore the originals?'):
                    self._run(lambda:self.injector.revert_injection(profile['target_dir'],profile['target_exe'],
                        additional_dirs=profile.get('recovery_dirs',profile.get('all_target_dirs',[])),preserve_changes=True),done)
                return
            self._record_result(profile, result)
            self.log(result.get('message', result.get('error', '')), 'success' if result.get('success') else 'error')
            if not result.get('success'):
                messagebox.showerror('Recovery incomplete', result['error'])
            elif result.get('preserved'):
                messagebox.showinfo('Changed files preserved','Copies were saved under .optiscaler_backup/preserved.\n'+result['preserved'][0]['copy'])
        self._run(lambda: self.injector.revert_injection(profile['target_dir'], profile['target_exe'],
                  additional_dirs=profile.get('recovery_dirs', profile.get('all_target_dirs', []))), done)

    def open_game_folder(self):
        if self.current_profile:
            try:os.startfile(self.current_profile['target_dir'])
            except OSError as exc:messagebox.showerror('Open game folder',str(exc))

    def export_diagnostics(self):
        from core.diagnostics import diagnostic_report
        from tkinter import filedialog
        from core.files import atomic_write
        from gui.theme import button
        dialog=ctk.CTkToplevel(self);dialog.title('Preview diagnostic report');dialog.geometry('720x600');dialog.transient(self.winfo_toplevel());dialog.grab_set()
        redacted=ctk.BooleanVar(value=True)
        report=ctk.CTkTextbox(dialog,wrap='word');report.pack(fill='both',expand=True,padx=16,pady=16)
        def refresh():
            text=diagnostic_report(self.current_profile,self.hw_info,self._analysis,self.preview_plan,self.health,
                getattr(getattr(self.winfo_toplevel(),'log_console',None),'entries',[]),redact=redacted.get(),evidence=self._evidence or [])
            report.configure(state='normal');report.delete('1.0','end');report.insert('1.0',text);report.configure(state='disabled')
        ctk.CTkCheckBox(dialog,text='Redact personal paths',variable=redacted,command=refresh).pack(anchor='w',padx=16)
        def save():
            path=filedialog.asksaveasfilename(parent=dialog,defaultextension='.json',initialfile='ArcScaler-diagnostics.json',filetypes=[('JSON report','*.json')])
            if path:
                try:atomic_write(path,report.get('1.0','end-1c').encode('utf-8'));dialog.destroy()
                except OSError as exc:messagebox.showerror('Export failed',str(exc),parent=dialog)
        button(dialog,'Save report',save,primary=True).pack(side='right',padx=16,pady=16)
        button(dialog,'Cancel',dialog.destroy).pack(side='right',pady=16)
        refresh()

    def shutdown(self):
        if self._closed:return
        self._remember_view();self._closed=True;self.research_generation+=1
        for timer in (self._plan_timer,self._view_timer):
            if timer:self.after_cancel(timer)
        self.tasks.close()
        if self.active_watchdog:self.active_watchdog.stop()
        self.dispatcher.close()
