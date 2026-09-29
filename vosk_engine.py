"""Alternative CPU transcription with the same live-event interface."""
import json
import queue
from nvidia_engine import runtime_folder

class VoskEngine:
    def __init__(self): self.model=None; self.running=False

    def start(self,stop,model_id='vosk'):
        import vosk
        vosk.SetLogLevel(-1)
        folder=runtime_folder()/'models/vosk'
        if not folder.exists(): raise RuntimeError('The Vosk model is missing from Documents / WordWatch / nvidia-runtime / models / vosk.')
        self.model=vosk.Model(str(folder)); self.running=not stop.is_set()

    def stream(self,audio,rate,stop,on_event,warning):
        import vosk
        recognizer=vosk.KaldiRecognizer(self.model,rate)
        seconds=0; last=''
        while not stop.is_set():
            try: data=audio.get(timeout=.05)
            except queue.Empty: continue
            seconds+=len(data)/(rate*2)
            final=recognizer.AcceptWaveform(data)
            if final:
                value=json.loads(recognizer.Result()).get('text','')
                on_event({'type':'speech.completed','transcript':value,'audio_processed':seconds}); last=''
            else:
                value=json.loads(recognizer.PartialResult()).get('partial','')
                # Empty repeat events allow stability timing to advance in Careful mode.
                on_event({'type':'speech.delta','hypothesis':value,'audio_processed':seconds})
                last=value
        on_event({'type':'speech.completed','transcript':json.loads(recognizer.FinalResult()).get('text',''),'audio_processed':seconds})

    def close(self): self.running=False; self.model=None
