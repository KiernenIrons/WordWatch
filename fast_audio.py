"""Local audio playback kept ready so alerts don't reopen the device."""
import queue
import ctypes
import threading
import time
from pathlib import Path
import numpy as np
import sounddevice as sd
import soundfile as sf


def prepare_sound(path, rate, channels, volume=1.0, trim=True):
    if path:
        data, source_rate = sf.read(path, dtype='float32', always_2d=True)
        if len(data) == 0: raise ValueError('The alert audio is empty.')
        if len(data)/source_rate > 30: raise ValueError('Choose a sound no longer than 30 seconds.')
        if trim:
            amplitude = np.max(np.abs(data), axis=1)
            threshold = max(.0001, float(amplitude.max())*.01)
            active = np.flatnonzero(amplitude > threshold)
            if active.size:
                # Keep a tiny pre-roll to avoid cutting into the attack; never edit the file.
                data = data[max(0,int(active[0])-round(source_rate*.002)):]
        if data.shape[1] != channels:
            data = np.repeat(data.mean(axis=1, keepdims=True), channels, axis=1)
        if source_rate != rate:
            positions = np.arange(round(len(data)*rate/source_rate))*source_rate/rate
            data = np.column_stack([np.interp(positions, np.arange(len(data)), data[:, c]) for c in range(channels)]).astype('float32')
    else:
        t = np.arange(int(rate*.14))/rate
        tone = .25*np.sin(2*np.pi*960*t)*np.minimum(1,t/.005)*np.minimum(1,(.14-t)/.01)
        data = np.repeat(tone[:,None], channels, axis=1).astype('float32')
    return np.ascontiguousarray(np.clip(data * volume, -1, 1), dtype='float32')


class FastPlayer:
    def __init__(self, events):
        self.events = events
        self.commands = queue.Queue()
        self.lock = threading.Lock()
        self.samples = np.zeros((0, 1), dtype='float32')
        self.position = 0
        self.pending = False
        self.ready = False
        self.closed = False
        self.last_config = None
        self.volume = 1.0
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def configure(self, device, sound, volume, trim=True):
        self.set_volume(volume)
        try:
            stat = Path(sound).stat() if sound else None
            fingerprint = (stat.st_mtime_ns, stat.st_size) if stat else None
        except OSError: fingerprint = None
        cfg = (device, sound, fingerprint, trim)
        if cfg != self.last_config or not self.ready:
            self.last_config = cfg
            self.commands.put(cfg)

    def set_volume(self, volume):
        with self.lock: self.volume = max(0.0, min(1.0, float(volume)))

    def trigger(self):
        with self.lock: self.pending = True

    def callback(self, out, frames, timing, status):
        out.fill(0)
        with self.lock:
            if self.pending:
                self.position = 0
                self.pending = False
            count = min(frames, len(self.samples)-self.position)
            if count > 0:
                out[:count] = self.samples[self.position:self.position+count] * self.volume
                self.position += count

    def run(self):
        # WASAPI COM interfaces are thread-local, including stream start/stop.
        com = ctypes.windll.ole32.CoInitializeEx(None, 0)
        try: self._run()
        finally:
            if com in (0,1): ctypes.windll.ole32.CoUninitialize()

    def _run(self):
        stream = None
        while True:
            cfg = self.commands.get()
            if cfg is None: break
            while not self.commands.empty():
                cfg = self.commands.get()
                if cfg is None: break
            if cfg is None: break
            device, sound, fingerprint, trim = cfg
            self.ready = False
            if stream:
                stream.close(); stream = None
            try:
                info = sd.query_devices(device, 'output')
                rate = int(info['default_samplerate']); channels = min(2, int(info['max_output_channels']))
                data = prepare_sound(sound, rate, channels, trim=trim)
                with self.lock:
                    self.samples = data
                    self.position = len(data)
                stream = sd.OutputStream(device=device, samplerate=rate, channels=channels,
                                         dtype='float32', blocksize=0, latency='low', callback=self.callback)
                stream.start(); self.ready = True
                self.events.put(('playback', f'Alert output ready • device buffer {stream.latency*1000:.0f} ms'))
            except Exception as e:
                with self.lock: self.pending = False
                self.events.put(('warning', 'Alert output unavailable: '+str(e)))
        if stream: stream.close()
        self.ready = False

    def close(self):
        if not self.closed:
            self.closed = True
            self.commands.put(None)
