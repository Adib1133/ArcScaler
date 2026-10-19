"""Compact file-action review; detailed paths remain available without wrapping rows."""
import customtkinter as ctk
from tkinter import ttk
from core.workflows import route_summary
from gui.theme import button, label


def show_preview(parent, plan, force, on_install):
    dialog = ctk.CTkToplevel(parent)
    dialog.title('ArcScaler · Review installation')
    scale = dialog._get_window_scaling()
    width = min(760, int(dialog.winfo_screenwidth()/scale)-48)
    height = min(620, int(dialog.winfo_screenheight()/scale)-80)
    dialog.geometry(f'{width}x{height}')
    dialog.minsize(min(520,width), min(450,height))
    dialog.transient(parent.winfo_toplevel());dialog.grab_set()
    summary = label(dialog, route_summary(plan['settings'], plan), wraplength=700, justify='left')
    summary.pack(fill='x', padx=16, pady=12)
    table = ctk.CTkFrame(dialog);table.pack(fill='both', expand=True, padx=16)
    style = ttk.Style(dialog)
    style.theme_use('clam')
    style.configure('ArcScaler.Treeview',background='#16191E',fieldbackground='#16191E',foreground='#EEF3FA',rowheight=28,font=('Segoe UI',10))
    style.configure('ArcScaler.Treeview.Heading',background='#242830',foreground='#EEF3FA',font=('Segoe UI',10,'bold'))
    style.map('ArcScaler.Treeview',background=[('selected','#285FAB')])
    tree = ttk.Treeview(table, columns=('action','file','location','reason'), show='headings', height=8,style='ArcScaler.Treeview')
    for name, width in [('action',70),('file',140),('location',230),('reason',240)]:
        tree.heading(name, text=name.title());tree.column(name, width=width, minwidth=65)
    for row in plan.get('file_actions', []):tree.insert('', 'end', values=tuple(row[k] for k in ('action','file','location','reason')))
    vertical=ttk.Scrollbar(table,orient='vertical',command=tree.yview)
    horizontal=ttk.Scrollbar(table,orient='horizontal',command=tree.xview)
    tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
    table.grid_rowconfigure(0,weight=1);table.grid_columnconfigure(0,weight=1)
    tree.grid(row=0,column=0,sticky='nsew');vertical.grid(row=0,column=1,sticky='ns');horizontal.grid(row=1,column=0,sticky='ew')
    messages = (['Force will override: '+e for e in plan['errors']] if force else plan['errors']) + plan['notes']
    detail = ctk.CTkTextbox(dialog, height=110, wrap='word')
    detail.pack(fill='x',padx=16,pady=8)
    detail.insert('1.0','\n'.join(messages) or 'Original files are backed up. Reused runtime files stay untouched.')
    detail.configure(state='disabled')
    acknowledgement = None
    if force:
        acknowledgement = ctk.CTkCheckBox(dialog,text='I accept the listed overrides and have verified this offline setup.')
        acknowledgement.pack(anchor='w',padx=16,pady=6)
    actions=ctk.CTkFrame(dialog,fg_color='transparent');actions.pack(fill='x',padx=16,pady=12)
    def install():
        if acknowledgement is not None and not acknowledgement.get():return
        dialog.destroy();on_install()
    button(actions,'Confirm force injection' if force else 'Confirm installation',install,primary=True).pack(side='right')
    button(actions,'Cancel',dialog.destroy).pack(side='right',padx=8)
    dialog.bind('<Configure>',lambda event:summary.configure(wraplength=max(250,int(event.width/summary._get_widget_scaling())-40)) if event.widget is dialog else None)
    return dialog
