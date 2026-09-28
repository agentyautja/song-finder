"""Audio analysis and matching engine for Song Finder.

Every track is reduced to three kinds of features:

* fingerprint - landmark hashes built from pairs of spectral peaks (the same idea
  Shazam uses). Survives MP3 re-encoding, noise and phone recordings, and finds
  a short clip inside a full song. Only matches the *same recording*.
* chroma      - the 12 musical pitch classes over time (melody / chords). Compared
  with a sliding window over several tempos and all 12 keys, so it also finds
  other versions of the same song (live, remix, cover, sped up...).
* timbre      - MFCC statistics describing the overall "sound" of the track.
"""
from __future__ import annotations

import math
import os
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

import miniaudio
import numpy as np

SR = 11025
MAX_SECONDS = 15 * 60
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".ogg")

# Fingerprint parameters
FP_NFFT = 1024
FP_HOP = 256
FP_FMIN, FP_FMAX = 8, 372  # bins -> ~86 Hz .. ~4 kHz
FP_PEAK_T, FP_PEAK_F = 15, 21  # local-maximum neighbourhood (frames, bins)
FP_BUCKET = 43  # ~1 second of frames
FP_PEAKS_PER_BUCKET = 20
FP_FAN = 10
FP_MAX_DT = 63
FP_MAX_DF = 64
FP_MAX_HASH_REPEATS = 20

# Chroma / timbre parameters
CH_NFFT = 4096
CH_HOP = 1024
CH_FRAME_SEC = 2 * CH_HOP / SR  # chroma is downsampled by 2 -> ~0.186 s per frame
CH_WIN = 48  # ~9 s analysis windows
CH_WIN_HOP = 24
TEMPOS = (0.84, 0.92, 1.0, 1.09, 1.19)
TEMPO_PENALTY = 0.97
KEY_PENALTY = 0.9


# --------------------------------------------------------------------------- #
# Library access
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Track:
    name: str  # display name (path inside the zip / folder)
    source: str  # zip file or folder
    member: str  # zip member name or absolute file path
    in_zip: bool

    def read_bytes(self) -> bytes:
        if self.in_zip:
            with zipfile.ZipFile(self.source) as zf:
                return zf.read(self.member)
        with open(self.member, "rb") as f:
            return f.read()

    @property
    def filename(self) -> str:
        return self.name.replace("\\", "/").rsplit("/", 1)[-1]


def _fix_zip_name(info: zipfile.ZipInfo) -> str:
    """Zips made on Windows often store UTF-8 names without the UTF-8 flag."""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def _is_audio(name: str) -> bool:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return (name.lower().endswith(AUDIO_EXTS) and not base.startswith("._")
            and "__MACOSX/" not in name)


def list_tracks(path: str) -> list[Track]:
    """All audio files inside a .zip file or a folder (recursively)."""
    tracks = []
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for fn in files:
                full = os.path.join(root, fn)
                if _is_audio(full):
                    tracks.append(Track(os.path.relpath(full, path), path, full, False))
    else:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if not info.is_dir() and _is_audio(info.filename):
                    tracks.append(Track(_fix_zip_name(info), path, info.filename, True))
    tracks.sort(key=lambda t: t.name.lower())
    return tracks


# --------------------------------------------------------------------------- #
# DSP helpers
# --------------------------------------------------------------------------- #
def decode_audio(data: bytes) -> np.ndarray:
    snd = miniaudio.decode(data, output_format=miniaudio.SampleFormat.FLOAT32,
                           nchannels=1, sample_rate=SR)
    y = np.frombuffer(snd.samples, dtype=np.float32)
    return y[: MAX_SECONDS * SR]


def _stft_mag(y: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    if len(y) < n_fft:
        y = np.pad(y, (0, n_fft - len(y)))
    window = np.hanning(n_fft).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop]
    out = np.empty((len(frames), n_fft // 2 + 1), np.float32)
    for i in range(0, len(frames), 1024):
        out[i:i + 1024] = np.abs(np.fft.rfft(frames[i:i + 1024] * window, axis=1))
    return out


def _max_filter_1d(a: np.ndarray, size: int, axis: int) -> np.ndarray:
    r = size // 2
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r, r)
    padded = np.pad(a, pad, mode="constant", constant_values=-np.inf)
    out = a.copy()
    n = a.shape[axis]
    for s in range(size):
        sl = [slice(None)] * a.ndim
        sl[axis] = slice(s, s + n)
        np.maximum(out, padded[tuple(sl)], out=out)
    return out


def _chroma_filter() -> np.ndarray:
    freqs = np.arange(CH_NFFT // 2 + 1) * SR / CH_NFFT
    fb = np.zeros((12, len(freqs)), np.float32)
    idx = np.nonzero((freqs >= 55) & (freqs <= 3520))[0]
    midi = 69 + 12 * np.log2(freqs[idx] / 440.0)
    nearest = np.round(midi)
    fb[nearest.astype(int) % 12, idx] = np.cos((midi - nearest) * np.pi) ** 2
    return fb


def _mel_filter(n_mels: int = 40, fmin: float = 60, fmax: float = 5000) -> np.ndarray:
    def hz2mel(f):
        return 2595 * np.log10(1 + f / 700)

    def mel2hz(m):
        return 700 * (10 ** (m / 2595) - 1)

    hz = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
    freqs = np.arange(CH_NFFT // 2 + 1) * SR / CH_NFFT
    fb = np.zeros((n_mels, len(freqs)), np.float32)
    for i in range(n_mels):
        lo, mid, hi = hz[i], hz[i + 1], hz[i + 2]
        fb[i] = np.maximum(0, np.minimum((freqs - lo) / (mid - lo), (hi - freqs) / (hi - mid)))
    return fb


def _dct_matrix(n_in: int = 40, n_out: int = 13) -> np.ndarray:
    k = np.arange(1, n_out)[:, None]  # skip c0 (loudness)
    n = np.arange(n_in)[None, :]
    return np.cos(np.pi * k * (n + 0.5) / n_in).astype(np.float32)


CHROMA_FB = _chroma_filter()
MEL_FB = _mel_filter()
DCT = _dct_matrix()


# --------------------------------------------------------------------------- #
# Feature extraction
# --------------------------------------------------------------------------- #
@dataclass
class Features:
    duration: float
    fp_hashes: np.ndarray
    fp_times: np.ndarray
    chroma: np.ndarray  # (frames, 12), centred + L2-normalised, silent frames = 0
    timbre: np.ndarray  # (24,)


def _fingerprint(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mag = _stft_mag(y, FP_NFFT, FP_HOP)[:, FP_FMIN:FP_FMAX]
    spec = 20 * np.log10(mag + 1e-6)
    local_max = _max_filter_1d(_max_filter_1d(spec, FP_PEAK_T, 0), FP_PEAK_F, 1)
    floor = np.percentile(spec, 50)
    t, f = np.nonzero((spec == local_max) & (spec > floor + 6))
    if len(t) < 2:
        return np.empty(0, np.int64), np.empty(0, np.int32)

    # Keep only the strongest peaks in each ~1 s bucket so density is even.
    vals = spec[t, f]
    bucket = t // FP_BUCKET
    order = np.lexsort((-vals, bucket))
    t, f, bucket = t[order], f[order], bucket[order]
    starts = np.r_[0, np.nonzero(np.diff(bucket))[0] + 1]
    rank = np.arange(len(bucket)) - np.repeat(starts, np.diff(np.r_[starts, len(bucket)]))
    keep = rank < FP_PEAKS_PER_BUCKET
    t, f = t[keep], f[keep]
    order = np.lexsort((f, t))
    t, f = t[order].astype(np.int64), f[order].astype(np.int64)

    hashes, times = [], []
    for o in range(1, FP_FAN + 1):
        if o >= len(t):
            break
        i = np.arange(len(t) - o)
        j = i + o
        dt = t[j] - t[i]
        ok = (dt >= 1) & (dt <= FP_MAX_DT) & (np.abs(f[j] - f[i]) <= FP_MAX_DF)
        i, j, dt = i[ok], j[ok], dt[ok]
        hashes.append((f[i] << 15) | (f[j] << 6) | dt)
        times.append(t[i])
    if not hashes:
        return np.empty(0, np.int64), np.empty(0, np.int32)
    return np.concatenate(hashes), np.concatenate(times).astype(np.int32)


def _chroma_and_timbre(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    power = _stft_mag(y, CH_NFFT, CH_HOP) ** 2

    chroma = power @ CHROMA_FB.T
    energy = chroma.sum(1)
    n = len(chroma) // 2 * 2
    if n == 0:
        return np.zeros((0, 12), np.float32), np.zeros(24, np.float32)
    chroma = np.sqrt(chroma[:n])
    chroma = (chroma[0::2] + chroma[1::2]) / 2
    energy = energy[0:n:2] + energy[1:n:2]
    chroma -= chroma.mean(1, keepdims=True)
    norm = np.linalg.norm(chroma, axis=1, keepdims=True)
    chroma = np.where(norm > 1e-9, chroma / np.maximum(norm, 1e-9), 0).astype(np.float32)
    silent = energy < 1e-3 * (np.percentile(energy, 95) + 1e-12)
    chroma[silent] = 0

    mel = power @ MEL_FB.T
    loud = mel.sum(1) > 1e-3 * (np.percentile(mel.sum(1), 95) + 1e-12)
    if loud.sum() < 2:
        loud[:] = True
    mfcc = np.log10(mel[loud] + 1e-10 * mel.max() + 1e-12) @ DCT.T
    timbre = np.concatenate([mfcc.mean(0), mfcc.std(0)]).astype(np.float32)
    return chroma, timbre


def extract_features(data: bytes) -> Features:
    y = decode_audio(data)
    if len(y) < SR // 2:
        raise ValueError("audio is too short")
    peak = float(np.max(np.abs(y)))
    if peak < 1e-4:
        raise ValueError("audio is silent")
    y = y / peak
    hashes, times = _fingerprint(y)
    chroma, timbre = _chroma_and_timbre(y)
    return Features(len(y) / SR, hashes, times, chroma, timbre)


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #
@dataclass
class ChromaWindows:
    tempo: float
    windows: np.ndarray  # (n_windows, frames, 12)
    starts: np.ndarray  # window start in *query* frames (unstretched)
    active: np.ndarray  # non-silent frames per window


@dataclass
class Query:
    features: Features
    hashes: np.ndarray  # sorted
    times: np.ndarray
    windows: list[ChromaWindows] = field(default_factory=list)


def _resample_rows(x: np.ndarray, length: int) -> np.ndarray:
    if length == len(x):
        return x
    pos = np.linspace(0, len(x) - 1, length)
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, len(x) - 1)
    w = (pos - lo)[:, None]
    out = x[lo] * (1 - w) + x[hi] * w
    norm = np.linalg.norm(out, axis=1, keepdims=True)
    return np.where(norm > 1e-9, out / np.maximum(norm, 1e-9), 0).astype(np.float32)


def prepare_query(features: Features) -> Query:
    order = np.argsort(features.fp_hashes, kind="stable")
    q = Query(features, features.fp_hashes[order], features.fp_times[order])

    chroma = features.chroma
    total = len(chroma)
    if total < 8:
        return q
    win = min(CH_WIN, total)
    starts = list(range(0, total - win + 1, CH_WIN_HOP))
    if starts[-1] != total - win:
        starts.append(total - win)
    segments = [chroma[s:s + win] for s in starts]
    active = np.array([(np.abs(seg).sum(1) > 0).sum() for seg in segments])
    keep = active >= win // 2
    if not keep.any():
        return q
    segments = [seg for seg, k in zip(segments, keep) if k]
    starts_arr = np.array(starts)[keep]
    active = active[keep]
    for tempo in TEMPOS:
        length = max(4, round(win * tempo))
        stretched = np.stack([_resample_rows(seg, length) for seg in segments])
        q.windows.append(ChromaWindows(tempo, stretched, starts_arr, active * tempo))
    return q


def _fingerprint_match(q: Query, c: Features) -> tuple[float, float]:
    """Returns (confidence 0..1, time in candidate where the query starts)."""
    if len(q.hashes) == 0 or len(c.fp_hashes) == 0:
        return 0.0, 0.0
    left = np.searchsorted(q.hashes, c.fp_hashes, "left")
    right = np.searchsorted(q.hashes, c.fp_hashes, "right")
    counts = right - left
    counts[counts > FP_MAX_HASH_REPEATS] = 0
    total = int(counts.sum())
    if total == 0:
        return 0.0, 0.0
    cand_idx = np.repeat(np.arange(len(c.fp_hashes)), counts)
    first = np.cumsum(counts) - counts
    q_idx = np.repeat(left, counts) + (np.arange(total) - np.repeat(first, counts))
    delta = c.fp_times[cand_idx].astype(np.int64) - q.times[q_idx]
    dmin = int(delta.min())
    hist = np.bincount(delta - dmin)
    smooth = hist.copy()
    smooth[1:] += hist[:-1]
    smooth[:-1] += hist[1:]
    best = int(smooth.argmax())
    matches = float(smooth[best])

    # How many aligned matches would pure chance produce?
    span = q.times.max(initial=0) + c.fp_times.max(initial=0) + 1
    expected = 3.0 * total / span
    threshold = expected + 4 * math.sqrt(expected) + 4
    conf = float(np.clip((matches - threshold) / 20.0, 0.0, 1.0))
    start = max(0.0, (best + dmin) * FP_HOP / SR)
    return conf, start


def _chroma_match(q: Query, c: Features) -> tuple[float, float]:
    """Returns (similarity -1..1, time in candidate where the query starts)."""
    if not q.windows or len(c.chroma) < 8:
        return 0.0, 0.0
    key_weights = np.full(12, KEY_PENALTY, np.float32)
    key_weights[0] = 1.0

    best_scores = None
    best_start = None
    for cw in q.windows:
        n_win, win_len, _ = cw.windows.shape
        cand = c.chroma
        if len(cand) < win_len:
            cand = np.pad(cand, ((0, win_len - len(cand)), (0, 0)))
        n_fft = 1 << (len(cand) + win_len - 1).bit_length()
        fc = np.fft.fft(np.fft.rfft(cand, n=n_fft, axis=0), axis=1)
        fw = np.fft.fft(np.fft.rfft(cw.windows, n=n_fft, axis=1), axis=2)
        corr = np.fft.irfft(np.fft.ifft(np.conj(fw) * fc[None], axis=2), n=n_fft, axis=1)
        corr = corr[:, : len(cand) - win_len + 1, :] * key_weights
        flat = corr.reshape(n_win, -1)
        arg = flat.argmax(1)
        scores = flat[np.arange(n_win), arg] / np.maximum(cw.active, 1)
        if cw.tempo != 1.0:
            scores *= TEMPO_PENALTY
        lags = arg // 12
        starts = (lags - cw.starts * cw.tempo) * CH_FRAME_SEC
        if best_scores is None:
            best_scores, best_start = scores, starts
        else:
            better = scores > best_scores
            best_scores = np.where(better, scores, best_scores)
            best_start = np.where(better, starts, best_start)

    top = max(1, math.ceil(len(best_scores) / 2))
    order = np.argsort(best_scores)[::-1][:top]
    similarity = float(best_scores[order].mean())
    return similarity, max(0.0, float(best_start[order[0]]))


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    track: Track
    duration: float
    fingerprint: float  # 0..1 same-recording confidence
    melody: float  # 0..1 melody / chord similarity
    timbre: float  # 0..1 overall sound similarity
    score: float  # 0..1 combined
    start: float  # seconds into the track where the match begins
    label: str
    _timbre_vec: np.ndarray = field(default=None, repr=False)
    _melody_raw: float = field(default=0.0, repr=False)


@dataclass
class SearchOutcome:
    results: list[Result]
    failed: list[tuple[Track, str]]
    query_duration: float
    cancelled: bool = False


def _label(r: Result) -> str:
    if r.fingerprint >= 0.5:
        return "Exact match"
    if r.melody >= 0.6:
        return "Likely same song"
    if r.score >= 0.45:
        return "Similar"
    if r.score >= 0.25:
        return "Somewhat similar"
    return "Not similar"


def _melody_confidences(raw: np.ndarray) -> np.ndarray:
    """Maps raw chroma similarity to 0..1, using both the absolute value and how
    far the song stands out from the rest of the collection (most songs share
    common chords, so only a clear outlier means "same song")."""
    absolute = np.clip((raw - 0.45) / 0.35, 0.0, 1.0)
    if len(raw) < 8:
        return absolute
    median = np.median(raw)
    spread = max(1.4826 * np.median(np.abs(raw - median)), 0.03)
    relative = np.clip(((raw - median) / spread - 1.5) / 3.5, 0.0, 1.0)
    return 0.5 * absolute + 0.5 * relative


def _finalize(results: list[Result], query_timbre: np.ndarray) -> None:
    melody = _melody_confidences(np.array([r._melody_raw for r in results]))
    for r, m in zip(results, melody):
        r.melody = float(m)

    vecs = np.array([r._timbre_vec for r in results] + [query_timbre])
    mean, std = vecs.mean(0), vecs.std(0)
    # With few songs the spread is unreliable; keep a sensible floor.
    std = np.maximum(std, 0.5 * np.median(std) + 0.05)
    zq = (query_timbre - mean) / std
    for r in results:
        dist = np.linalg.norm((r._timbre_vec - mean) / std - zq) / math.sqrt(len(zq))
        r.timbre = float(math.exp(-dist ** 2 / 1.3))
        # Other versions of a song often use different instruments, so the
        # overall sound only nudges the ranking; the melody decides.
        r.score = max(r.fingerprint, 0.85 * r.melody + 0.1 * r.timbre)
        r.label = _label(r)
    results.sort(key=lambda r: (r.score, r.fingerprint, r.melody), reverse=True)


ProgressFn = Callable[[int, int, str], None]


def search(query_path: str, library_path: str, progress: ProgressFn | None = None,
           cancel: threading.Event | None = None, workers: int | None = None) -> SearchOutcome:
    with open(query_path, "rb") as f:
        query = prepare_query(extract_features(f.read()))

    tracks = list_tracks(library_path)
    if not tracks:
        raise ValueError("No audio files were found in the collection.")
    workers = workers or max(1, min(8, (os.cpu_count() or 2) - 1))

    def analyse(track: Track) -> Result:
        feats = extract_features(track.read_bytes())
        fp_conf, fp_start = _fingerprint_match(query, feats)
        sim, ch_start = _chroma_match(query, feats)
        start = fp_start if fp_conf >= 0.5 else ch_start
        if query.features.duration > 0.8 * feats.duration:
            start = 0.0
        return Result(track, feats.duration, fp_conf, 0.0, 0.0, 0.0,
                      min(start, max(0.0, feats.duration - 1)), "", feats.timbre, sim)

    results, failed = [], []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyse, t): t for t in tracks}
        for fut in as_completed(futures):
            track = futures[fut]
            if cancel is not None and cancel.is_set():
                for other in futures:
                    other.cancel()
                return SearchOutcome([], [], query.features.duration, cancelled=True)
            try:
                results.append(fut.result())
            except Exception as exc:  # corrupt / unsupported file
                failed.append((track, str(exc) or type(exc).__name__))
            done += 1
            if progress:
                progress(done, len(tracks), track.filename)

    if results:
        _finalize(results, query.features.timbre)
    return SearchOutcome(results, failed, query.features.duration)
