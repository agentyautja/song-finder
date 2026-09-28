"""Minimal audio playback that can start from any point in a song."""
import threading

import miniaudio
import numpy as np

PLAY_SR = 44100


class Player:
    def __init__(self):
        self._lock = threading.Lock()
        self._device = None
        self._pos = 0
        self._total = 0
        self.key = None  # identifies what is currently playing

    def play(self, data: bytes, start_seconds: float, key) -> None:
        """Decodes and starts playback (blocking while decoding, call from a thread)."""
        snd = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16,
                               nchannels=2, sample_rate=PLAY_SR)
        samples = np.frombuffer(snd.samples, dtype=np.int16)
        total = len(samples) // 2
        start = min(int(start_seconds * PLAY_SR), max(0, total - 1))

        def stream():
            required = yield b""
            while self._pos < total:
                end = min(self._pos + required, total)
                chunk = samples[self._pos * 2:end * 2].tobytes()
                self._pos = end
                required = yield chunk

        with self._lock:
            self._close()
            self._pos, self._total = start, total
            gen = stream()
            next(gen)
            self._device = miniaudio.PlaybackDevice(
                output_format=miniaudio.SampleFormat.SIGNED16, nchannels=2,
                sample_rate=PLAY_SR, app_name="Song Finder")
            self._device.start(gen)
            self.key = key

    def stop(self) -> None:
        with self._lock:
            self._close()

    def _close(self) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None
        self.key = None

    @property
    def finished(self) -> bool:
        return self._device is not None and self._pos >= self._total

    @property
    def position(self) -> float:
        return self._pos / PLAY_SR
