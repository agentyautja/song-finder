"""Generates a synthetic test library (zip of MP3s) plus queries with known answers.

    python tools/make_test_data.py

Output in test_data/:
  library.zip        40 synthetic songs (song_00.mp3 ... song_39.mp3)
  q_clip_noisy.mp3   12 s of song_05 with heavy noise, low bitrate   -> song_05 (exact)
  q_full.mp3         song_20 re-encoded                                -> song_20 (exact)
  q_cover.mp3        song_12 re-arranged: other instruments, key +2,
                     10% faster                                        -> song_12 (other version)
  q_unknown.mp3      a song that is not in the library                 -> nothing
"""
import io
import os
import zipfile

import lameenc
import numpy as np

SR = 44100
OUT = os.path.join(os.path.dirname(__file__), "..", "test_data")
MAJOR = [0, 2, 4, 5, 7, 9, 11]


def make_spec(rng):
    key = int(rng.integers(0, 12))
    degrees = [0] + list(rng.choice([1, 2, 3, 4, 5], size=3))
    melody = []  # (beat offset within 8 beats, scale step, length in beats)
    beat = 0.0
    while beat < 8:
        length = float(rng.choice([0.5, 0.5, 1.0, 1.5]))
        if rng.random() > 0.15:
            melody.append((beat, int(rng.integers(0, 10)), length))
        beat += length
    return {"key": key, "bpm": float(rng.uniform(85, 145)), "degrees": degrees,
            "melody": melody, "bars": int(rng.integers(18, 26))}


def make_style(rng):
    return {"lead": rng.uniform(0.2, 1.0, 8) ** np.arange(1, 9) * rng.uniform(0.3, 1, 8),
            "pad": rng.uniform(0.1, 0.8, 6) ** np.arange(1, 7),
            "bass": rng.uniform(0.2, 0.7, 4) ** np.arange(1, 5),
            "drums": float(rng.uniform(0.3, 1.0)),
            "decay": float(rng.uniform(1.5, 6.0))}


def tone(freq, dur, harmonics, decay):
    t = np.arange(int(dur * SR)) / SR
    sig = sum(a * np.sin(2 * np.pi * freq * (k + 1) * t) for k, a in enumerate(harmonics)
              if freq * (k + 1) < SR / 2)
    env = np.exp(-t * decay) * np.minimum(1, t * 200) * np.minimum(1, (dur - t) * 50)
    return sig * env


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def scale_note(key, step, octave):
    return 12 * octave + key + MAJOR[step % 7] + 12 * (step // 7)


def render(spec, style, transpose=0, tempo=1.0, seed=0):
    rng = np.random.default_rng(seed)
    beat = 60 / (spec["bpm"] * tempo)
    total_beats = spec["bars"] * 4
    out = np.zeros(int((total_beats + 2) * beat * SR))
    key = spec["key"] + transpose

    def add(sig, start):
        i = int(start * SR)
        n = min(len(sig), len(out) - i)
        out[i:i + n] += sig[:n]

    for bar in range(spec["bars"]):
        degree = spec["degrees"][bar % 4]
        chord = [scale_note(key, degree + s, 4) for s in (0, 2, 4)]
        start = bar * 4 * beat
        for m in chord:
            add(0.25 * tone(midi_hz(m), 4 * beat, style["pad"], 0.8), start)
        for b in range(4):
            add(0.5 * tone(midi_hz(scale_note(key, degree, 2)), beat * 0.9, style["bass"], 3), start + b * beat)
    for rep in range(spec["bars"] // 2):
        for off, step, length in spec["melody"]:
            m = scale_note(key, step, 5)
            add(0.35 * tone(midi_hz(m), length * beat, style["lead"], style["decay"]),
                (rep * 8 + off) * beat)
    d = style["drums"]
    for b in range(total_beats):
        t = b * beat
        kt = np.arange(int(0.15 * SR)) / SR
        if b % 2 == 0:
            add(d * 0.9 * np.sin(2 * np.pi * (50 + 80 * np.exp(-kt * 30)) * kt) * np.exp(-kt * 20), t)
        else:
            add(d * 0.4 * rng.standard_normal(len(kt)) * np.exp(-kt * 25), t)
        for h in (0, 0.5):
            ht = np.arange(int(0.04 * SR)) / SR
            add(d * 0.12 * rng.standard_normal(len(ht)) * np.exp(-ht * 90), t + h * beat)
    return out / (np.max(np.abs(out)) + 1e-9) * 0.9


def to_mp3(signal, bitrate=128, sr=SR):
    enc = lameenc.Encoder()
    enc.set_bit_rate(bitrate)
    enc.set_in_sample_rate(sr)
    enc.set_channels(1)
    enc.set_quality(2)
    pcm = (np.clip(signal, -1, 1) * 32767).astype(np.int16).tobytes()
    return bytes(enc.encode(pcm) + enc.flush())


def main():
    os.makedirs(OUT, exist_ok=True)
    rng = np.random.default_rng(1234)
    songs = []
    with zipfile.ZipFile(os.path.join(OUT, "library.zip"), "w", zipfile.ZIP_STORED) as zf:
        for i in range(40):
            spec, style = make_spec(rng), make_style(rng)
            audio = render(spec, style, seed=i)
            songs.append((spec, style, audio))
            zf.writestr(f"My Music/song_{i:02d}.mp3", to_mp3(audio))
            print("song", i)

    def save(name, signal, bitrate=128):
        with open(os.path.join(OUT, name), "wb") as f:
            f.write(to_mp3(signal, bitrate))

    clip = songs[5][2][40 * SR:52 * SR].copy()
    clip += 0.25 * np.random.default_rng(9).standard_normal(len(clip))  # heavy noise
    save("q_clip_noisy.mp3", clip / np.max(np.abs(clip)), bitrate=48)
    save("q_full.mp3", songs[20][2], bitrate=96)
    spec12 = songs[12][0]
    save("q_cover.mp3", render(spec12, make_style(np.random.default_rng(77)), transpose=2, tempo=1.1, seed=99))
    save("q_unknown.mp3", render(make_spec(rng), make_style(rng), seed=500))
    print("done ->", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
