"""Local NVIDIA speech server lifecycle and incremental transcript handling."""
import ctypes
import json
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from websockets.sync.client import connect

MODELS = {
    'nemotron-en': ('NVIDIA Nemotron — English', 'english.gguf'),
    'nemotron-3.5': ('NVIDIA Nemotron 3.5 — English / multilingual model', 'nemotron-3.5.gguf'),
    'vosk': ('Vosk — lightweight English / CPU', 'vosk'),
}

# Frequent standalone triggers that also begin ordinary longer words.
# These cannot safely be distinguished until a boundary or final result arrives.
PREFIX_GUARDS = {'hell','ass','dick','cum','crap','damn','um','uh','like'}


class ChildJob:
    """Windows closes the GPU child even if WordWatch is forcibly terminated."""
    def __init__(self, process):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),
                ('flags',w.DWORD),('min_working_set',ctypes.c_size_t),('max_working_set',ctypes.c_size_t),
                ('active_processes',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(n,ctypes.c_uint64) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Limits(ctypes.Structure):
            _fields_ = [('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),
                ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype=w.HANDLE
        self.kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.handle=self.kernel.CreateJobObjectW(None,None)
        limits=Limits(); limits.basic.flags=0x2000
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            self.close(); raise ctypes.WinError(ctypes.get_last_error())
        if not self.kernel.AssignProcessToJobObject(self.handle,w.HANDLE(int(process._handle))):
            self.close(); raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle); self.handle=None


def runtime_folder():
    if os.environ.get('WORDWATCH_RUNTIME'):
        return Path(os.environ['WORDWATCH_RUNTIME'])
    if not getattr(sys, 'frozen', False):
        return Path(__file__).parent / 'nvidia-runtime'
    installed = Path(sys.executable).parent / 'nvidia-runtime'
    if installed.is_dir():
        return installed
    buf = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) != 0:
        raise RuntimeError('Cannot find your Documents folder.')
    return Path(buf.value) / 'WordWatch' / 'nvidia-runtime'


class NvidiaServer:
    def __init__(self):
        self.process = None
        self.job = None
        self.errors = deque(maxlen=15)
        self.key = secrets.token_urlsafe(32)
        self.port = None
        self.lock = threading.Lock()

    @property
    def running(self): return self.process is not None and self.process.poll() is None

    def start(self, stop, right_context=0, model_id='nemotron-en'):
        folder = runtime_folder()
        exe = folder / 'bin' / 'nemo-speech.exe'
        if model_id not in MODELS: raise ValueError('Unknown transcription model.')
        model = folder / 'models' / MODELS[model_id][1]
        if not exe.exists() or not model.exists():
            raise RuntimeError('Files for '+MODELS[model_id][0]+' are missing. Keep nvidia-runtime in Documents / WordWatch.')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); self.port = s.getsockname()[1]
        env = os.environ.copy()
        env['NEMO_SPEECH_HTTP_API_KEY'] = self.key
        args = [str(exe), 'serve', '--asr-model', str(model), '--device', 'cuda:0',
                '--host', '127.0.0.1', '--port', str(self.port), '--no-ui',
                '--asr.batching.enabled=false', '--asr.endpointing.enable=true',
                '--asr.endpointing.stop_history_eou_ms', '600',
                '--asr.streaming.rnnt_right_context', str(right_context)]
        with self.lock:
            if stop.is_set(): return
            self.process = subprocess.Popen(args, env=env, cwd=str(folder),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.job = ChildJob(self.process)
        process = self.process
        def drain():
            for line in iter(process.stderr.readline, b''):
                self.errors.append(line.decode('utf-8', 'replace').strip())
            process.stderr.close()
        threading.Thread(target=drain, daemon=True).start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 90
        while not stop.is_set():
            if process.poll() is not None:
                raise RuntimeError('NVIDIA could not start: ' + ' '.join(self.errors)[-1000:])
            try:
                with opener.open(f'http://127.0.0.1:{self.port}/ready', timeout=.5) as reply:
                    if reply.status == 200: return
            except OSError: pass
            if time.monotonic() > deadline: raise RuntimeError('NVIDIA took too long to load. Try closing GPU-heavy apps.')
            stop.wait(.1)

    def close(self):
        with self.lock:
            p, self.process = self.process, None
            job, self.job = self.job, None
        if job: job.close()
        if p and p.poll() is None:
            p.terminate()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=2)

    def stream(self, audio, rate, stop, on_event, warning, rotate_after=600, force_after=1200):
        """Rotate at a natural pause to bound each server stream's memory and byte count."""
        while not stop.is_set():
            rotate = threading.Event(); ended = threading.Event()
            failures = queue.Queue(); sent_seconds = [0.0]
            with connect(f'ws://127.0.0.1:{self.port}/v1/audio/transcriptions/realtime',
                         additional_headers={'Authorization':'Bearer '+self.key}, proxy=None,
                         open_timeout=10, close_timeout=1, max_size=4*1024*1024) as ws:
                created = json.loads(ws.recv(timeout=10))
                if created.get('type') != 'session.created': raise RuntimeError(str(created))
                ws.send(json.dumps({'type':'session.update','session':{
                    'sample_rate':rate, 'language':'en-US', 'verbatim':True,
                    'profanity_filter':False, 'word_timestamps':False, 'endpointing_ms':600}}))
                updated = json.loads(ws.recv(timeout=10))
                if updated.get('type') != 'session.updated': raise RuntimeError(str(updated))
                def send():
                    try:
                        while not ended.is_set():
                            if stop.is_set() or rotate.is_set() or sent_seconds[0] >= force_after:
                                ws.send(json.dumps({'type':'input_audio_buffer.commit'})); return
                            try: data = audio.get(timeout=.05)
                            except queue.Empty: continue
                            ws.send(data)
                            sent_seconds[0] += len(data)/(rate*2)
                    except Exception as e: failures.put(e)
                sender = threading.Thread(target=send, daemon=True); sender.start()
                stopping_at = None
                try:
                    while True:
                        if not failures.empty(): raise failures.get()
                        if stop.is_set():
                            stopping_at = stopping_at or time.monotonic()
                            if time.monotonic()-stopping_at > 2: break
                        try: event = json.loads(ws.recv(timeout=.1))
                        except TimeoutError: continue
                        kind = event.get('type','')
                        if kind == 'error': raise RuntimeError(str(event.get('error', event)))
                        if kind.endswith('.delta') or kind.endswith('.completed'):
                            on_event(event)
                            if kind.endswith('.completed') and sent_seconds[0] >= rotate_after: rotate.set()
                        if kind == 'input_audio_buffer.committed': break
                finally:
                    ended.set(); sender.join(timeout=1)


class StreamingDetector:
    """Delay incomplete token prefixes; deduplicate each occurrence until final."""
    def __init__(self, phrases, mode='Careful'):
        self.phrases = [(p, self.tokens(p)) for p in phrases if self.tokens(p)]
        self.mode = mode
        self.tail = []
        self.pending = {}
        self.emitted = set()

    @staticmethod
    def tokens(text):
        return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", text.lower().replace('’', "'"))

    def update(self, text, audio_time=0, final=False, suppress=False):
        current = self.tokens(text); words = self.tail + current; boundary = len(self.tail)
        visible = set(); found = []
        for label, phrase in self.phrases:
            count = 0
            for i in range(max(0,boundary-len(phrase)+1), len(words)-len(phrase)+1):
                if words[i:i+len(phrase)] != phrase: continue
                count += 1; key = (label,count); visible.add(key)
                first = self.pending.setdefault(key,audio_time)
                # A following token or punctuation establishes a whole-word boundary.
                complete = i+len(phrase)<len(words) or bool(re.search(r'[\s.,!?;:]$',text))
                # Fast reacts to the first exact live match, including the trailing
                # token. Careful retains boundary checks for ambiguous prefixes.
                fast_match = self.mode=='Fast' and (complete or phrase[-1] not in PREFIX_GUARDS)
                reliable = final or fast_match or (complete and audio_time-first>=.16)
                if suppress or (reliable and self.mode!='Transcript only'):
                    if key not in self.emitted and not suppress: found.append(label)
                    self.emitted.add(key)
        self.pending = {k:v for k,v in self.pending.items() if k in visible}
        if final:
            n = max((len(p) for _,p in self.phrases),default=1)-1
            self.tail = words[-n:] if n else []
            self.pending.clear(); self.emitted.clear()
        return found


class TranscriptEvents:
    def __init__(self, get_config, emit_text, emit_hit):
        self.get_config = get_config; self.emit_text = emit_text; self.emit_hit = emit_hit
        self.config = get_config(); self.detector = StreamingDetector(*self.config)
        self.text = ''

    def feed(self, event):
        final = event['type'].endswith('.completed')
        now = event.get('audio_processed',0)
        config = self.get_config()
        if config != self.config:
            self.config = config; self.detector = StreamingDetector(*config)
            self.detector.update(self.text, now, suppress=True)
        if final: self.text = event.get('transcript','')
        elif 'hypothesis' in event: self.text = event['hypothesis']
        else: self.text += event.get('delta','')
        for word in self.detector.update(self.text, now, final): self.emit_hit(word, now)
        if final or event.get('delta') or 'hypothesis' in event: self.emit_text('text' if final else 'partial', self.text)
        if final: self.text = ''
