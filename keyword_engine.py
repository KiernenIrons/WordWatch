"""Streaming keyword spotting, independent from the transcript decoder."""
import threading
from pathlib import Path
from collections import deque


class AlertMerger:
    """Pair cross-engine reports in audio time; never collapse same-engine repeats."""
    def __init__(self, window=.85):
        self.window = window
        self.pending = deque(maxlen=512)
        self.lock = threading.Lock()

    def accept(self, word, audio_time, source):
        with self.lock:
            self.pending = deque((p for p in self.pending if abs(audio_time-p[1]) <= 4), maxlen=512)
            candidates = [(abs(audio_time-t), i) for i,(w,t,s) in enumerate(self.pending)
                          if w == word and s != source and abs(audio_time-t) <= self.window]
            if candidates:
                _, i = min(candidates)
                del self.pending[i]
                return False
            self.pending.append((word,audio_time,source))
            return True


class KeywordEngine:
    def __init__(self, model_dir, data_dir, phrases, threshold=.5):
        import sentencepiece
        import sherpa_onnx
        folder = Path(model_dir)
        tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(folder/'bpe.model'))
        self.labels = {}
        lines = []
        self.unsupported = []
        for i, phrase in enumerate(phrases):
            encoded = tokenizer.encode(phrase.upper(), out_type=str)
            if not encoded or '<unk>' in encoded:
                self.unsupported.append(phrase)
                continue
            label = f'word{i}'
            self.labels[label] = phrase
            lines.append(' '.join(encoded)+f' @{label}')
        if not lines: raise ValueError('No supported English keywords are enabled.')
        keyword_file = Path(data_dir)/'keyword-cache.txt'
        keyword_file.write_text('\n'.join(lines), encoding='utf-8')
        self.decoder = sherpa_onnx.KeywordSpotter(
            tokens=str(folder/'tokens.txt'), encoder=str(folder/'encoder.onnx'),
            decoder=str(folder/'decoder.onnx'), joiner=str(folder/'joiner.onnx'),
            keywords_file=str(keyword_file), num_threads=1,
            keywords_threshold=threshold, keywords_score=1.0, num_trailing_blanks=1)
        self.stream = self.decoder.create_stream()

    def feed(self, rate, samples):
        self.stream.accept_waveform(rate, samples)
        found = []
        while self.decoder.is_ready(self.stream):
            self.decoder.decode_stream(self.stream)
            label = self.decoder.get_result(self.stream)
            if label:
                if label in self.labels: found.append(self.labels[label])
                self.decoder.reset_stream(self.stream)
        return found

    def reset(self):
        self.stream = self.decoder.create_stream()
