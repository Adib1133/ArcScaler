"""ArcScaler desktop shell: library, downloads, hardware, log and app preferences."""
import json
import os
import threading
from pathlib import Path
from tkinter import messagebox
import customtkinter as ctk
from gui.theme import divider
from PIL import Image
from core.injector import Injector
from core.library import GameLibrary
from core.hardware import HardwareDetector
from core.paths import prepare_assets, data_root, resource_root
from core.icons import IconCache
from core.files import write_json
from gui.library_tab import LibraryTab
from gui.config_tab import ConfigTab
from gui.version_tab import VersionTab
from gui.log_widget import LogConsole
from gui.dispatch import UIDispatcher
from gui.theme import BG,SIDE,PANEL,BORDER,MUTED,BLUE,label,button,card

class MainWindow(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode('Dark');ctk.set_default_color_theme('blue')
        super().__init__()
        from gui.tk_lifecycle import TkCollection
        self._tk_collection = TkCollection(self)
        self.title('ArcScaler');self.minsize(600,500);self.configure(fg_color=BG)
        self.protocol('WM_DELETE_WINDOW',self._on_close)
        icon=resource_root()/'assets/arcscaler.ico'
        if icon.exists():self.iconbitmap(str(icon))
        try:
            import ctypes
            ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.windll.user32.GetParent(self.winfo_id()),20,ctypes.byref(ctypes.c_int(1)),4)
        except (AttributeError,OSError):pass
        self.dispatcher=UIDispatcher(self)
        from core.tasks import TaskManager
        self.tasks=TaskManager(self.dispatcher,workers=2)
        self.storage=data_root()
        self.preferences={}
        try:self.preferences=json.loads((self.storage/'settings.json').read_text(encoding='utf-8'))
        except FileNotFoundError:pass
        except (OSError,ValueError) as exc:messagebox.showwarning('ArcScaler settings',str(exc))
        self.injector=Injector(str(prepare_assets()))
        self.library=GameLibrary(str(self.storage/'profiles.json'))
        self._sync_game_defaults()
        self.icons=IconCache(self.storage/'icon-cache',self.dispatcher)
        self.hardware_worker=None
        self.hw_info={'primary_gpu':{'name':'Detecting GPU…','vendor':'Unknown','driver_version':'—','xess_acceleration':'Auto'},'cpu':'—','ram_gb':'—','os':'Windows','is_arc_detected':False}
        self._build_ui()
        self.log_console.log('ArcScaler started.')
        self._redetect()
        self._check_library_health()
        self.bind('<Configure>',self._responsive,add='+')
        self._apply_scale(self.preferences.get('ui_scale','100%'))
        # Scaling temporarily pins Tk's min/max to its intermediate dimensions.
        # Set the initial desktop size only after widgets and scaling are ready.
        self._set_scaled_min_max()
        width=min(1100*self._get_widget_scaling(),self.winfo_screenwidth()-48)
        height=min(720*self._get_widget_scaling(),self.winfo_screenheight()-80)
        self.geometry(f'{round(self._reverse_window_scaling(width))}x{round(self._reverse_window_scaling(height))}')
        self.deiconify()

    def _build_ui(self):
        self.sidebar_frame=ctk.CTkFrame(self,width=188,fg_color=SIDE,corner_radius=0,border_width=1,border_color=BORDER)
        self.sidebar_frame.pack(side='left',fill='y');self.sidebar_frame.pack_propagate(False)
        self.sidebar_divider=divider(self,vertical=True);self.sidebar_divider.pack(side='left',fill='y')
        brand=ctk.CTkFrame(self.sidebar_frame,fg_color='transparent');brand.pack(fill='x',padx=18,pady=(20,24))
        self.brand=ctk.CTkImage(Image.open(resource_root()/'assets/arcscaler-icon.png'),size=(28,28))
        ctk.CTkLabel(brand,text='',image=self.brand).pack(side='left',padx=(0,10))
        names=ctk.CTkFrame(brand,fg_color='transparent');names.pack(side='left')
        title=label(names,'ArcScaler');title.configure(font=ctk.CTkFont(size=14,weight='bold'));title.pack(anchor='w')
        label(names,'for Intel Arc',muted=True).pack(anchor='w')
        self.sidebar_buttons={}
        for name,symbol in [('Library','▦'),('Downloads','↓'),('Hardware','⚙'),('Log','≡')]:
            item=button(self.sidebar_frame,f'{symbol}   {name}',lambda n=name:self.select_tab(n),anchor='w')
            item.pack(fill='x',padx=12,pady=3);self.sidebar_buttons[name]=item
        self.gpu_badge=card(self.sidebar_frame);self.gpu_badge.pack(side='bottom',fill='x',padx=12,pady=14)
        self.gpu_label=label(self.gpu_badge,'Detecting GPU…',wraplength=145,justify='left');self.gpu_label.pack(padx=10,pady=10)
        app_button=button(self.sidebar_frame,'☼   App settings',lambda:self.select_tab('App settings'),anchor='w');app_button.pack(side='bottom',fill='x',padx=12,pady=10);self.sidebar_buttons['App settings']=app_button
        self.content_area=ctk.CTkFrame(self,fg_color=BG,corner_radius=0);self.content_area.pack(side='left',fill='both',expand=True)
        self.recovery_notice=button(self.content_area,'',self._open_recovery)
        self.log_console=LogConsole(self.content_area)
        self.library_view=ctk.CTkFrame(self.content_area,fg_color=BG,corner_radius=0)
        self.library_tab=LibraryTab(self.library_view,self.library,self._on_game_selected_from_library,self.log_console.log,self.icons)
        self.library_tab.reduced_motion=self.preferences.get('reduced_motion',False)
        self.library_tab.pack(side='left',fill='y')
        self.rail_divider=divider(self.library_view,vertical=True);self.rail_divider.pack(side='left',fill='y')
        self.config_tab=ConfigTab(self.library_view,self.injector,self.library,self.log_console.log,self._on_profile_updated,self.hw_info)
        self.config_tab.icons=self.icons;self.config_tab.pack(side='left',fill='both',expand=True)
        self.config_tab.show_empty(self.library_tab)
        self.version_tab=VersionTab(self.content_area,self.injector.version_manager,self.log_console.log,self._versions_updated)
        self.version_tab.on_use_version=self._use_version
        self.version_tab.selected_version=self.preferences.get('default_release','')
        self.system_info_tab=ctk.CTkFrame(self.content_area,fg_color=BG,corner_radius=0)
        self._render_hardware()
        self.settings_tab=self._build_settings()
        self.views={'Library':self.library_view,'Downloads':self.version_tab,'Hardware':self.system_info_tab,'Log':self.log_console,'App settings':self.settings_tab}
        self.active_tab_name=None;self.select_tab('Library')
        self.compact_nav=ctk.CTkFrame(self.content_area,fg_color=PANEL)
        self.compact_view=ctk.CTkOptionMenu(self.compact_nav,values=list(self.views),command=self.select_tab,width=135)
        self.compact_view.set('Library');self.compact_view.pack(side='left',padx=8,pady=8)
        self.compact_game=ctk.CTkOptionMenu(self.compact_nav,values=['No games'],command=self._compact_select,width=210)
        self.compact_game.pack(side='right',padx=8,pady=8)
        self._compact_games()
        profiles=list(self.library.profiles.values())
        if profiles:self._on_game_selected_from_library(profiles[0])

    def select_tab(self,name):
        if self.active_tab_name == name:return
        for view in self.views.values():view.pack_forget()
        self.views[name].pack(fill='both',expand=True)
        for key,widget in self.sidebar_buttons.items():widget.configure(fg_color='#1D2430' if key==name else 'transparent',border_width=0,text_color='white' if key==name else MUTED)
        self.active_tab_name=name
        if hasattr(self,'compact_view'):self.compact_view.set(name)
        if hasattr(self,'compact_nav') and self.compact_nav.winfo_manager():self.compact_nav.pack(before=self.views[name])

    def _compact_games(self):
        if not hasattr(self,'compact_game'):return
        self.compact_profiles={p['name'][:24]+' · '+p['id'][:4]:p['id'] for p in self.library.profiles.values()}
        self.compact_game.configure(values=list(self.compact_profiles) or ['No games'])
        active=self.config_tab.current_profile
        self.compact_game.set(next((name for name,game_id in self.compact_profiles.items() if active and game_id==active['id']),next(iter(self.compact_profiles),'No games')))

    def _compact_select(self,value):
        game_id=self.compact_profiles.get(value)
        if game_id:self._on_game_selected_from_library(self.library.profiles[game_id])

    def _apply_scale(self,value):
        if value not in ('100%','125%','150%','175%','200%'):value='100%'
        ctk.set_widget_scaling(int(value[:-1])/100)
        self.after_idle(lambda:self._responsive(None))

    def _responsive(self,event):
        if event is not None and event.widget is not self:return
        if not hasattr(self,'compact_nav'):return
        logical=self.winfo_width()/self._get_widget_scaling()
        layout=(logical<980,logical<800)
        if layout==getattr(self,'_layout',None):return
        self._layout=layout
        self.sidebar_frame.pack_forget();self.sidebar_divider.pack_forget()
        if not layout[0]:
            self.sidebar_frame.pack(side='left',fill='y',before=self.content_area)
            self.sidebar_divider.pack(side='left',fill='y',before=self.content_area)
        self.library_tab.pack_forget();self.rail_divider.pack_forget()
        if not layout[1]:
            self.library_tab.pack(side='left',fill='y',before=self.config_tab)
            self.rail_divider.pack(side='left',fill='y',before=self.config_tab)
        self.compact_nav.pack_forget()
        if layout[0]:self.compact_nav.pack(fill='x',before=self.views[self.active_tab_name])

    def _on_game_selected_from_library(self,profile):
        if self.config_tab.busy:return
        profile=dict(self.library.profiles.get(profile['id'],profile))
        if self.config_tab.current_profile and self.config_tab.current_profile['id']==profile['id']:
            self.select_tab('Library');return
        if not profile.get('optiscaler_version') and self.preferences.get('default_release'):
            profile=dict(profile,optiscaler_version=self.preferences['default_release'])
        self.library_tab.set_active_game(profile)
        self.config_tab.load_game(profile)
        self.select_tab('Library')
        self._compact_games()

    def _on_profile_updated(self):
        self.library_tab.refresh_library()
        self._compact_games()
        self._refresh_recovery_notice()
        if not self.config_tab.current_profile:self.config_tab.show_empty(self.library_tab)

    def _refresh_recovery_notice(self):
        self.recovery_profiles=[p['id'] for p in self.library.profiles.values() if p.get('installation_health') in ('recovery_required','modified','incomplete')]
        if self.recovery_profiles:
            self.recovery_notice.configure(text=f'{len(self.recovery_profiles)} game(s) need installation review · Open')
            self.recovery_notice.pack(fill='x',padx=16,pady=8,before=self.views[self.active_tab_name])
        else:self.recovery_notice.pack_forget()

    def _versions_updated(self):
        self.config_tab.update_version_list()
        if hasattr(self,'default_release_menu'):
            values=self.injector.version_manager.get_installed_versions()
            self.default_release_menu.configure(values=values or ['No installed releases'])

    def _use_version(self,tag):
        self.preferences['default_release']=tag;self._save_preferences()
        if hasattr(self,'default_release_menu'):self.default_release_menu.set(tag)
        self.version_tab.selected_version=tag;self.version_tab._populate_cards()
        self.config_tab.version_var.set(tag);self.config_tab._on_pipeline_param_changed()
        self.log_console.log('Selected OptiScaler '+tag+'.')

    def _redetect(self):
        if self.hardware_worker and self.hardware_worker.is_alive():return
        def worker():
            try:info,error=HardwareDetector.get_system_info(),None
            except Exception as exc:info,error=None,str(exc)
            self.dispatcher.post(done,info,error)
        def done(info,error):
            if error:self.log_console.log(error,'error');return
            self.hw_info=info;self.config_tab.hw_info=info
            gpu=info['primary_gpu'];self.gpu_label.configure(text=gpu['name']+'\nXeSS · '+gpu['xess_acceleration'])
            self._render_hardware();self.log_console.log(f"Detected {gpu['name']} (Driver: {gpu['driver_version']}). XeSS path: {gpu['xess_acceleration']}.")
        self.hardware_worker=threading.Thread(target=worker,daemon=True)
        self.hardware_worker.start()

    def _render_hardware(self):
        from gui.theme import TEXT
        for child in self.system_info_tab.winfo_children():child.destroy()
        frame=self.system_info_tab
        top=ctk.CTkFrame(frame,fg_color='transparent');top.pack(fill='x',padx=30,pady=(26,0))
        title=label(top,'Hardware');title.configure(font=ctk.CTkFont(size=22,weight='bold'));title.pack(side='left')
        button(top,'Copy diagnostics',self._copy_diagnostics,width=140).pack(side='right')
        button(top,'Re-detect',self._redetect,width=100).pack(side='right',padx=8)
        label(frame,'What ArcScaler found on this PC',muted=True).pack(anchor='w',padx=30,pady=(0,14))
        gpu=self.hw_info['primary_gpu'];banner=card(frame);banner.pack(fill='x',padx=30,pady=(0,16))
        detected=label(banner,'●  '+gpu['vendor']+' GPU detected')
        detected.configure(text_color='#42D9A0');detected.pack(anchor='w',padx=16,pady=(12,0))
        label(banner,'XeSS runtime selects '+gpu['xess_acceleration']+' on this GPU.',muted=True).pack(anchor='w',padx=16,pady=(0,12))
        table=ctk.CTkFrame(frame,fg_color='transparent');table.pack(fill='x',padx=30)
        table.grid_columnconfigure((0,1),weight=1)
        fields=[('Graphics',gpu['name']),('Driver',gpu['driver_version']),('Processor',self.hw_info['cpu']),('Memory',str(self.hw_info['ram_gb'])+' GB'),('XeSS mode',gpu['xess_acceleration']),('System',self.hw_info['os'])]
        for i,(key,value) in enumerate(fields):
            row=ctk.CTkFrame(table,fg_color='transparent');row.grid(row=i//2,column=i%2,sticky='ew',padx=(0,20),pady=4)
            label(row,key,muted=True).pack(side='left')
            label(row,str(value),wraplength=210,justify='right').pack(side='right')
        label(frame,'Recommended setup for this GPU',muted=True).pack(anchor='w',padx=30,pady=(24,4))
        rec=card(frame);rec.pack(fill='x',padx=30)
        for title,detail,value in [('GPU identity spoofing','Leave off first to avoid unnecessary compatibility changes.','Off'),('XeSS network','Picked automatically for your hardware.','Auto'),('Low-latency mode (XeLL)','Routes supported latency markers through FakeNvapi.','On')]:
            row=ctk.CTkFrame(rec,fg_color='transparent');row.pack(fill='x',padx=16,pady=10)
            text=ctk.CTkFrame(row,fg_color='transparent');text.pack(side='left');label(text,title).pack(anchor='w');label(text,detail,muted=True).pack(anchor='w')
            label(row,value,muted=True).pack(side='right')
        button(frame,'Apply to selected game',self._apply_hardware,primary=True,width=190).pack(anchor='w',padx=30,pady=14)

    def _check_library_health(self):
        from core.installation_state import installation_health
        profiles=list(self.library.profiles.values())
        def operation():
            from core.tasks import checkpoint
            results=[]
            for profile in profiles:
                checkpoint()
                results.append((profile['id'],installation_health(profile['target_dir'],profile.get('recovery_dirs',profile.get('all_target_dirs',[])))))
            return results
        def done(results):
            self.recovery_profiles=[]
            for game_id,health in results:
                if health['state'] in ('recovery_required','modified','incomplete'):self.recovery_profiles.append(game_id)
                if game_id in self.library.profiles and (not self.config_tab.current_profile or self.config_tab.current_profile['id']!=game_id):
                    self.library.update_profile(game_id,{'installation_health':health['state'],'is_injected':health['state']=='installed'})
            if self.recovery_profiles:
                self.recovery_notice.configure(text=f'{len(self.recovery_profiles)} game(s) need installation review · Open')
                self.recovery_notice.pack(fill='x',padx=16,pady=8,before=self.views[self.active_tab_name])
            else:self.recovery_notice.pack_forget()
            self.library_tab.refresh_library()
        self.tasks.submit('startup-health',operation,done,lambda error:self.log_console.log('Installation review failed: '+error,'error'))

    def _open_recovery(self):
        for game_id in getattr(self,'recovery_profiles',[]):
            if game_id in self.library.profiles:
                self._on_game_selected_from_library(self.library.profiles[game_id]);self.config_tab.rescan();break

    def _copy_diagnostics(self):
        self.clipboard_clear();self.clipboard_append(json.dumps(self.hw_info,indent=2))

    def _apply_hardware(self):
        if not self.config_tab.current_profile:messagebox.showinfo('Select a game','Select a game in Library first.');return
        self.config_tab.spoof_var.set(False);self.config_tab.model_var.set('auto');self.config_tab.reflex_var.set(True)
        self.config_tab._on_pipeline_param_changed();self.select_tab('Library')

    def _build_settings(self):
        from gui.theme import ContentFrame
        frame=ContentFrame(self.content_area,fg_color=BG,corner_radius=0)
        title=label(frame,'App settings');title.configure(font=ctk.CTkFont(size=22,weight='bold'));title.pack(anchor='w',padx=30,pady=(26,4))
        label(frame,'Defaults for new games and downloads',muted=True).pack(anchor='w',padx=30,pady=(0,16))
        from core.settings import HOOKS
        box=card(frame);box.pack(fill='x',padx=30,pady=(0,16))
        def row(title,detail,last=False):
            item=ctk.CTkFrame(box,fg_color='transparent');item.pack(fill='x',padx=18,pady=12)
            words=ctk.CTkFrame(item,fg_color='transparent');words.pack(fill='x')
            label(words,title).pack(anchor='w');label(words,detail,muted=True,wraplength=300,justify='left').pack(anchor='w')
            if not last:divider(box).pack(fill='x',padx=18)
            return item
        def save(key,value):
            self.preferences[key]=value;self._save_preferences()
            if key=='default_release':
                self.version_tab.selected_version=value;self.version_tab._populate_cards()
        releases=self.injector.version_manager.get_installed_versions()
        self.default_release_menu=ctk.CTkOptionMenu(row('Default OptiScaler version','Used for new installs'),
            values=releases or ['No installed releases'],fg_color='#171C24',button_color='#171C24',
            command=lambda value:save('default_release',value))
        self.default_release_menu.set(self.preferences.get('default_release') or (releases[0] if releases else 'No installed releases'))
        self.default_release_menu.pack(side='right')
        proxy=ctk.CTkOptionMenu(row('Default proxy filename','Used for newly added games'),values=list(HOOKS),
            fg_color='#171C24',button_color='#171C24',command=lambda value:save('default_proxy',value))
        proxy.set(self.preferences.get('default_proxy','dxgi.dll'));proxy.pack(side='right')
        backup=ctk.CTkSwitch(row('Back up original files','Before every install · always enabled for recovery',True),text='',progress_color=BLUE)
        backup.select();backup.configure(state='disabled');backup.pack(side='right')
        button(frame,'Open app data folder',lambda:os.startfile(self.storage),width=200).pack(anchor='w',padx=30,pady=8)
        motion=ctk.BooleanVar(value=self.preferences.get('reduced_motion',False))
        def motion_changed():
            self.library_tab.reduced_motion=motion.get();save('reduced_motion',motion.get())
        ctk.CTkSwitch(frame,text='Reduce motion',variable=motion,command=motion_changed).pack(anchor='w',padx=30,pady=8)
        label(frame,'Interface size',muted=True).pack(anchor='w',padx=30,pady=(8,0))
        scale=ctk.CTkOptionMenu(frame,values=['100%','125%','150%','175%','200%'],command=lambda value:(save('ui_scale',value),self._apply_scale(value)))
        scale.set(self.preferences.get('ui_scale','100%'));scale.pack(anchor='w',padx=30,pady=8)
        button(frame,'Preview diagnostic report',self.config_tab.export_diagnostics,width=220).pack(anchor='w',padx=30,pady=8)
        def overrides():
            path=self.storage/'compatibility.json'
            if not path.exists():write_json(path,{'steam':{},'exe':{}})
            os.startfile(path)
        button(frame,'Edit compatibility overrides',overrides,width=230).pack(anchor='w',padx=30,pady=8)
        label(frame,str(self.storage),muted=True,wraplength=650).pack(anchor='w',padx=30,pady=12)
        label(frame,'Dark appearance · per-game settings saved automatically',muted=True).pack(anchor='w',padx=30)
        return frame

    def _sync_game_defaults(self):
        from core.settings import HOOKS
        proxy=self.preferences.get('default_proxy','dxgi.dll')
        self.library.new_game_defaults={'hook_method':proxy if proxy in HOOKS else 'dxgi.dll',
            'optiscaler_version':self.preferences.get('default_release','')}

    def _save_preferences(self):
        self._sync_game_defaults()
        try:write_json(self.storage/'settings.json',self.preferences)
        except OSError as exc:messagebox.showerror('Save preferences',str(exc))

    def _on_close(self):
        if self.config_tab.busy or self.version_tab.mutating:
            messagebox.showinfo('Operation in progress','Wait for the current operation to finish.');return
        if self.hardware_worker and self.hardware_worker.is_alive():
            self.after(100,self._on_close);return
        watchdog=self.config_tab.active_watchdog
        if watchdog:
            watchdog.stop()
            if watchdog._monitor_thread and watchdog._monitor_thread.is_alive():self.after(100,self._on_close);return
        self.tasks.close();self.icons.close();self.dispatcher.close();self.config_tab.shutdown();self.version_tab.shutdown()
        self.library_tab.shutdown();self.log_console.dispatcher.close();self.cancel_timers();self.destroy()

    def cancel_timers(self):
        for timer in self.tk.splitlist(self.tk.call('after','info')):self.tk.call('after','cancel',timer)

    def destroy(self):
        try:
            super().destroy()
        finally:
            if hasattr(self, '_tk_collection'):self._tk_collection.close()
