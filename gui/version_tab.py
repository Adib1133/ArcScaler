"""Compact release list backed by the validated package manager."""
import os
import threading
from pathlib import Path
from tkinter import messagebox
import customtkinter as ctk
from gui.theme import divider
from gui.theme import ContentFrame
from gui.dispatch import UIDispatcher
from gui.theme import BG,BORDER,MUTED,BLUE,label,button,badge

class VersionTab(ctk.CTkFrame):
    def __init__(self,master,version_manager,log_callback,on_versions_updated=None,**kwargs):
        super().__init__(master,fg_color=BG,corner_radius=0,**kwargs)
        self.version_manager,self.log=version_manager,log_callback
        self.on_versions_updated=on_versions_updated;self.on_use_version=None;self.selected_version=''
        self.dispatcher=UIDispatcher(self);self.busy=False;self.releases=[];self.buttons=[]
        from core.tasks import TaskManager
        self.tasks=TaskManager(self.dispatcher,workers=1)
        self.mutating=False;self.cancel_event=None
        top=ctk.CTkFrame(self,fg_color='transparent');top.pack(fill='x',padx=28,pady=(24,4))
        title=label(top,'Downloads');title.configure(font=ctk.CTkFont(size=22,weight='bold'));title.pack(side='left')
        self.check_btn=button(top,'Refresh releases',self.refresh_versions,width=125);self.check_btn.pack(side='right')
        self.folder_btn=button(top,'Open folder',self.open_versions_dir,width=105);self.folder_btn.pack(side='right',padx=8)
        self.validate_btn=button(top,'Validate local',self._validate_local,width=110);self.validate_btn.pack(side='right')
        self.status=label(self,'Reading local releases…',muted=True,wraplength=800,justify='left');self.status.pack(anchor='w',padx=28)
        label(self,'Manual install: open the folder, extract into a version folder (for example v0.9.4), then Validate local.',muted=True,wraplength=800,justify='left').pack(anchor='w',padx=28,pady=12)
        self.filter=ctk.CTkSegmentedButton(self,values=['All','Downloaded'],command=lambda _:self._populate_cards(),selected_color='#1E2530',fg_color='#171C24',unselected_color='#171C24',unselected_hover_color='#222B39',corner_radius=8,height=32)
        self.filter.set('All');self.filter.pack(anchor='w',padx=28)
        self.progress=ctk.CTkProgressBar(self,height=3,progress_color=BLUE);self.progress.set(0);self.progress.pack(fill='x',padx=28,pady=8)
        self.cancel_btn=button(self,'Cancel download',self.cancel_download)
        self.releases_scroll=ContentFrame(self,fg_color=BG,corner_radius=0);self.releases_scroll.pack(fill='both',expand=True,padx=20,pady=(0,12))
        self.after(50,lambda:self.refresh_versions(False))

    def _busy(self,value):
        self.busy=value
        for widget in [self.check_btn,self.validate_btn,*self.buttons]:widget.configure(state='disabled' if value else 'normal')
        if not value:self._populate_cards()

    def refresh_versions(self,force_refresh=True):
        if self.busy:return
        self._busy(True);self.status.configure(text='Reading official releases…')
        def done(releases,error):
            self.releases=releases;self._busy(False)
            self.status.configure(text=error or f'{len(releases)} OptiScaler releases')
            if self.on_versions_updated:self.on_versions_updated()
        self.tasks.submit('releases',lambda:self.version_manager.fetch_available_releases(force_refresh),lambda releases:done(releases,self.version_manager.last_error),lambda error:done([],error))

    def _populate_cards(self):
        for child in self.releases_scroll.winfo_children():child.destroy()
        self.buttons=[]
        releases=list(self.releases)
        tags={r['tag_name'] for r in releases}
        for tag in self.version_manager.get_installed_versions():
            if tag not in tags:releases.append({'tag_name':tag,'installed':True,'assets':[]})
        for i,release in enumerate(releases):
            installed=release.get('installed',False);tag=release['tag_name']
            if self.filter.get()=='Downloaded' and not installed:continue
            row=ctk.CTkFrame(self.releases_scroll,fg_color='transparent',height=48);row.pack(fill='x',padx=6,pady=1)
            label(row,tag,width=65,anchor='w').pack(side='left',padx=(0,8))
            if tag==self.selected_version:badge(row,'●  In use',True).pack(side='left',padx=(0,8))
            if i==0 and self.releases:badge(row,'Latest').pack(side='left')
            def add(text,command,width,state=True,primary=False):
                widget=button(row,text,command,width=width,primary=primary);widget.configure(state='normal' if state and not self.busy else 'disabled');widget.pack(side='right',padx=(6,0),pady=7);self.buttons.append(widget)
            add('Delete',lambda t=tag:self._delete_version(t),62,installed and tag!=self.selected_version)
            add('Validate',lambda t=tag:self._validate_version(t),72,installed)
            if installed:add('In use' if tag==self.selected_version else 'Use this version',lambda t=tag:self.on_use_version(t) if self.on_use_version else None,128,tag!=self.selected_version)
            else:add('Download & install',lambda r=release:self._download_release(r),140,bool(release.get('assets')),True)
            size=sum(a.get('size',0) for a in release.get('assets',[]))
            label(row,f'{size/1048576:.1f} MB' if size else 'Local' if installed else '—',muted=True).pack(side='right',padx=20)
            divider(self.releases_scroll).pack(fill='x',padx=6)

    def _work(self,operation,done):
        if self.busy:return
        self._busy(True)
        self.mutating=True
        def finish(result,error):
            self.mutating=False;self.cancel_btn.pack_forget()
            self._busy(False)
            if error:
                self.status.configure(text=error);self.log(error,'error');messagebox.showerror('Downloads',error)
            else:done(result)
        self.tasks.submit('release-operation',operation,lambda result:finish(result,None),lambda error:finish(None,error),cancellable=False)

    def _download_release(self,release):
        self.progress.set(0)
        self.cancel_event=threading.Event();self.cancel_btn.configure(state='normal');self.cancel_btn.pack(anchor='w',padx=28,pady=4,before=self.releases_scroll)
        def progress(value,text):
            self.dispatcher.post(lambda:(self.progress.set(value),self.status.configure(text=text)))
        def done(result):
            if result.get('success'):
                self.log('Downloaded and verified '+result['tag']+'.','success');self.refresh_versions(False)
            elif self.cancel_event and self.cancel_event.is_set():
                self.status.configure(text='Download cancelled. Existing release retained.');self.progress.set(0)
            else:messagebox.showerror('Download failed',result.get('error','Unknown download error'))
        self._work(lambda:self.version_manager.download_and_install_version(release,progress,self.cancel_event),done)

    def cancel_download(self):
        if self.cancel_event:
            self.cancel_event.set();self.status.configure(text='Cancelling before commit…');self.cancel_btn.configure(state='disabled')

    def _validate_local(self):
        def operation():
            results=[]
            for folder in sorted(Path(self.version_manager.versions_dir).iterdir()):
                if not folder.is_dir() or folder.name.startswith('.'):continue
                result=self.version_manager.validate_local_version(folder.name,ignore_hashes=False)
                if not result['valid'] and not (folder/'version_meta.json').exists():
                    self.version_manager.rebuild_version_meta(folder.name)
                    result=self.version_manager.validate_local_version(folder.name,ignore_hashes=False)
                results.append(folder.name+': '+('valid' if result['valid'] else result['error']))
            return '\n'.join(results) or 'No version folders found.'
        def done(text):
            self.log(text);messagebox.showinfo('Validate local',text);self.refresh_versions(False)
        self._work(operation,done)

    def _validate_version(self,tag):
        def operation():
            result=self.version_manager.validate_local_version(tag,ignore_hashes=False)
            if not result['valid']:raise ValueError(result['error'])
            return f'{tag}: {len(result["files"])} files verified.'
        self._work(operation,lambda text:(self.status.configure(text=text),self.log(text,'success'),messagebox.showinfo('Validation passed',text)))

    def _delete_version(self,tag):
        if self.busy or tag==self.selected_version:return
        if messagebox.askyesno('Delete version',f'Delete the downloaded {tag} package? Installed game files are retained.'):
            self._work(lambda:self.version_manager.delete_version(tag),lambda _:self.refresh_versions(False))

    def open_versions_dir(self):
        try:os.startfile(self.version_manager.versions_dir)
        except OSError as exc:messagebox.showerror('Open folder',str(exc))

    def shutdown(self):
        self.tasks.close()
        self.dispatcher.close()
