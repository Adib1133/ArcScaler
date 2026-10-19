"""ArcScaler's shared native widget palette and focus behavior."""
import customtkinter as ctk

BG='#090A0C'
SIDE='#0E1013'
PANEL='#16191E'
BORDER='#343A43'
TEXT='#EEF3FA'
MUTED='#8B9CB6'
BLUE='#3985FF'
GREEN='#42D9A0'
RED='#FF7887'

def label(parent,text,muted=False,**kwargs):
    return ctk.CTkLabel(parent,text=text,text_color=MUTED if muted else TEXT,
                        font=ctk.CTkFont(family='Segoe UI',size=12),**kwargs)

class GlassButton(ctk.CTkButton):
    """Visible keyboard focus and Enter/Space activation."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._rest_border=self.cget('border_color')
        self._canvas.configure(takefocus=1)
        self._canvas.bind('<FocusIn>',lambda _:self.configure(border_color='#9BCBFF',border_width=2),add='+')
        self._canvas.bind('<FocusOut>',lambda _:self.configure(border_color=self._rest_border,border_width=1),add='+')
        self._canvas.bind('<Return>',lambda _:self.invoke(),add='+')
        self._canvas.bind('<space>',lambda _:self.invoke(),add='+')


def button(parent,text,command,primary=False,**kwargs):
    return GlassButton(parent,text=text,command=command,height=36,corner_radius=14,
                        fg_color=BLUE if primary else '#22262D',hover_color='#285FAB' if primary else '#30363F',
                        border_width=1,border_color='#79ADFF' if primary else '#454E5A',text_color=TEXT,
                        font=ctk.CTkFont(family='Segoe UI',size=12,weight='bold'),**kwargs)

def card(parent):
    return ctk.CTkFrame(parent,fg_color=PANEL,border_width=1,border_color=BORDER,corner_radius=18)

def enabled_tree(widget, enabled):
    """Disable actual Tk controls and keyboard traversal, not just their color."""
    for child in widget.winfo_children():
        if isinstance(child,(ctk.CTkOptionMenu,ctk.CTkSlider,ctk.CTkSwitch,ctk.CTkButton,ctk.CTkCheckBox,ctk.CTkEntry,ctk.CTkSegmentedButton)):
            child.configure(state='normal' if enabled else 'disabled')
        if isinstance(child,ctk.CTkLabel):
            child.configure(text_color=TEXT if enabled else '#515967')
        if isinstance(child,ctk.CTkSlider):
            child.configure(progress_color=BLUE if enabled else '#444B57',button_color=BLUE if enabled else '#555D69')
        try:
            child.tk.call(child._w,'configure','-takefocus',1 if enabled and child.winfo_class() in ('Entry','Button','Scale') else 0)
        except Exception:
            pass  # Container canvases have no focus option on some Tk builds.
        enabled_tree(child,enabled)


def badge(parent,text,positive=False):
    shell=ctk.CTkFrame(parent,fg_color='#0D201F' if positive else PANEL,
        border_width=1,border_color='#19564A' if positive else BORDER,corner_radius=14)
    item=label(shell,text,muted=not positive,height=24)
    if positive:item.configure(text_color='#2BD9A0')
    item.pack(padx=10)
    return shell


class ContentFrame(ctk.CTkScrollableFrame):
    """A fixed pane until its contents actually exceed the available height."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._parent_canvas.configure(yscrollcommand=self._scroll_changed)

    def _scroll_changed(self,first,last):
        self._scrollbar.set(first,last)
        if float(first)<=0 and float(last)>=1:
            self._scrollbar.grid_remove()
        else:
            self._scrollbar.grid()


def gauge_image(scale,enabled=True,size=(135,155)):
    """Supersampled gradient arc with round ends; no coarse Tk arc segments."""
    import math
    from PIL import Image,ImageDraw
    factor=4
    img=Image.new('RGB',(size[0]*factor,size[1]*factor),PANEL)
    draw=ImageDraw.Draw(img)
    radius=55.5*factor;center=67.5*factor;stroke=8*factor
    def point(t):
        angle=math.radians(135+270*t)
        return center+radius*math.cos(angle),center+radius*math.sin(angle)
    def cap(t,color):
        x,y=point(t);r=stroke/2
        draw.ellipse((x-r,y-r,x+r,y+r),fill=color)
    points=[point(i/540) for i in range(541)]
    draw.line(points,fill='#252E3D',width=stroke,joint='curve');cap(0,'#252E3D');cap(1,'#252E3D')
    end=max(0,min(1,scale));count=max(1,int(540*end))
    def color(t):
        return tuple(round(a+(b-a)*t) for a,b in zip((29,195,240),(68,108,255))) if enabled else (81,89,103)
    for i in range(count):
        t=i/count*end;next_t=(i+1)/count*end
        cap(t,color(t))
    if end:cap(0,color(0));cap(end,color(end))
    return img.resize(size,Image.Resampling.LANCZOS)


def divider(parent,vertical=False):
    import tkinter as tk
    return tk.Frame(parent,bg=BORDER,bd=0,highlightthickness=0,**({'width':1} if vertical else {'height':1}))
