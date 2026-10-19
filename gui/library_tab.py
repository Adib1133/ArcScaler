"""Searchable game rail with asynchronous discovery and executable artwork."""
import threading
from tkinter import filedialog, messagebox
import customtkinter as ctk
from gui.theme import divider
from gui.theme import ContentFrame
from gui.dispatch import UIDispatcher
from gui.theme import BG, PANEL, MUTED, BORDER, TEXT, button, label

class LibraryTab(ctk.CTkFrame):
    def __init__(self, master, game_library, on_select_game, log_callback, icons, **kwargs):
        super().__init__(master,fg_color=BG,width=250,corner_radius=0,**kwargs)
        self.pack_propagate(False)
        self.library,self.on_select_game,self.log,self.icons=game_library,on_select_game,log_callback,icons
        self.dispatcher=UIDispatcher(self)
        from core.tasks import TaskManager
        self.tasks=TaskManager(self.dispatcher,workers=1)
        self.reduced_motion=False
        self.scanning=False
        self.active_profile=None
        self.search=ctk.StringVar()
        search_entry=ctk.CTkEntry(self,placeholder_text='Search games',height=34,
                     fg_color=PANEL,border_color=BORDER,border_width=1,corner_radius=8)
        search_entry.pack(fill='x',padx=12,pady=16)
        search_entry.bind('<KeyRelease>',lambda _:self.search.set(search_entry.get()))
        self.search.trace_add('write',lambda *_:self.refresh_library())
        actions=ctk.CTkFrame(self,fg_color='transparent')
        actions.pack(side='bottom',fill='x',padx=12,pady=12)
        divider(actions).pack(fill='x',pady=(0,8))
        button(actions,'+  Add game (.exe / .lnk)',self._add_game_file).pack(fill='x',pady=4)
        row=ctk.CTkFrame(actions,fg_color='transparent');row.pack(fill='x')
        button(row,'Add folder',self._add_game_folder,width=105).pack(side='left')
        button(row,'Auto-scan',self._auto_scan,width=105).pack(side='right')
        self.cancel_scan_btn=button(actions,'Cancel scan',self.cancel_scan)
        
        self.list=ContentFrame(self,fg_color=BG,corner_radius=0)
        self.list.pack(fill='both',expand=True,padx=4)
        self.refresh_library()

    def refresh_library(self):
        profiles=list(self.library.profiles.values())
        query=self.search.get().casefold()
        signature=(query,tuple((p['id'],p['name'],p['target_exe']) for p in profiles))
        if signature==getattr(self,'_list_signature',None):
            for profile in profiles:
                if profile['id'] in self.status_labels:self._status(self.status_labels[profile['id']],profile)
            return
        self._list_signature=signature
        for child in self.list.winfo_children():child.destroy()
        self.rows={};self.status_labels={}
        for profile in profiles:
            if query not in profile['name'].casefold():continue
            selected=self.active_profile and self.active_profile['id']==profile['id']
            row=ctk.CTkFrame(self.list,fg_color='#1A202A' if selected else 'transparent',corner_radius=8)
            row.pack(fill='x',pady=2)
            self.rows[profile['id']]=row
            icon=label(row,'',width=36,height=40);icon.pack(side='left',padx=(6,8),pady=6)
            text=ctk.CTkFrame(row,fg_color='transparent');text.pack(side='left',fill='x',expand=True)
            title=label(text,profile['name'][:27],anchor='w');title.configure(font=ctk.CTkFont(size=12,weight='bold'));title.pack(anchor='w')
            status='Installed' if profile.get('is_injected') else 'Not installed'
            health=profile.get('installation_health')
            if health and health not in ('installed','not_installed'):status=health.replace('_',' ').capitalize()
            if profile.get('is_injected'):status+=' · '+profile.get('last_installation_plan',{}).get('settings',{}).get('optiscaler_version','')
            status_label=label(text,'● '+status,anchor='w')
            status_label.configure(text_color='#42D9A0' if health=='installed' or profile.get('is_injected') else '#FFBF69' if health and health!='not_installed' else '#FF7887')
            status_label.pack(anchor='w')
            self.status_labels[profile['id']]=status_label
            for widget in [row,icon,text,*text.winfo_children()]:
                widget.bind('<Button-1>',lambda e,p=dict(profile):self.on_select_game(p))
            def received(image,widget=icon):
                if widget.winfo_exists():
                    artwork=ctk.CTkImage(image,size=(36,36));widget.configure(image=artwork,text='');widget.artwork=artwork
            self.icons.request(profile['target_exe'],profile['name'],received)
        if not profiles:label(self.list,'Your library is empty.',muted=True,wraplength=190).pack(pady=24)

    @staticmethod
    def _status(widget,profile):
        health=profile.get('installation_health') or ('installed' if profile.get('is_injected') else 'not_installed')
        text=health.replace('_',' ').capitalize()
        if health=='installed':text+=' · '+profile.get('last_installation_plan',{}).get('settings',{}).get('optiscaler_version','')
        widget.configure(text='● '+text,text_color='#42D9A0' if health=='installed' else '#FF7887' if health=='not_installed' else '#FFBF69')

    def set_active_game(self,profile):
        self.active_profile=profile
        self.selection_generation=getattr(self,'selection_generation',0)+1
        generation=self.selection_generation
        for game_id,row in self.rows.items():
            if game_id!=profile['id']:row.configure(fg_color='transparent')
        row=self.rows.get(profile['id'])
        def animate(step=0):
            if generation!=self.selection_generation or row is None or not row.winfo_exists():return
            start=(16,18,22);end=(39,45,54)
            color='#'+''.join(f'{round(a+(b-a)*step/8):02x}' for a,b in zip(start,end))
            row.configure(fg_color=color)
            if step<8:self.after(16,lambda:animate(step+1))
        animate(8 if self.reduced_motion else 0)

    def _add_game_file(self):
        path=filedialog.askopenfilename(filetypes=[('Game executable or shortcut','*.exe *.lnk')])
        if path:self._scan(lambda:[self.library.add_game_by_path(path)])

    def _add_game_folder(self):
        path=filedialog.askdirectory()
        if path:self._scan(lambda:[self.library.add_game_by_path(path)])

    def _auto_scan(self):
        self._scan(self.library.auto_scan_installed_games)

    def _scan(self,operation):
        if self.scanning:return
        self.scanning=True
        self.cancel_scan_btn.pack(fill='x',pady=4)
        self.log('Scanning game executables…')
        def done(result,error):
            self.scanning=False
            self.cancel_scan_btn.pack_forget()
            self.refresh_library()
            valid=[p for p in result if p]
            if error or not valid:messagebox.showinfo('Game discovery',error or 'No supported x64 game executable was found.')
            elif len(valid)==1:self.on_select_game(valid[0])
            self.log(f'Found {len(valid)} game(s).')
        self.tasks.submit('library-scan',operation,lambda result:done(result,None),lambda error:done([],error))

    def cancel_scan(self):
        self.tasks.cancel('library-scan');self.scanning=False
        self.cancel_scan_btn.pack_forget();self.refresh_library();self.log('Game scan cancelled.')

    def shutdown(self):
        self.tasks.close();self.dispatcher.close()
