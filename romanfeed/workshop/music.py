"""Bouncy, original holiday workshop music, written note by note in numpy.

Each piece is our own melody over a seeded progression in a major key:
celesta/glockenspiel lead, plucked bass on the beat, soft marimba off-beat
chords, a warm pad and sleigh bells. No existing carol or song is quoted:
melodies are generated from chord tones with stepwise passing notes, so a
seed gives a tune nobody else owns and Content ID has nothing to match.

Pieces loop seamlessly: the length is a whole number of bars, the last bar
resolves to the tonic, and the reverb tail is wrapped onto the start.
"""
from __future__ import annotations

import hashlib
import os
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 44100
KEYS = {"C": 60, "D": 62, "Eb": 63, "F": 65, "G": 67, "Bb": 58, "A": 57}
MAJOR = [0, 2, 4, 5, 7, 9, 11]
# chord roots as scale degrees (0 = I); triads built from the major scale
PHRASES = [
    [0, 5, 3, 4, 0, 5, 1, 4],
    [0, 3, 0, 4, 5, 3, 1, 4],
    [3, 4, 2, 5, 1, 4, 0, 0],
    [0, 2, 3, 0, 3, 4, 0, 4],
    [5, 3, 0, 4, 5, 3, 4, 0],
]
RHYTHMS = [   # (beat, length in beats) per 4/4 bar
    [(0, 1), (1, 1), (2, 1), (3, 1)],
    [(0, 1.5), (1.5, 0.5), (2, 1), (3, 1)],
    [(0, 0.5), (0.5, 0.5), (1, 1), (2, 0.5), (2.5, 0.5), (3, 1)],
    [(0, 1), (1, 0.5), (1.5, 0.5), (2, 2)],
    [(0, 0.5), (0.5, 0.5), (1, 0.5), (1.5, 0.5), (2, 2)],
    [(0, 2), (2, 1), (3, 1)],
]


def _rng(seed: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))


def hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


@dataclass
class Piece:
    seed: str
    key: str
    bpm: int
    bars: int

    @property
    def seconds(self) -> float:
        return self.bars * 4 * 60 / self.bpm


# --- instruments ---------------------------------------------------------------------------------------

def _env(n: int, attack_s: float, decay_s: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.minimum(1.0, t / max(attack_s, 1e-4))
    return a * np.exp(-t / decay_s)


def celesta(f: float, dur: float) -> np.ndarray:
    n = int(SR * (dur + 1.6))
    t = np.arange(n) / SR
    tone = (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t / 0.25)
            + 0.18 * np.sin(2 * np.pi * 4.07 * f * t) * np.exp(-t / 0.08))
    return tone * _env(n, 0.003, 0.65)


def glock(f: float, dur: float) -> np.ndarray:
    n = int(SR * (dur + 1.8))
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * 2.76 * f * t) * np.exp(-t / 0.3) \
        + 0.2 * np.sin(2 * np.pi * 5.4 * f * t) * np.exp(-t / 0.1)
    return tone * _env(n, 0.002, 0.9)


def pluck_bass(f: float, dur: float) -> np.ndarray:
    n = int(SR * (dur + 0.3))
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * f * t) + 0.45 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t / 0.12) \
        + 0.15 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t / 0.06)
    return tone * _env(n, 0.004, 0.32)


def marimba(f: float, dur: float) -> np.ndarray:
    n = int(SR * 0.6)
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 4 * f * t) * np.exp(-t / 0.03)
    return tone * _env(n, 0.002, 0.16)


def pad(f: float, dur: float) -> np.ndarray:
    n = int(SR * (dur + 0.8))
    t = np.arange(n) / SR
    tone = sum(a * np.sin(2 * np.pi * f * h * t * (1 + d)) for h, a, d in
               ((1, 1.0, 0), (1, 0.6, 0.003), (2, 0.25, 0), (3, 0.08, 0)))
    att = np.minimum(1.0, t / 0.4)
    rel = np.clip((dur + 0.8 - t) / 0.8, 0, 1)
    return tone * att * rel


def bells(rng: np.random.Generator, accent: float) -> np.ndarray:
    """One shake of sleigh bells: a cluster of tiny high jingles."""
    n = int(SR * 0.22)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for _ in range(5):
        f = rng.uniform(4200, 7800)
        start = int(rng.uniform(0, 0.03) * SR)
        tt = t[: n - start]
        out[start:] += np.sin(2 * np.pi * f * tt + rng.uniform(0, 6.28)) * np.exp(-tt / rng.uniform(0.03, 0.08))
    noise = rng.standard_normal(n)
    noise = np.diff(noise, prepend=0) * np.exp(-t / 0.02) * 0.08
    return (out * 0.25 + noise) * accent


# --- the score -----------------------------------------------------------------------------------------

def _scale_note(tonic: int, deg: int) -> int:
    o, d = divmod(deg, 7)
    return tonic + 12 * o + MAJOR[d]


def _triad(root_deg: int) -> list[int]:
    return [root_deg, root_deg + 2, root_deg + 4]


def _melody(rng: np.random.Generator, chords: list[int], cadence: bool) -> list[tuple[float, float, int]]:
    """(beat, beats, scale degree) for one 8-bar phrase over `chords`."""
    notes = []
    prev = 7 + int(rng.integers(0, 5))           # degree, around the octave above the tonic
    rhythms = [RHYTHMS[int(rng.integers(0, len(RHYTHMS)))] for _ in range(4)]
    for bar, root in enumerate(chords):
        rhythm = rhythms[bar % 4] if bar < 6 or not cadence else RHYTHMS[5 if bar == 7 else 0]
        tones = [d + 7 * k for d in _triad(root) for k in (0, 1, 2)]
        for i, (b, ln) in enumerate(rhythm):
            strong = b in (0, 2)
            if cadence and bar == 7:
                deg = min((d for d in (7, 14, 9, 11)), key=lambda d: abs(d - prev)) if i == 0 else 7
            elif strong:
                cands = sorted(tones, key=lambda d: abs(d - prev) + rng.uniform(0, 1.5))
                deg = cands[0]
            else:
                deg = prev + int(rng.choice([-1, 1, 1, 2, -2]))
            deg = int(np.clip(deg, 4, 15))
            notes.append((bar * 4 + b, ln, deg))
            prev = deg
    return notes


def plan(seed: str) -> Piece:
    rng = _rng(seed)
    key = list(KEYS)[int(rng.integers(0, len(KEYS)))]
    bpm = int(rng.integers(104, 128))
    return Piece(seed, key, bpm, 0)


def render_pcm(seed: str, target_s: float = 150) -> tuple[np.ndarray, Piece]:
    rng = _rng(seed)
    piece = plan(seed)
    tonic = KEYS[piece.key]
    beat = 60 / piece.bpm
    pa = PHRASES[int(rng.integers(0, len(PHRASES)))]
    pb = PHRASES[int(rng.integers(0, len(PHRASES)))]
    mel_a = _melody(rng, pa, cadence=True)
    mel_b = _melody(rng, pb, cadence=True)
    # form: A A B A, then again with the glockenspiel lead and a busier bass
    form = [("A", pa, mel_a), ("A", pa, mel_a), ("B", pb, mel_b), ("A", pa, mel_a)]
    rounds = max(1, round(target_s / (len(form) * 8 * 4 * beat)))
    sections = [(r, s) for r in range(rounds) for s in form]
    bars = len(sections) * 8
    piece.bars = bars
    total = int(bars * 4 * beat * SR)
    out = np.zeros((2, total + SR * 3))

    def add(sig: np.ndarray, at_s: float, gain: float, pan: float):
        i = max(0, int(at_s * SR))
        j = min(i + len(sig), out.shape[1])
        out[0, i:j] += sig[: j - i] * gain * np.cos((pan + 1) * np.pi / 4)
        out[1, i:j] += sig[: j - i] * gain * np.sin((pan + 1) * np.pi / 4)

    for si, (rnd, (name, chords, mel)) in enumerate(sections):
        t0 = si * 8 * 4 * beat
        lead = celesta if (rnd + (name == "B")) % 2 == 0 else glock
        for b, ln, deg in mel:
            add(lead(hz(_scale_note(tonic, deg)), ln * beat), t0 + b * beat, 0.30, -0.1)
            if name == "B" or rnd % 2:          # a quiet harmony a third below
                add(celesta(hz(_scale_note(tonic, deg - 2)), ln * beat), t0 + b * beat, 0.10, 0.3)
        for bar, root in enumerate(chords):
            tb = t0 + bar * 4 * beat
            r = _scale_note(tonic - 24, root)
            fifth = _scale_note(tonic - 24, root + 4)
            pattern = [(0, r), (2, fifth)] if rnd % 2 == 0 else [(0, r), (1.5, r), (2, fifth), (3, r + 12)]
            for b, m in pattern:
                add(pluck_bass(hz(m), beat), tb + b * beat, 0.42, 0.0)
            chord = [_scale_note(tonic - 12, d) for d in _triad(root)]
            for b in (1, 3) if name == "A" else (0.5, 1.5, 2.5, 3.5):
                for m in chord:
                    add(marimba(hz(m), 0.5 * beat), tb + b * beat, 0.07, 0.35)
            for m in chord:
                add(pad(hz(m), 4 * beat), tb, 0.05, -0.3)
            for e in range(8):
                acc = 1.0 if e % 2 == 0 else 0.55
                if rnd == 0 and name == "A" and si == 0 and bar < 2:
                    acc *= 0.5
                add(bells(rng, acc), tb + e * beat / 2 + rng.uniform(-0.006, 0.006), 0.07, 0.5)
    # small room reverb (exponential noise tail), then wrap the tail onto the start
    ir_n = int(SR * 1.2)
    irng = np.random.default_rng(7)
    ir = irng.standard_normal((2, ir_n)) * np.exp(-np.arange(ir_n) / SR / 0.35)
    ir /= np.sqrt((ir ** 2).sum(axis=1, keepdims=True))
    n = out.shape[1] + ir_n
    size = 1 << int(np.ceil(np.log2(n)))
    wet = np.fft.irfft(np.fft.rfft(out, size) * np.fft.rfft(ir, size), size)[:, :n]
    mix = np.zeros((2, n))
    mix[:, : out.shape[1]] = out
    mix = mix * 0.85 + wet * 0.22
    loop = mix[:, :total].copy()
    tail = mix[:, total:]
    k = min(tail.shape[1], total)
    loop[:, :k] += tail[:, :k]
    loop = np.tanh(loop * 1.4) / 1.4
    return loop.astype(np.float32), piece


def write_wav(pcm: np.ndarray, path: Path) -> float:
    peak = float(np.max(np.abs(pcm)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.round(np.clip(pcm, -1, 1).T * 32767).astype("<i2").tobytes())
    return peak


def compose(dest: Path, seeds: list[str], *, piece_s: float = 150, target_lufs: float = -16.0) -> list[Piece]:
    """Several pieces back to back in one AAC file (the stream loops the file)."""
    from romanfeed.audio.composer import _encode_normalised

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    parts, pieces = [], []
    for s in seeds:
        pcm, p = render_pcm(s, piece_s)
        parts.append(pcm)
        pieces.append(p)
    wav = dest.with_name(f".{dest.stem}.{os.getpid()}.{threading.get_ident()}.wav")
    try:
        peak = write_wav(np.concatenate(parts, axis=1), wav)
        _encode_normalised(wav, dest, peak, target_lufs=target_lufs)
    finally:
        wav.unlink(missing_ok=True)
    return pieces
