import os, sys, json, re, queue, threading, time, ctypes, shutil
from pathlib import Path
from collections import deque
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import winsound
import sounddevice as sd
import numpy as np
import soundfile as sf
from fast_audio import FastPlayer
from keyword_engine import AlertMerger
from nvidia_engine import NvidiaServer, TranscriptEvents, StreamingDetector, MODELS
from challenge_ui import ChallengeUI
from modern_ui import Button as ModernButton, Navigation, ScrollPage
from vosk_engine import VoskEngine

BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
PRESETS = {
    'Swearing': 'fuck, fucking, fucked, fucker, motherfucker, shit, shitty, bullshit, bitch, bastard, asshole, arsehole, cunt, dick, prick, bollocks, wanker, piss, pissed',
    'Mild swearing': 'damn, bloody, crap, hell, bugger',
    'Insults': 'idiot, moron, stupid, dumb, loser',
    'Filler words': 'um, uh, like, literally, you know, i mean',
}

def tokens(s):
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", s.lower().replace('’', "'"))

class Detector:
    def __init__(self, phrases):
        self.phrases = [(p, tokens(p)) for p in phrases if tokens(p)]
        self.tail = []
    def feed(self, text):
        new = tokens(text)
        combined = self.tail + new
        boundary = len(self.tail)
        found = []
        for label, phrase in self.phrases:
            for i in range(max(0, boundary-len(phrase)+1), len(combined)-len(phrase)+1):
                if combined[i:i+len(phrase)] == phrase:
                    found.append(label)
        n = max((len(p) for _, p in self.phrases), default=1)-1
        self.tail = combined[-n:] if n else []
        return found

class LiveDetector:
    """Compare each complete partial hypothesis without replaying its alerts.

    Keep per-phrase occurrence high-water marks until the utterance is final.
    Unlike positions, these survive recognition inserting/removing earlier words.
    """
    def __init__(self, phrases):
        self.phrases = [(p, tokens(p)) for p in phrases if tokens(p)]
        self.tail = []
        self.alerted = {}

    def update(self, text, final=False):
        current = tokens(text)
        combined = self.tail + current
        boundary = len(self.tail)
        fresh = []
        for label, phrase in self.phrases:
            count = sum(combined[i:i+len(phrase)] == phrase
                        for i in range(max(0, boundary-len(phrase)+1), len(combined)-len(phrase)+1))
            previous = self.alerted.get(label, 0)
            fresh.extend([label] * max(0, count-previous))
            self.alerted[label] = max(previous, count)
        if final:
            n = max((len(p) for _, p in self.phrases), default=1)-1
            self.tail = combined[-n:] if n else []
            self.alerted.clear()
        return fresh

class CarefulDetector(LiveDetector):
    """Reject transient guesses; final matches also require word confidence."""
    def __init__(self, phrases):
        super().__init__(phrases)
        self.pending = {}
        self.emitted = set()
        self.tail_conf = []

    def update(self, text, audio_time=0, final=False, confidences=None):
        current = tokens(text); combined = self.tail + current
        boundary = len(self.tail)
        conf = self.tail_conf + (list(confidences) if confidences is not None else [0]*len(current))
        found = []; visible = set()
        for label, phrase in self.phrases:
            count = 0
            for i in range(max(0,boundary-len(phrase)+1),len(combined)-len(phrase)+1):
                if combined[i:i+len(phrase)] != phrase: continue
                count += 1; key = (label,count); visible.add(key)
                first = self.pending.setdefault(key,audio_time)
                if final:
                    reliable = len(conf)==len(combined) and min(conf[i:i+len(phrase)]) >= .85
                else:
                    reliable = audio_time-first >= .3 and i+len(phrase)<len(combined)
                if reliable and key not in self.emitted:
                    found.append(label); self.emitted.add(key)
        self.pending = {k:v for k,v in self.pending.items() if k in visible}
        if final:
            n = max((len(p) for _,p in self.phrases),default=1)-1
            self.tail = combined[-n:] if n else []
            self.tail_conf = conf[-n:] if n else []
            self.pending.clear(); self.emitted.clear()
        return found

def trigger_spans(text, phrases):
    """Character spans preserve the exact visible text, including punctuation."""
    words = list(re.finditer(r"[a-z0-9]+(?:'[a-z0-9]+)?", text.lower().replace('’', "'")))
    normalized = [m.group() for m in words]
    spans = set()
    for label in phrases:
        phrase = tokens(label)
        if not phrase: continue
        for i in range(len(words)-len(phrase)+1):
            if normalized[i:i+len(phrase)] == phrase:
                spans.add((words[i].start(), words[i+len(phrase)-1].end()))
    return sorted(spans)

class App:
    def __init__(self, root):
        self.root = root
        self.data = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'WordWatch'
        self.data.mkdir(parents=True, exist_ok=True)
        self.cfgfile = self.data / 'settings.json'
        try: self.cfg = json.loads(self.cfgfile.read_text('utf-8'))
        except (OSError, ValueError): self.cfg = {}
        self.events = queue.Queue()
        self.player = FastPlayer(self.events)
        self.alert_merger = AlertMerger()
        self.config_snapshot = ((), 'Careful')
        self.catches = deque(maxlen=12)
        self.keyword_worker = None
        self.transcript_worker = None
        self.listening_enabled = False
        self.latest_audio_time = 0.0
        self.stop = threading.Event()
        self.worker = None
        self.backlog = deque(maxlen=2000)
        self.hits = 0
        self.live_text = ''
        self.engine = None
        self.sound = self.cfg.get('sound', '')
        self.tray = None
        self.playlock = threading.Lock()
        self.input_device = None
        root.title('WordWatch — live speech coach')
        root.geometry('1220x850'); root.minsize(1040, 740)
        root.configure(bg='#0b1020')
        style = ttk.Style(); style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10), background='#0b1020', foreground='#e5edf9', bordercolor='#29374f', lightcolor='#29374f', darkcolor='#29374f')
        style.configure('TFrame', background='#0b1020')
        style.configure('Card.TFrame', background='#151d31')
        style.configure('TLabel', background='#0b1020', foreground='#e5edf9')
        style.configure('Muted.TLabel', foreground='#9aacc5')
        style.configure('Card.TLabel', background='#151d31', foreground='#9aacc5')
        style.configure('Status.TLabel', background='#151d31', foreground='#63e3c0', font=('Segoe UI', 11, 'bold'))
        style.configure('TButton', padding=(14, 9), background='#24344d', foreground='#e5edf9', borderwidth=0)
        style.map('TButton', background=[('active', '#334967'), ('disabled', '#19263b')], foreground=[('disabled', '#677892')])
        style.configure('Accent.TButton', background='#63e3c0', foreground='#09251e', font=('Segoe UI', 10, 'bold'))
        style.map('Accent.TButton', background=[('active', '#91f0d5'), ('disabled', '#243b42')], foreground=[('disabled', '#8ba6a4')])
        style.configure('Title.TLabel', font=('Segoe UI', 25, 'bold'))
        style.configure('Eyebrow.TLabel', foreground='#63e3c0', font=('Segoe UI', 9, 'bold'))
        style.configure('TNotebook', borderwidth=0, background='#0b1020')
        style.configure('TNotebook.Tab', padding=(20, 12), background='#151d31', foreground='#9aacc5')
        style.map('TNotebook.Tab', background=[('selected', '#24344d')], foreground=[('selected', '#63e3c0')])
        style.configure('TEntry', padding=7, fieldbackground='#19263b', foreground='#e5edf9', insertcolor='#e5edf9')
        style.configure('TCombobox', padding=7, fieldbackground='#19263b', background='#24344d', foreground='#e5edf9', arrowcolor='#63e3c0')
        style.map('TCombobox', fieldbackground=[('readonly', '#19263b')], foreground=[('readonly', '#e5edf9')], selectbackground=[('readonly', '#19263b')], selectforeground=[('readonly', '#e5edf9')])
        style.configure('TCheckbutton', padding=(0, 3), background='#0b1020', foreground='#e5edf9')
        style.map('TCheckbutton', background=[('active', '#19263b')])
        root.option_add('*TCombobox*Listbox.background', '#19263b')
        root.option_add('*TCombobox*Listbox.foreground', '#e5edf9')
        root.option_add('*TCombobox*Listbox.selectBackground', '#334967')
        outer = ttk.Frame(root, padding=26); outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='WORDWATCH  /  PERSONAL SPEECH STUDIO', style='Eyebrow.TLabel').pack(anchor='w')
        ttk.Label(outer, text='WordWatch', style='Title.TLabel').pack(anchor='w', pady=(2, 2))
        ttk.Label(outer, text='Speak with intention. Build a better vocabulary.', style='Muted.TLabel').pack(anchor='w', pady=(0, 18))
        bar = ttk.Frame(outer, style='Card.TFrame', padding=14); bar.pack(fill='x')
        self.status = tk.StringVar(value='Ready')
        ttk.Label(bar, textvariable=self.status, style='Status.TLabel').pack(side='left', expand=True, fill='x')
        self.startbtn = ModernButton(bar, text='Start listening', style='Accent.TButton', command=self.start); self.startbtn.pack(side='left', padx=6)
        self.pausebtn = ModernButton(bar, text='Pause', command=self.pause); self.pausebtn.pack(side='left')
        tabs = Navigation(outer); self.tabs = tabs; tabs.pack(fill='both', expand=True, pady=18)
        live = ttk.Frame(tabs, padding=4); settings_page = ScrollPage(tabs); settings = settings_page.body
        tabs.add(live, text=' Live transcript '); tabs.add(settings_page, text=' Words & sound ')
        devices = ttk.Frame(settings); devices.pack(fill='x', pady=(10, 0))
        ttk.Label(devices, text='Microphone').grid(row=0, column=0, sticky='w')
        ttk.Label(devices, text='Alert output').grid(row=0, column=1, sticky='w')
        self.mic = ttk.Combobox(devices, state='readonly', width=26)
        self.output = ttk.Combobox(devices, state='readonly', width=26)
        self.mic.grid(row=1, column=0, sticky='ew', padx=(0, 8)); self.output.grid(row=1, column=1, sticky='ew')
        devices.columnconfigure(0, weight=1); devices.columnconfigure(1, weight=1)
        self.refresh_devices()
        self.mic.bind('<<ComboboxSelected>>', self.change_mic)
        self.output.bind('<<ComboboxSelected>>', lambda e: self.save())
        ModernButton(devices, text='Refresh devices', command=self.refresh_devices).grid(row=1, column=2, padx=(8, 0))
        modelrow = ttk.Frame(settings); modelrow.pack(fill='x', pady=(10,0))
        ttk.Label(modelrow, text='Transcription model').pack(side='left',padx=(0,10))
        self.model_choice = ttk.Combobox(modelrow,state='readonly',values=[v[0] for v in MODELS.values()],width=57)
        self.model_choice.set(MODELS.get(self.cfg.get('model','vosk'),MODELS['vosk'])[0])
        self.model_choice.pack(side='left',fill='x',expand=True)
        self.model_choice.bind('<<ComboboxSelected>>',self.change_mic)
        ttk.Separator(settings).pack(fill='x',pady=20)
        ttk.Label(live,text='Live speech',font=('Segoe UI',23,'bold')).pack(anchor='w',pady=(0,14))
        self.stats = tk.StringVar(value='0 / 2,000 words  •  0 alerts this session')
        ttk.Label(live, textvariable=self.stats, style='Muted.TLabel').pack(anchor='w')
        textframe = ttk.Frame(live); textframe.pack(fill='both', expand=True, pady=12)
        self.transcript = tk.Text(textframe, wrap='word', font=('Segoe UI', 15), bg='#151d31', fg='#e5edf9', selectbackground='#334967', relief='flat', state='disabled', padx=20, pady=20, spacing1=5, spacing3=6, height=6, borderwidth=0, highlightthickness=1, highlightbackground='#29374f')
        scrollbar = ttk.Scrollbar(textframe, command=self.transcript.yview)
        self.transcript.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y'); self.transcript.pack(side='left', fill='both', expand=True)
        self.transcript.tag_configure('provisional', foreground='#8da6c7')
        self.transcript.tag_configure('trigger', foreground='#ffdbdf', background='#79344c', font=('Segoe UI', 15, 'bold'))
        self.transcript.tag_configure('placeholder', foreground='#7186a4')
        self.partial = tk.StringVar(value='LIVE WORDS update as you speak  •  Pink highlights mark triggers')
        ttk.Label(live, textvariable=self.partial, style='Muted.TLabel').pack(anchor='w')
        self.last = tk.StringVar(value='No matches yet.')
        ttk.Label(live, textvariable=self.last, wraplength=730).pack(anchor='w', pady=8)
        self.catchlog = tk.Text(live, height=2, wrap='word', font=('Segoe UI', 10), bg='#151d31', fg='#9aacc5', relief='flat', state='disabled', padx=10, pady=7)
        self.catchlog.pack(fill='x', pady=(0, 8))
        self.catchlog.tag_configure('catch', foreground='#ffdbdf', background='#79344c', font=('Segoe UI', 10, 'bold'))
        ModernButton(live, text='Clear transcript', command=self.clear).pack(anchor='w')
        ttk.Label(settings, text='Choose what you want to catch', font=('Segoe UI', 14, 'bold')).pack(anchor='w', pady=(0, 4))
        ttk.Label(settings, text='Enable a category, then edit its comma-separated words.', style='Muted.TLabel').pack(anchor='w')
        categorygrid = ttk.Frame(settings); categorygrid.pack(fill='x', pady=6)
        categorygrid.columnconfigure(0, weight=1); categorygrid.columnconfigure(1, weight=1)
        self.enabled = {}; self.entries = {}
        for idx, (category, default) in enumerate(PRESETS.items()):
            cell = ttk.Frame(categorygrid, padding=(0, 2, 14, 5)); cell.grid(row=idx//2, column=idx%2, sticky='ew')
            v = tk.BooleanVar(value=self.cfg.get('enabled', {}).get(category, category == 'Swearing'))
            self.enabled[category] = v
            ttk.Checkbutton(cell, text=category, variable=v).pack(anchor='w')
            e = ttk.Entry(cell); e.insert(0, self.cfg.get('categories', {}).get(category, default)); e.pack(fill='x'); self.entries[category] = e
        ttk.Label(settings, text='Your own words or phrases — separate with commas:').pack(anchor='w', pady=(10, 2))
        self.custom = ttk.Entry(settings); self.custom.insert(0, self.cfg.get('custom', '')); self.custom.pack(fill='x')
        sounds = ttk.Frame(settings); sounds.pack(fill='x', pady=10)
        ModernButton(sounds, text='Choose alert sound…', command=self.choose_sound).pack(side='left')
        ModernButton(sounds, text='Test sound', command=self.play).pack(side='left', padx=5)
        ModernButton(sounds, text='Use beep', command=self.beep).pack(side='left')
        self.soundlabel = tk.StringVar(value=Path(self.sound).name if self.sound else 'Default beep')
        ttk.Label(settings, textvariable=self.soundlabel).pack(anchor='w')
        self.trim_sound = tk.BooleanVar(value=self.cfg.get('trim_sound',True))
        ttk.Checkbutton(settings,text='Skip quiet lead-in for immediate sound',variable=self.trim_sound,command=self.save).pack(anchor='w')
        tuning = ttk.Frame(settings); tuning.pack(fill='x', pady=(8, 0))
        ttk.Label(tuning, text='Detection').pack(side='left', padx=(0, 8))
        self.mode = ttk.Combobox(tuning, state='readonly', values=['Careful', 'Fast', 'Transcript only'], width=18)
        self.mode.set('Fast' if self.cfg.get('mode') in ('Balanced','Sensitive') else self.cfg.get('mode','Fast')); self.mode.pack(side='left')
        self.mode.bind('<<ComboboxSelected>>',lambda e:self.save())
        ttk.Label(tuning, text='Alert volume').pack(side='left', padx=(18, 8))
        self.volume = tk.DoubleVar(value=self.cfg.get('volume', 80))
        self.volume_save_job = None
        self.volume_label = tk.StringVar(value=f'{round(self.volume.get())}%')
        ttk.Scale(tuning, from_=0, to=100, variable=self.volume, length=140, command=self.change_volume).pack(side='left')
        ttk.Label(tuning, textvariable=self.volume_label, width=5).pack(side='left',padx=6)
        ttk.Label(settings, text='Fast uses live matches; ambiguous prefixes wait for a complete word. Transcript only mutes alerts.', style='Muted.TLabel').pack(anchor='w', pady=(4, 0))
        ModernButton(settings, text='Save word settings', style='Accent.TButton', command=self.save).pack(anchor='w', pady=10)
        self.runtime = tk.StringVar(value='LOCAL ONLY • 2,000-word memory • Minimize to keep listening; close to quit')
        ttk.Label(outer, textvariable=self.runtime, style='Muted.TLabel', wraplength=980).pack(anchor='w')
        self.detector = LiveDetector(self.phrases())
        self.challenge = ChallengeUI(root,tabs,self.data/'daily-challenge.sqlite3',self.render)
        self.transcript.tag_configure('challenge',foreground='#63e3c0',underline=True)
        self.transcript.tag_raise('trigger')
        self.config_snapshot = (tuple(self.phrases()), self.mode.get())
        if '--smoke-test' not in sys.argv: self.configure_audio()
        self.render()
        root.protocol('WM_DELETE_WINDOW', self.quit)
        root.after(100, self.poll)
        if '--smoke-test' not in sys.argv: root.after(500, self.start)

    def phrases(self):
        values = [e.get() for k,e in self.entries.items() if self.enabled[k].get()]
        values.append(self.custom.get())
        return sorted(set(p.strip().lower() for v in values for p in v.split(',') if p.strip()))

    def save(self):
        self.cfg = dict(enabled={k:v.get() for k,v in self.enabled.items()}, categories={k:e.get() for k,e in self.entries.items()}, custom=self.custom.get(), sound=self.sound, mic=self.mic.get(), output=self.output.get(), mode=self.mode.get(), volume=round(self.volume.get()), model=self.selected_model(),trim_sound=self.trim_sound.get())
        try:
            tmp = self.cfgfile.with_suffix('.tmp'); tmp.write_text(json.dumps(self.cfg, indent=2), 'utf-8'); tmp.replace(self.cfgfile)
            if [p for p, _ in self.detector.phrases] != self.phrases():
                self.detector = LiveDetector(self.phrases())
                self.detector.update(self.live_text)
            self.render()
            self.config_snapshot = (tuple(self.phrases()), self.mode.get())
            self.configure_audio()
            self.last.set('Word settings saved.')
        except OSError as e: messagebox.showerror('Could not save settings', str(e))

    def choose_sound(self):
        name = filedialog.askopenfilename(title='Choose a short alert sound', filetypes=[('Audio', '*.wav *.mp3 *.flac *.ogg'), ('All files', '*.*')])
        if not name: return
        try:
            with sf.SoundFile(name) as f:
                if len(f) / f.samplerate > 30: raise ValueError('Choose a sound no longer than 30 seconds.')
            target = self.data / ('alert' + Path(name).suffix.lower())
            if Path(name).resolve() != target.resolve(): shutil.copy2(name, target)
            self.sound = str(target); self.soundlabel.set(Path(name).name); self.save(); self.play()
        except (OSError, ValueError, RuntimeError) as e: messagebox.showerror('Could not import sound', str(e))

    def beep(self):
        self.sound = ''; self.soundlabel.set('Default beep'); self.save(); self.play()

    def play(self):
        self.configure_audio()
        self.player.trigger()

    def configure_audio(self):
        self.player.configure(self.device_map.get(self.output.get()), self.sound, self.volume.get()/100,self.trim_sound.get())

    def selected_model(self):
        return next((k for k,v in MODELS.items() if v[0]==self.model_choice.get()),'nemotron-en')

    def change_volume(self, value=None):
        volume = round(self.volume.get())
        self.volume_label.set(f'{volume}%')
        self.player.set_volume(volume/100)
        if self.volume_save_job: self.root.after_cancel(self.volume_save_job)
        self.volume_save_job = self.root.after(350, self.persist_volume)

    def persist_volume(self):
        self.volume_save_job = None
        self.cfg['volume'] = round(self.volume.get())
        try:
            tmp = self.cfgfile.with_suffix('.tmp'); tmp.write_text(json.dumps(self.cfg,indent=2),'utf-8');tmp.replace(self.cfgfile)
        except OSError as e: self.last.set('Could not save volume: '+str(e))

    def report_hit(self, word, audio_time, source):
        if not self.listening_enabled: return
        if self.alert_merger.accept(word, audio_time, source):
            # Trigger the already-open output directly, without waiting for Tk.
            self.player.trigger()
            self.events.put(('hit', (word, source)))

    def refresh_devices(self):
        oldmic = self.mic.get() or self.cfg.get('mic', 'Windows default')
        oldout = self.output.get() or self.cfg.get('output', 'Windows default')
        self.device_map = {'Windows default': None}
        inputs = ['Windows default']; outputs = ['Windows default']
        try:
            hosts = sd.query_hostapis()
            for i,d in enumerate(sd.query_devices()):
                label = f"{d['name']} [{hosts[d['hostapi']]['name']}]"
                self.device_map[label] = i
                if d['max_input_channels']: inputs.append(label)
                if d['max_output_channels']: outputs.append(label)
        except Exception: pass
        self.mic['values'] = inputs; self.output['values'] = outputs
        self.mic.set(oldmic if oldmic in inputs else inputs[0]); self.output.set(oldout if oldout in outputs else outputs[0])

    def change_mic(self, event=None):
        self.save()
        if self.worker and self.worker.is_alive():
            self.pause()
            def restart():
                if self.worker.is_alive(): self.root.after(100, restart)
                else: self.start()
            self.root.after(150, restart)

    def start(self):
        if self.worker and self.worker.is_alive(): return
        self.stop.clear(); self.detector = LiveDetector(self.phrases())
        self.latest_audio_time = 0.0
        self.alert_merger = AlertMerger()
        self.listening_enabled = True
        self.config_snapshot = (tuple(self.phrases()), self.mode.get())
        self.configure_audio()
        self.live_text = ''; self.challenge.preview(''); self.render()
        self.input_device = self.device_map.get(self.mic.get())
        self.model_id = self.selected_model()
        self.status.set('Loading '+MODELS[self.model_id][0]+'…'); self.startbtn.state(['disabled'])
        self.worker = threading.Thread(target=self.listen, daemon=True); self.worker.start()

    def listen(self):
        engine = VoskEngine() if self.model_id=='vosk' else NvidiaServer(); self.engine = engine
        audio = queue.Queue(maxsize=100)
        failed = threading.Event()
        com = ctypes.windll.ole32.CoInitializeEx(None, 0)
        try:
            engine.start(self.stop,model_id=self.model_id)
            if self.stop.is_set(): return
            device = sd.query_devices(self.input_device, kind='input')
            rate = int(device['default_samplerate'])
            def callback(data, frames, timing, status):
                self.latest_audio_time += frames/rate
                if status or audio.full():
                    if not failed.is_set():
                        self.events.put(('warning', 'Audio interrupted or GPU overloaded. Listening paused; close heavy apps and restart.'))
                    failed.set(); self.stop.set(); return
                audio.put_nowait(bytes(data))
            transcript = TranscriptEvents(lambda:self.config_snapshot,
                lambda kind,text:self.events.put((kind,text)),
                lambda word,end:self.report_hit(word,end,'speech'))
            self.events.put(('keyword_status', MODELS[self.model_id][0]+' • Local only'))
            with sd.RawInputStream(device=self.input_device, samplerate=rate,
                    blocksize=0, latency='low', dtype='int16', channels=1,
                    callback=callback) as stream:
                self.events.put(('capture', f'Mic buffer {stream.latency*1000:.0f} ms'))
                self.events.put(('status', '● Listening — '+('Vosk CPU' if self.model_id=='vosk' else 'NVIDIA GPU')))
                engine.stream(audio, rate, self.stop, transcript.feed,
                    lambda text:self.events.put(('warning',text)))
        except Exception as e:
            self.events.put(('error', str(e)))
        finally:
            self.stop.set(); self.listening_enabled = False
            engine.close()
            if com in (0,1): ctypes.windll.ole32.CoUninitialize()
            if self.engine is engine: self.engine = None
            self.events.put(('stopped', 'Paused'))

    def pause(self):
        self.listening_enabled = False
        self.stop.set(); self.status.set('Pausing…')

    def poll(self):
        for _ in range(100):
            try: kind, value = self.events.get_nowait()
            except queue.Empty: break
            if kind in ('text', 'partial'):
                final = kind == 'text'
                if not final and value == self.live_text: continue
                if final:
                    try: self.challenge.feed(value)
                    except Exception as e: self.last.set('Could not save challenge score: '+str(e))
                    self.backlog.extend(value.split()); self.live_text = ''
                else:
                    self.live_text = value
                    self.challenge.preview(value)
                self.render()
            elif kind == 'hit':
                word, source = value
                self.hits += 1
                self.catches.append((time.strftime('%H:%M:%S'),word))
                self.last.set('Caught: '+word+' • '+('direct keyword match' if source=='keyword' else 'live speech match'))
                self.catchlog.configure(state='normal'); self.catchlog.delete('1.0','end')
                for stamp, phrase in self.catches:
                    self.catchlog.insert('end',stamp+'  '); self.catchlog.insert('end',phrase,'catch'); self.catchlog.insert('end','   ')
                self.catchlog.see('end'); self.catchlog.configure(state='disabled')
                self.render()
            elif kind in ('playback','capture','keyword_status'):
                setattr(self, kind+'_detail', value)
                self.runtime.set(' • '.join(getattr(self,k+'_detail','') for k in ('keyword_status','capture','playback') if getattr(self,k+'_detail','')))
            elif kind == 'error':
                self.last.set('Microphone / recognition error: ' + value)
                self.status.set('Cannot listen — see the error below.')
            elif kind == 'warning': self.last.set(value)
            elif kind == 'stopped':
                self.startbtn.state(['!disabled'])
                if self.stop.is_set(): self.status.set(value)
            else: self.status.set(value)
        self.root.after(30, self.poll)

    def render(self):
        current = self.live_text.split()[-2000:]
        remaining = 2000-len(current)
        confirmed = list(self.backlog)[-remaining:] if remaining else []
        prefix = ' '.join(confirmed)
        text = ' '.join(confirmed + current)
        bottom = self.transcript.yview()[1] >= .98
        scroll = self.transcript.yview()[0]
        self.transcript.configure(state='normal'); self.transcript.delete('1.0', 'end')
        if text:
            self.transcript.insert('end', text)
            if current: self.transcript.tag_add('provisional', f'1.0+{len(prefix)+(1 if prefix else 0)}c', 'end-1c')
            for start, end in trigger_spans(text, [p for p, _ in self.detector.phrases]):
                self.transcript.tag_add('trigger', f'1.0+{start}c', f'1.0+{end}c')
            for start,end in trigger_spans(text,self.challenge.store.words):
                self.transcript.tag_add('challenge',f'1.0+{start}c',f'1.0+{end}c')
        else: self.transcript.insert('end', 'Your words will appear here as you speak.\nTrigger words light up in pink.', 'placeholder')
        if bottom: self.transcript.see('end')
        else: self.transcript.yview_moveto(scroll)
        self.transcript.configure(state='disabled')
        self.stats.set(f'{len(confirmed)+len(current):,} / 2,000 words  •  {self.hits} alerts this session')

    def clear(self):
        self.backlog.clear(); self.live_text = ''; self.render()

    def quit(self):
        self.listening_enabled = False
        self.challenge.close()
        if self.volume_save_job:
            self.root.after_cancel(self.volume_save_job); self.persist_volume()
        self.stop.set(); self.player.close()
        if self.engine: self.engine.close()
        self.root.destroy()

def selftest():
    d = Detector(['shit', 'you know', 'fuck'])
    assert d.feed('a shirt and a shithole') == []
    assert d.feed('SHIT you') == ['shit']
    assert d.feed('know fuck fuck') == ['you know', 'fuck', 'fuck']
    assert d.feed('fine') == []
    live = LiveDetector(['shit', 'you know'])
    assert live.update('shit') == ['shit']
    assert live.update('shit') == []
    assert live.update('well shit') == []  # a revised prefix must not re-alert
    assert live.update('well shirt') == []
    assert live.update('well shit') == []  # reappearing hypothesis
    assert live.update('well shit shit') == ['shit']
    assert live.update('well shit shit you', final=True) == []
    assert live.update('know') == ['you know']
    assert live.update('know', final=True) == []
    assert live.update('shit', final=True) == ['shit']
    assert live.update('', final=True) == []
    assert live.update('shirt shithole') == []
    assert trigger_spans('Shit! a shirt; YOU know.', ['shit', 'you know']) == [(0, 4), (15, 23)]
    careful = CarefulDetector(['shit','you know'])
    assert careful.update('shit', audio_time=1) == []
    assert careful.update('shirt', audio_time=1.2) == []
    assert careful.update('shirt', audio_time=1.6) == []
    assert careful.update('shit happens', audio_time=2) == []
    assert careful.update('shit happens', audio_time=2.2) == []
    assert careful.update('shit happens', audio_time=2.4) == ['shit']
    assert careful.update('shit happens', audio_time=3, final=True, confidences=[.99,.95]) == []
    assert careful.update('shit', audio_time=4, final=True, confidences=[.4]) == []
    assert careful.update('shit', audio_time=5, final=True, confidences=[.95]) == ['shit']
    assert careful.update('you', audio_time=6, final=True, confidences=[.99]) == []
    assert careful.update('know', audio_time=7, final=True, confidences=[.99]) == ['you know']
    merger = AlertMerger()
    assert merger.accept('shit', 1.0, 'speech')
    assert not merger.accept('shit', 1.3, 'keyword')
    assert merger.accept('shit', 1.5, 'speech')
    assert not merger.accept('shit', 1.8, 'keyword')
    assert merger.accept('fuck', 2.0, 'keyword')
    assert not merger.accept('fuck', 1.9, 'speech')
    assert merger.accept('fuck', 2.3, 'keyword')
    assert merger.accept('fuck', 2.5, 'keyword')
    assert not merger.accept('fuck', 2.4, 'speech')
    assert not merger.accept('fuck', 2.6, 'speech')
    from fast_audio import prepare_sound
    sound = prepare_sound('', 48000, 2, .8)
    assert sound.shape == (6720, 2) and np.abs(sound).max() <= .21
    assert np.all(prepare_sound('',16000,1,0)==0)
    from fast_audio import FastPlayer
    player = FastPlayer.__new__(FastPlayer)
    player.lock = threading.Lock(); player.samples=np.ones((20,2),dtype='float32')*.8
    player.position=0; player.pending=False; player.volume=1
    out=np.empty((5,2),dtype='float32')
    player.callback(out,5,None,None); assert np.allclose(out,.8)
    player.set_volume(.25); player.callback(out,5,None,None); assert np.allclose(out,.2)
    player.set_volume(0); player.callback(out,5,None,None); assert np.all(out==0)
    b = deque(maxlen=2000); b.extend(str(i) for i in range(2100)); assert len(b)==2000 and b[0]=='100'
    d = StreamingDetector(['shit','ass','you know'])
    assert d.update('ass',1)==[]
    assert d.update('assignment',1.3)==[]
    assert d.update('shit happens',2)==[]
    assert d.update('shit happens',2.2)==['shit']
    assert d.update('shit happens.',2.8,True)==[]
    assert d.update('shit shit',3,True)==['shit','shit']
    assert d.update('you',4,True)==[]
    assert d.update('know',5,True)==['you know']
    assert StreamingDetector(['shit'],'Transcript only').update('shit',1,True)==[]
    fast = StreamingDetector(['shit'],'Fast')
    assert fast.update('shit',1)==['shit']  # no next-word or silence wait
    assert fast.update('shit',1.08)==[]
    assert fast.update('shit.',1.5,True)==[]
    assert fast.update('shit shit',2)==['shit','shit']
    guarded=StreamingDetector(['hell','ass','um'],'Fast')
    assert guarded.update('hell',1)==[]
    assert guarded.update('hello',1.08)==[]
    assert guarded.update('hello.',1.7,True)==[]
    assert guarded.update('ass',2)==[]
    assert guarded.update('assignment',2.08)==[]
    assert guarded.update('assignment.',3,True)==[]
    assert guarded.update('um',4)==[]
    assert guarded.update('umbrella.',4.5,True)==[]
    assert guarded.update('hell',5)==[]
    assert guarded.update('hell yes',5.08)==['hell']
    assert guarded.update('hell yes.',6,True)==[]
    assert guarded.update('hell.',7,True)==['hell']
    received=[]; caught=[]
    t=TranscriptEvents(lambda:(('shit',),'Careful'),lambda k,v:received.append((k,v)),lambda w,a:caught.append(w))
    for delta,at in [('Sh',1),('it',1.08),(' happens',1.16),('',1.4)]:
        t.feed({'type':'x.delta','delta':delta,'audio_processed':at})
    t.feed({'type':'x.completed','transcript':'Shit happens.','audio_processed':2})
    assert caught==['shit'] and received[-1]==('text','Shit happens.')
    order=[]
    t=TranscriptEvents(lambda:(('shit',),'Fast'),lambda k,v:order.append('text'),lambda w,a:order.append('sound'))
    t.feed({'type':'x.delta','delta':'shit','audio_processed':1})
    assert order==['sound','text']
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'click.wav'
        wave=np.concatenate([np.zeros(3200),np.ones(320)*.5]).astype('float32')
        sf.write(path,wave,16000)
        before=path.read_bytes()
        trimmed=prepare_sound(str(path),16000,1)
        full=prepare_sound(str(path),16000,1,trim=False)
        assert len(trimmed)==352 and len(full)==3520
        assert path.read_bytes()==before
    return True

if __name__ == '__main__':
    if '--engine-test' in sys.argv:
        test_model=sys.argv[sys.argv.index('--test-model')+1] if '--test-model' in sys.argv else 'nemotron-en'
        engine=VoskEngine() if test_model=='vosk' else NvidiaServer(); stop=threading.Event(); received=[]
        try:
            engine.start(stop,model_id=test_model); process=getattr(engine,'process',None)
            audio=queue.Queue()
            for _ in range(20): audio.put(bytes(2560))
            timer=threading.Timer(2,stop.set); timer.start()
            engine.stream(audio,16000,stop,received.append,lambda x:None)
            timer.join()
            assert not any(e.get('delta') or e.get('transcript') for e in received)
        finally:
            stop.set(); engine.close()
        assert not engine.running and (process is None or process.poll() is not None)
        Path(sys.argv[sys.argv.index('--engine-test')+1]).write_text('PASS: '+test_model+' startup, silence recognition, shutdown.')
        sys.exit()
    if '--self-test' in sys.argv:
        selftest(); Path(sys.argv[sys.argv.index('--self-test')+1]).write_text('PASS: live detection, revision deduplication, cross-engine deduplication, repeated occurrences, phrase boundaries, highlighting, 2000-word cap, cached audio generation, NVIDIA streaming event assembly and word-boundary verification.'); sys.exit()
    if '--smoke-test' not in sys.argv and '--live-check' not in sys.argv:
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.restype = wintypes.HANDLE
        instance_mutex = kernel.CreateMutexW(None, False, 'Local\\WordWatchDesktop')
        if kernel.GetLastError() == 183:
            user32 = ctypes.windll.user32
            existing = user32.FindWindowW(None, 'WordWatch — live speech coach')
            if existing:
                user32.ShowWindow(existing, 9); user32.SetForegroundWindow(existing)
            sys.exit(0)
    root = tk.Tk()
    if '--smoke-test' in sys.argv or '--live-check' in sys.argv: root.withdraw()
    app = App(root)
    test_errors = []
    if '--live-check' in sys.argv:
        root.withdraw()
        def check_live():
            okay=bool(app.engine and app.engine.running and app.player.ready and 'Listening' in app.status.get())
            Path(sys.argv[sys.argv.index('--live-check')+1]).write_text('PASS: NVIDIA GPU, microphone and alert output running together' if okay else 'FAIL: listening or output unavailable')
            if not okay: test_errors.append('Live startup failed')
            app.quit()
        root.after(6000,check_live)
    if '--smoke-test' in sys.argv:
        def callback_error(kind, value, trace):
            test_errors.append(str(value)); app.quit()
        root.report_callback_exception = callback_error
        def smoke():
            app.play = lambda: None
            app.player.trigger = lambda: None
            app.listening_enabled = True
            app.report_hit('shit', 1, 'speech')
            app.events.put(('partial', 'hello shit'))
            app.poll()
            assert app.hits == 1
            assert app.transcript.tag_ranges('trigger')
            assert 'hello shit' in app.transcript.get('1.0', 'end')
            app.events.put(('text', 'hello shit'))
            app.report_hit('shit', 1.3, 'keyword')
            app.poll()
            assert app.hits == 1
            assert app.transcript.tag_ranges('trigger')
            assert not app.transcript.tag_ranges('provisional')
            assert app.catchlog.tag_ranges('catch')
            app.model_choice.set(MODELS['nemotron-3.5'][0]); app.change_mic()
            assert app.cfg['model']=='nemotron-3.5'
            app.model_choice.set(MODELS['nemotron-en'][0]); app.change_mic()
            assert app.cfg['model']=='nemotron-en'
            app.challenge.entry.delete(0,'end'); app.challenge.entry.insert(0,'punctuation, nuance'); app.challenge.apply()
            before=app.challenge.store.counts().get('punctuation',0)
            app.events.put(('partial','Punctuation punctuation')); app.poll()
            assert app.challenge.store.counts().get('punctuation',0)==before
            assert app.challenge.live_counts()['punctuation']==before+2
            assert app.transcript.tag_ranges('challenge')
            app.events.put(('partial','Punctuation')); app.poll()
            assert app.challenge.live_counts()['punctuation']==before+1
            app.events.put(('partial','Punctuation punctuation')); app.poll()
            app.events.put(('text','Punctuation punctuation.')); app.poll()
            assert app.challenge.store.counts()['punctuation']==before+2
            app.challenge.refresh()
            assert app.challenge.live_counts()['punctuation']==before+2
            assert app.challenge.board.get_children()
            assert len(app.challenge.board.rows)==2
            assert app.challenge.board.rows==sorted(app.challenge.board.rows,key=lambda row:(-row[1],row[0]))
            for page in range(len(app.tabs.pages)):
                app.tabs.select(page); root.update_idletasks()
            app.root.after(300, app.quit)
        root.after(800, smoke)
    root.mainloop()
    if test_errors: sys.exit(1)
