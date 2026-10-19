"""Filterable activity log, marshalled onto the Tk thread."""
import logging
import time
import customtkinter as ctk
from gui.dispatch import UIDispatcher
from gui.theme import BG, PANEL, BORDER, BLUE, label, button

class LogConsole(ctk.CTkFrame):
    def __init__(self,master,**kwargs):
        super().__init__(master,fg_color=BG,corner_radius=0,**kwargs)
        self.dispatcher=UIDispatcher(self)
        self.entries=[]
        bar=ctk.CTkFrame(self,fg_color='transparent');bar.pack(fill='x',padx=28,pady=(24,4))
        title=label(bar,'Log');title.configure(font=ctk.CTkFont(size=22,weight='bold'));title.pack(side='left')
        button(bar,'Clear',self.clear_logs,width=60).pack(side='right',padx=(8,0))
        button(bar,'Copy',self.copy,width=70).pack(side='right',padx=8)
        self.filter=ctk.CTkSegmentedButton(bar,values=['All','Warnings','Errors'],command=lambda _:self.render(),selected_color=BLUE)
        self.filter.set('All');self.filter.pack(side='right')
        label(self,'Live activity and safety checks',muted=True).pack(anchor='w',padx=28)
        self.text_area=ctk.CTkTextbox(self,fg_color='#0C1118',border_width=1,border_color=BORDER,
                                     font=('Consolas',12),wrap='word',corner_radius=12)
        self.text_area.pack(fill='both',expand=True,padx=28,pady=(14,24))
        self.text_area.configure(state='disabled')
    def log(self,message,level='info'):
        logging.getLogger('arcscaler').log({'error':logging.ERROR,'warning':logging.WARNING}.get(level,logging.INFO),message)
        self.dispatcher.post(self._append_log,message,level)
    def _append_log(self,message,level='info'):
        self.entries.append((time.strftime('%H:%M:%S'),level,message))
        self.entries=self.entries[-3000:]
        self.render()
    def render(self):
        selected=self.filter.get()
        self.text_area.configure(state='normal');self.text_area.delete('1.0','end')
        for stamp,level,message in self.entries:
            if selected=='Warnings' and level!='warning' or selected=='Errors' and level not in ('error','crash'):continue
            self.text_area.insert('end',f'{stamp}  {level.upper():8} {message}\n')
        self.text_area.configure(state='disabled');self.text_area.see('end')
    def clear_logs(self):
        self.entries.clear();self.render()
    def copy(self):
        self.clipboard_clear();self.clipboard_append(self.text_area.get('1.0','end-1c'))
