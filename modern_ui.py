import tkinter as tk
from tkinter import ttk

BG='#0b1020'; CARD='#151d31'; LINE='#26314a'; MUTED='#93a3bd'; TEXT='#eef2ff'; ACCENT='#a5a0ff'

def rounded(canvas,x,y,w,h,r=12,**kw):
    return canvas.create_polygon(x+r,y,x+w-r,y,x+w,y,x+w,y+r,x+w,y+h-r,x+w,y+h,x+w-r,y+h,x+r,y+h,x,y+h,x,y+h-r,x,y+r,x,y,smooth=True,**kw)

class Button(tk.Canvas):
    def __init__(self,parent,text='',command=None,style='',**kw):
        self.text=text; self.command=command; self.disabled=False; self.hover=False
        self.accent='Accent' in style; self.selected=False
        super().__init__(parent,height=40,width=max(100,len(text)*8+28),background=BG,highlightthickness=0,takefocus=1,cursor='hand2')
        self.bind('<Configure>',lambda e:self.draw()); self.bind('<Enter>',lambda e:self.enter(True)); self.bind('<Leave>',lambda e:self.enter(False))
        self.bind('<Button-1>',lambda e:self.invoke()); self.bind('<Return>',lambda e:self.invoke()); self.bind('<space>',lambda e:self.invoke())
        self.bind('<FocusIn>',lambda e:self.draw());self.bind('<FocusOut>',lambda e:self.draw())
        self.draw()
    def enter(self,hover):self.hover=hover;self.draw()
    def invoke(self):
        if not self.disabled and self.command:self.command()
    def state(self,states):
        if 'disabled' in states:self.disabled=True
        if '!disabled' in states:self.disabled=False
        self.draw()
    def draw(self):
        self.delete('all'); w=max(self.winfo_width(),int(self['width'])); h=40
        fill='#262f49' if self.hover else CARD
        if self.accent or self.selected:fill=ACCENT if self.accent else '#302d54'
        if self.disabled:fill='#192237'
        rounded(self,1,1,w-2,h-2,12,fill=fill,outline=ACCENT if self.focus_get()==self else '')
        self.create_text(w/2,h/2,text=self.text,fill=MUTED if self.disabled else '#14112b' if self.accent else TEXT,font=('Segoe UI',10,'bold'))

class Navigation(ttk.Frame):
    def __init__(self,parent):
        super().__init__(parent); self.pages=[]; self.buttons=[];self.current=None
        self.rail=ttk.Frame(self,width=170);self.rail.pack(side='left',fill='y',padx=(0,24));self.rail.pack_propagate(False)
        ttk.Label(self.rail,text='WORKSPACE',style='Eyebrow.TLabel').pack(anchor='w',pady=(12,14),padx=8)
        self.body=ttk.Frame(self);self.body.pack(side='left',fill='both',expand=True)
        footer=ttk.Frame(self.rail);footer.pack(side='bottom',fill='x',padx=8,pady=10)
        ttk.Label(footer,text='●  PRIVATE BY DESIGN',foreground='#63e3c0',font=('Segoe UI',8,'bold')).pack(anchor='w')
        ttk.Label(footer,text='Your voice stays here.',style='Muted.TLabel',font=('Segoe UI',9)).pack(anchor='w',pady=(6,0))
    def add(self,page,text):
        name=text.strip(); names={'Live transcript':'Live speech','Words & sound':'Settings','Daily challenge':'Words of the day','Leaderboard':'Leaderboard'}
        label=names.get(name,name); index=len(self.pages); self.pages.append(page)
        b=Button(self.rail,text=label,command=lambda:self.select(index));b.pack(fill='x',pady=4);self.buttons.append(b)
        if self.current is None:self.select(0)
    def select(self,index):
        if isinstance(index,str):index=int(index)
        if self.current is not None:self.pages[self.current].pack_forget()
        self.current=index;self.pages[index].pack(in_=self.body,fill='both',expand=True)
        for i,b in enumerate(self.buttons):b.selected=i==index;b.draw()

class ScrollPage(ttk.Frame):
    def __init__(self,parent):
        super().__init__(parent)
        self.canvas=tk.Canvas(self,bg=BG,highlightthickness=0);self.canvas.pack(side='left',fill='both',expand=True)
        bar=ttk.Scrollbar(self,command=self.canvas.yview);bar.pack(side='right',fill='y');self.canvas.configure(yscrollcommand=bar.set)
        self.body=ttk.Frame(self.canvas,padding=(0,0,12,12));self.item=self.canvas.create_window(0,0,anchor='nw',window=self.body)
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.item,width=e.width))
        self.body.bind('<Configure>',lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.bind_all('<MouseWheel>',self.wheel,add='+')
    def wheel(self,e):
        if self.winfo_ismapped() and str(e.widget).startswith(str(self)) and e.widget.winfo_class()!='TCombobox':self.canvas.yview_scroll(-int(e.delta/120),'units')

class Ranking(ttk.Frame):
    def __init__(self,parent,meanings=None):
        super().__init__(parent);self.rows=[];self.meanings=meanings
        self.canvas=tk.Canvas(self,bg=BG,highlightthickness=0);self.canvas.pack(side='left',fill='both',expand=True)
        bar=ttk.Scrollbar(self,command=self.canvas.yview);bar.pack(side='right',fill='y');self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.bind('<Configure>',lambda e:self.draw())
        self.canvas.bind('<MouseWheel>',lambda e:self.canvas.yview_scroll(-int(e.delta/120),'units'))
    def set_rows(self,rows):self.rows=list(rows);self.draw()
    def get_children(self):return tuple(w for w,n in self.rows)
    def draw(self):
        c=self.canvas;c.delete('all');width=max(300,c.winfo_width()-12);height=74 if self.meanings else 66
        top=max((n for w,n in self.rows),default=1) or 1
        for i,(word,count) in enumerate(self.rows):
            y=i*(height+8)+2;color=['#f4cd78','#b7c9e7','#cda58e'][i] if i<3 else MUTED
            rounded(c,0,y,width,height,14,fill=CARD,outline=LINE if i==0 else '')
            c.create_text(28,y+height/2,text=f'{i+1:02}',fill=color,font=('Segoe UI',12,'bold'))
            c.create_text(62,y+23,text=word.capitalize(),anchor='w',fill=TEXT,font=('Segoe UI',14,'bold'))
            if self.meanings:
                hint=self.meanings.get(word,'Your own word or phrase.')
                if len(hint)>int((width-190)/6):hint=hint[:max(12,int((width-190)/6)-1)]+'…'
                c.create_text(62,y+48,text=hint,anchor='w',fill=MUTED,font=('Segoe UI',9))
            else:
                length=max(0,(width-180)*count/top)
                rounded(c,62,y+45,width-180,4,2,fill=LINE,outline='')
                if length>1:rounded(c,62,y+45,length,4,2,fill=ACCENT,outline='')
            c.create_text(width-36,y+25,text=str(count),fill=TEXT,font=('Segoe UI',21,'bold'))
            c.create_text(width-36,y+49,text='USES',fill=MUTED,font=('Segoe UI',8))
        c.configure(scrollregion=(0,0,width,len(self.rows)*(height+8)+4))
