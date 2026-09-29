import tkinter as tk
from tkinter import ttk,messagebox
from daily_challenge import DailyChallenge,VOCAB
from modern_ui import Ranking,Button

class ChallengeUI:
    def __init__(self,root,tabs,path,on_change):
        self.root=root;self.on_change=on_change;self.closed=False;self.pending=''
        self.store=DailyChallenge(path)
        daily=ttk.Frame(tabs,padding=4);board=ttk.Frame(tabs,padding=4)
        tabs.add(daily,text='Daily challenge');tabs.add(board,text='Leaderboard')
        ttk.Label(daily,text='Words of the day',font=('Segoe UI',23,'bold')).pack(anchor='w')
        ttk.Label(daily,text='Ten little ways to make your conversations more interesting.',style='Muted.TLabel').pack(anchor='w',pady=(6,20))
        self.rotation_text=tk.StringVar()
        ttk.Label(daily,textvariable=self.rotation_text,style='Muted.TLabel').pack(anchor='w',pady=(0,12))
        edit=ttk.Frame(daily);edit.pack(fill='x')
        self.entry=ttk.Entry(edit);self.entry.pack(side='left',fill='x',expand=True,pady=3)
        Button(edit,text='Save words',command=self.apply,style='Accent.TButton').pack(side='left',padx=(10,0))
        ttk.Label(daily,text='Up to 10 words or phrases, separated by commas.',style='Muted.TLabel').pack(anchor='w',pady=(6,18))
        self.words=Ranking(daily,meanings=VOCAB);self.words.pack(fill='both',expand=True)
        ttk.Label(board,text='Your daily leaderboard',font=('Segoe UI',23,'bold')).pack(anchor='w')
        ttk.Label(board,text='Your most-used words of the day. Ranked live as you speak.',style='Muted.TLabel').pack(anchor='w',pady=(6,18))
        metric=ttk.Frame(board,style='Card.TFrame',padding=18);metric.pack(fill='x',pady=(0,18))
        self.total=tk.StringVar();self.tried=tk.StringVar();self.leader=tk.StringVar()
        for title,var in [('TOTAL USES',self.total),('WORDS USED',self.tried),('TOP WORD',self.leader)]:
            card=ttk.Frame(metric,style='Card.TFrame');card.pack(side='left',fill='x',expand=True)
            ttk.Label(card,text=title,style='Card.TLabel',font=('Segoe UI',8,'bold')).pack(anchor='w')
            ttk.Label(card,textvariable=var,style='Card.TLabel',foreground='#eef2ff',font=('Segoe UI',21,'bold')).pack(anchor='w',pady=(5,0))
        self.board=Ranking(board);self.board.pack(fill='both',expand=True)
        self.summary=tk.StringVar()
        ttk.Label(board,textvariable=self.summary,style='Muted.TLabel').pack(anchor='w',pady=(12,0))
        ttk.Label(board,text='Live counts can adjust as words are recognized. Confirmed scores save automatically.',style='Muted.TLabel',wraplength=760).pack(anchor='w',pady=(4,0))
        self.refresh(True);self.timer=root.after(1000,self.tick)

    def tick(self):
        if self.closed:return
        if self.store.rollover():self.pending='';self.refresh(True);self.on_change()
        self.update_countdown()
        self.timer=self.root.after(1000,self.tick)

    def apply(self):
        try:self.store.set_words(self.entry.get())
        except (ValueError,OSError) as e:messagebox.showerror('Words of the day',str(e));return
        self.pending='';self.refresh(True);self.on_change()

    def preview(self,text):
        if self.pending==text:return
        self.pending=text;self.refresh()

    def feed(self,text):
        old=self.store.day;self.store.feed(text);self.pending='';self.refresh(old!=self.store.day)

    def live_counts(self):
        extra=self.store.preview(self.pending);saved=self.store.counts()
        return {w:saved.get(w,0)+extra.get(w,0) for w in self.store.words}

    def refresh(self,edit=False):
        if edit:self.entry.delete(0,'end');self.entry.insert(0,', '.join(self.store.words))
        counts=self.live_counts();rows=sorted(counts.items(),key=lambda item:(-item[1],item[0]))
        self.board.set_rows(rows);self.words.set_rows([(w,counts[w]) for w in self.store.words])
        self.total.set(str(sum(counts.values())));self.tried.set(f'{sum(n>0 for n in counts.values())} / {len(counts)}')
        leader=rows[0][0].capitalize() if rows and rows[0][1]>0 else 'Your first word'
        self.leader.set(leader if len(leader)<19 else leader[:17]+'…')
        self.update_countdown()

    def update_countdown(self):
        left=self.store.seconds_remaining;hours,left=divmod(left,3600);minutes,seconds=divmod(left,60)
        text=f'New words in {hours:02}:{minutes:02}:{seconds:02}  •  Rotates every 24 hours'
        self.rotation_text.set(text);self.summary.set('LIVE  •  '+text)

    def close(self):
        self.closed=True;self.root.after_cancel(self.timer);self.store.close()
