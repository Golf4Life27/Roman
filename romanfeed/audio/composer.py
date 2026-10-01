"""Procedural ambient composer: original music for every video.

Library tracks (even paid-plan AI generations) are shared material: one of
ours drew a Content ID claim, and a handful of tracks looped under every
video reads as "mass production" in monetisation review. This module writes
the soundtrack itself, so each piece is unique to its seed and owned
outright (licence "owned"): there is no upstream rightsholder, and no
reference file anywhere for Content ID to match against.

Musical recipe (slow sleep ambient; nothing percussive, nothing sudden):
  - seeded key and mode (minor, Dorian, Aeolian, Lydian)
  - a slow diatonic progression, one chord every 14-24 s, voice-led, with
    long overlapping attacks/releases so chords dissolve into each other
  - warm pads: band-limited wavetables (a few harmonics, steep rolloff),
    two slightly detuned voices per note spread across the stereo field
  - a soft tonic drone with a quieter sub octave, slowly beating L/R
  - an occasional celesta/bell note from the mode's pentatonic subset,
    long decay, never closer together than a few seconds
  - a quiet band-passed noise "air" layer that swells over minutes
  - a 5-7 s synthetic stereo reverb (FFT overlap-add convolution)
  - tanh soft limiter, then a two-pass EBU R128 normalisation to TARGET_LUFS
    when encoding: measure with ffmpeg's ebur128, encode with that gain

Performance: everything is float32 and rendered in ~18 s chunks (the
reverb's FFT block), streamed straight to a WAV, so memory stays flat no
matter how long the piece. Oscillators are phase-accumulator wavetable
lookups and envelopes run at a 32-sample control rate, which keeps the cost
to a few seconds of CPU per minute of audio on a CI runner.

Determinism: the whole score comes from one seeded RNG and chunking depends
only on the seed, so the same seed renders byte-identical PCM.
"""
from __future__ import annotations

import hashlib
import itertools
import logging
import os
import re
import subprocess
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from romanfeed.audio.library import TARGET_LUFS, Track, _slug
from romanfeed.render import ffmpeg

log = logging.getLogger(__name__)

_TABLE_N = 4096   # wavetable length; harmonics stop at 8, so linear interp is clean
_CTRL = 32        # control-rate decimation for envelopes (0.7 ms at 44.1 kHz)
_A4 = 440.0

_NOTE_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

# Scale (semitones), pentatonic subset for the bells (no semitone clashes, so
# a bell over any chord of the mode stays consonant), and the chord-root
# transition vocabulary as scale-degree indices. Diminished degrees never
# appear as roots. "minor" and "aeolian" share a scale but move differently:
# minor leans on the relative-major colours (VI, III, VII), aeolian on the
# plainer modal iv and v.
_MODES: dict[str, dict] = {
    "minor": {
        "scale": [0, 2, 3, 5, 7, 8, 10], "penta": [0, 3, 5, 7, 10], "label": "minor",
        "moves": {0: [5, 2, 3, 6], 5: [2, 6, 0, 3], 2: [6, 5, 3], 6: [0, 2, 5], 3: [0, 5, 6]},
    },
    "aeolian": {
        "scale": [0, 2, 3, 5, 7, 8, 10], "penta": [0, 3, 5, 7, 10], "label": "Aeolian",
        "moves": {0: [3, 4, 6, 5], 3: [0, 4, 6], 4: [0, 5, 3], 6: [0, 3], 5: [6, 3, 0]},
    },
    "dorian": {
        "scale": [0, 2, 3, 5, 7, 9, 10], "penta": [0, 3, 5, 7, 9], "label": "Dorian",
        "moves": {0: [3, 6, 2, 1], 3: [0, 6, 1], 6: [0, 3, 2], 2: [3, 6, 0], 1: [0, 6, 3]},
    },
    "lydian": {
        "scale": [0, 2, 4, 6, 7, 9, 11], "penta": [0, 2, 4, 7, 9], "label": "Lydian",
        "moves": {0: [1, 4, 5, 2], 1: [0, 4, 5], 4: [0, 1], 5: [1, 0, 4], 2: [5, 1, 0]},
    },
}

_ADJECTIVES = [
    "Quiet", "Distant", "Slow", "Soft", "Silent", "Drifting", "Pale", "Deep", "Still",
    "Faint", "Hushed", "Gentle", "Low", "Velvet", "Dim", "Weightless", "Patient", "Long",
]
_NOUNS = [
    "Orbit", "Nebula", "Horizon", "Tide", "Drift", "Light", "Halo", "Ember", "Meridian",
    "Aurora", "Perihelion", "Expanse", "Dust", "Signal", "Aphelion", "Eclipse", "Parallax",
    "Starfield", "Lagrange", "Corona", "Redshift", "Equinox",
]


def _midi_hz(m: float) -> float:
    return _A4 * 2.0 ** ((m - 69.0) / 12.0)


def _smooth(x: np.ndarray) -> np.ndarray:
    """Smoothstep of x clipped to [0, 1]: a ramp with no corner at either end,
    so attacks and releases never click."""
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _wavetable(amps: list[float]) -> np.ndarray:
    """One cycle of sum(a_h * sin(h x)), peak-normalised, with a wrap sample
    appended so linear interpolation can read index N."""
    x = np.arange(_TABLE_N, dtype=np.float64) * (2 * np.pi / _TABLE_N)
    y = sum(a * np.sin((h + 1) * x) for h, a in enumerate(amps))
    y /= np.max(np.abs(y))
    return np.append(y, y[0]).astype(np.float32)


def _pan(p: float) -> tuple[float, float]:
    """Constant-power pan, p in [-1, 1]."""
    a = (p + 1.0) * np.pi / 4.0
    return float(np.cos(a)), float(np.sin(a))


@dataclass
class _Voice:
    """One oscillator with an envelope, active over [start, end) samples.
    Start/end sit on the control-rate grid so envelope blocks line up."""
    start: int
    end: int
    inc: float          # cycles per sample
    phase0: float
    table: np.ndarray
    gl: float
    gr: float
    env: Callable[[np.ndarray], np.ndarray]   # seconds (float64) -> gain
    bus: int            # 0 pad/drone, 1 bell


@dataclass
class _Plan:
    seconds: float
    sample_rate: int
    n_samples: int
    title: str
    key: str
    mode: str
    voices: list[_Voice]
    ir: np.ndarray           # (2, L) reverb impulse response
    air_kernel: np.ndarray   # (L,) band-pass FIR for the air layer
    air_swell: Callable[[np.ndarray], np.ndarray]
    seed_int: int
    fade_in: float
    fade_out: float


def _seed_int(seed: str) -> int:
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")


def _snap(t: float, sr: int) -> int:
    return int(t * sr) // _CTRL * _CTRL


def _pad_env(on: float, att: float, off: float, rel: float, lfo_hz: float, lfo_ph: float, depth: float):
    def env(t: np.ndarray) -> np.ndarray:
        e = _smooth((t - on) / att) * _smooth((off + rel - t) / rel)
        return e * (1.0 - depth + depth * np.sin(2 * np.pi * lfo_hz * t + lfo_ph))
    return env


def _bell_env(on: float, decay: float):
    def env(t: np.ndarray) -> np.ndarray:
        dt = t - on
        # 40 ms rounded attack: the note blooms rather than strikes.
        return _smooth(dt / 0.04) * np.exp(-np.maximum(dt, 0.0) / decay)
    return env


def _drone_env(lfo_hz: float, lfo_ph: float):
    def env(t: np.ndarray) -> np.ndarray:
        return 0.75 + 0.25 * np.sin(2 * np.pi * lfo_hz * t + lfo_ph)
    return env


def _progression(rng: np.random.Generator, moves: dict[int, list[int]], n: int) -> list[int]:
    """Walk the mode's chord graph, preferring the first-listed moves, coming
    home to the tonic every few chords and always ending there."""
    out, cur, since_home = [0], 0, 0
    for i in range(1, n):
        if i == n - 1 or (since_home >= 4 and rng.random() < 0.5 and 0 in moves[cur]):
            nxt = 0
        else:
            opts = [d for d in moves[cur] if d != 0] or moves[cur]
            w = np.array([1.0 / (k + 1) for k in range(len(opts))])
            nxt = int(opts[rng.choice(len(opts), p=w / w.sum())])
        since_home = 0 if nxt == 0 else since_home + 1
        out.append(nxt)
        cur = nxt
    return out


def _voice_lead(prev: list[int], pcs: list[int], lo: int, hi: int) -> list[int]:
    """Assign chord pitch classes to the previous voices with the least total
    movement (each in its nearest octave), so the pad moves by small steps."""
    def nearest(p: int, pc: int) -> int:
        return min((m for m in range(lo, hi + 1) if m % 12 == pc), key=lambda m: (abs(m - p), m))

    best = min(
        (sum(abs(nearest(p, pc) - p) for p, pc in zip(prev, perm)), perm)
        for perm in itertools.permutations(pcs)
    )[1]
    return sorted(nearest(p, pc) for p, pc in zip(prev, best))


def _plan(seconds: float, seed: str, sr: int) -> _Plan:
    seed_int = _seed_int(seed)
    rng = np.random.default_rng(seed_int)
    n_samples = int(round(seconds * sr))

    mode_name = str(rng.choice(list(_MODES)))
    mode = _MODES[mode_name]
    root_pc = int(rng.integers(12))
    key = _NOTE_NAMES[root_pc]
    title = f"{rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)} in {key} {mode['label']}"
    scale = mode["scale"]

    def pc_of(degree: int) -> int:
        return (root_pc + scale[degree % 7]) % 12

    # Timbres: steep harmonic rolloff, varied per piece. The second pad table
    # leans on odd harmonics for a hollower, more glassy voice.
    roll = rng.uniform(1.8, 2.6)
    warm = _wavetable([1.0 / (h ** roll) for h in range(1, 9)])
    hollow = _wavetable([(1.0 if h % 2 else 0.35) / (h ** (roll + 0.3)) for h in range(1, 8)])
    sine = _wavetable([1.0])
    drone_tab = _wavetable([1.0, 0.18, 0.05])

    voices: list[_Voice] = []
    chord_pcs: list[list[int]] = []

    def add(t0: float, t1: float, freq: float, table, pan: float, env, bus: int = 0, amp: float = 1.0):
        s0, s1 = max(_snap(t0, sr), 0), min(_snap(t1, sr) + _CTRL, n_samples)
        if s1 <= s0 or freq * 2 >= sr:
            return
        gl, gr = _pan(pan)
        voices.append(_Voice(s0, s1, freq / sr, float(rng.random()), table, gl * amp, gr * amp, env, bus))

    # --- chords ---------------------------------------------------------
    starts, t = [], 0.0
    while t < seconds:
        starts.append(t)
        t += float(rng.uniform(14.0, 24.0))
    roots = _progression(rng, mode["moves"], len(starts))
    upper = [57, 62, 66, 69]          # previous upper voicing, seeds the voice leading
    bass_prev = 48
    for i, (t0, deg) in enumerate(zip(starts, roots)):
        t1 = starts[i + 1] if i + 1 < len(starts) else seconds
        att, rel = rng.uniform(5.0, 8.0), rng.uniform(7.0, 10.0)
        on = t0 - 3.0 if i else -0.6 * att   # overlap the previous chord's release
        colour = 6 if rng.random() < 0.5 else 8          # add the 7th or the 9th
        pcs = [pc_of(deg + d) for d in (0, 2, 4, colour)]
        chord_pcs.append(pcs)
        upper = _voice_lead(upper, pcs, 55, 76)
        bass = min((m for m in range(43, 57) if m % 12 == pcs[0]), key=lambda m: abs(m - bass_prev))
        bass_prev = bass
        for j, m in enumerate([bass] + upper):
            f = _midi_hz(m)
            amp = 0.075 if j == 0 else 0.055 * (0.8 if m > 70 else 1.0)
            lfo = rng.uniform(0.03, 0.09)
            env = _pad_env(on, att, t1, rel, lfo, rng.uniform(0, 2 * np.pi), rng.uniform(0.1, 0.25))
            spread = rng.uniform(0.3, 0.8) * (0.4 if j == 0 else 1.0)
            cents = rng.uniform(3.0, 8.0)
            tab = warm if (j == 0 or rng.random() < 0.6) else hollow
            for sgn in (-1.0, 1.0):
                add(on, t1 + rel, f * 2 ** (sgn * cents / 1200), tab, sgn * spread, env, 0, amp)

    # --- drone: tonic in octave 2, sub octave below, beating slowly L/R ---
    d = _midi_hz(36 + root_pc)
    beat = rng.uniform(0.05, 0.12)   # Hz between the L and R copies
    for pan, f in ((-0.5, d), (0.5, d + beat)):
        add(0.0, seconds, f, drone_tab, pan, _drone_env(rng.uniform(0.01, 0.03), rng.uniform(0, 6.3)), 0, 0.09)
    add(0.0, seconds, d / 2, sine, 0.0, _drone_env(rng.uniform(0.01, 0.02), rng.uniform(0, 6.3)), 0, 0.06)

    # --- bells: sparse pentatonic random walk ----------------------------
    penta = [root_pc + 72 + iv for iv in mode["penta"]] + [root_pc + 84 + iv for iv in mode["penta"][:3]]
    penta = [m - 12 if m > 90 else m for m in penta]
    idx = int(rng.integers(len(penta)))
    t = rng.uniform(6.0, 14.0)
    while t < seconds - 10.0:
        # Skip pentatonic notes a semitone from the sounding chord: a minor
        # ninth against the pad is the one interval that reads as a mistake.
        pcs = chord_pcs[max(0, int(np.searchsorted(starts, t, side="right")) - 1)]
        ok = [k for k, m in enumerate(penta) if all((m - pc) % 12 not in (1, 11) for pc in pcs)] or list(range(len(penta)))
        want = int(np.clip(idx + rng.choice([-2, -1, -1, 1, 1, 2]), 0, len(penta) - 1))
        idx = min(ok, key=lambda k: (abs(k - want), k))
        f0 = _midi_hz(penta[idx])
        decay = rng.uniform(2.0, 3.5)          # e-folding time of the fundamental
        pan = rng.uniform(-0.6, 0.6)
        vel = rng.uniform(0.5, 1.0) * 0.07
        for ratio, a, dk in ((1.0, 1.0, 1.0), (2.0, 0.3, 0.5), (2.76, 0.12, 0.3), (4.07, 0.05, 0.2)):
            dur = min(decay * dk * 7.0, 14.0)  # ~-60 dB
            add(t, t + dur, f0 * ratio, sine, pan, _bell_env(t, decay * dk), 1, vel * a)
        gap = rng.uniform(5.0, 15.0)
        if rng.random() < 0.3:
            gap += rng.uniform(10.0, 25.0)     # rests: silence is part of it
        t += gap

    # --- reverb impulse response: decorrelated stereo noise, exponential
    # decay, darker tail (high frequencies die first like a real hall) ----
    rt60 = float(rng.uniform(5.0, 7.0))
    L = int(rt60 * sr)
    tt = np.arange(L, dtype=np.float64) / sr
    freqs = np.fft.rfftfreq(L, 1.0 / sr)
    ir = np.empty((2, L), dtype=np.float32)
    for ch in range(2):
        spec = np.fft.rfft(rng.standard_normal(L))
        bright = np.fft.irfft(spec / (1 + (freqs / 7000.0) ** 2), n=L)
        dark = np.fft.irfft(spec / (1 + (freqs / 1800.0) ** 2), n=L)
        h = 0.5 * bright * np.exp(-6.91 * tt / (rt60 * 0.35)) + dark * np.exp(-6.91 * tt / rt60)
        pre = int(0.02 * sr)
        h = np.concatenate([np.zeros(pre), h[: L - pre]])
        h[pre: pre + int(0.03 * sr)] *= np.linspace(0, 1, int(0.03 * sr))
        ir[ch] = (h / np.sqrt(np.sum(h * h))).astype(np.float32)

    # --- air: band-pass FIR (windowed-sinc difference, ~250 Hz - 2.5 kHz) ---
    taps = 1023
    n = np.arange(taps) - taps // 2
    def lp(fc: float) -> np.ndarray:
        return 2 * fc / sr * np.sinc(2 * fc / sr * n)
    kern = (lp(rng.uniform(2000.0, 3200.0)) - lp(rng.uniform(200.0, 350.0))) * np.hanning(taps)
    kern = (kern / np.sqrt(np.sum(kern * kern))).astype(np.float32)
    p1, p2 = rng.uniform(45.0, 90.0), rng.uniform(110.0, 200.0)
    f1, f2 = rng.uniform(0, 2 * np.pi), rng.uniform(0, 2 * np.pi)
    air_level = rng.uniform(0.010, 0.018)

    def air_swell(t: np.ndarray) -> np.ndarray:
        e = (0.5 + 0.5 * np.sin(2 * np.pi * t / p1 + f1)) * (0.55 + 0.45 * np.sin(2 * np.pi * t / p2 + f2))
        return air_level * (0.15 + 0.85 * e * e)

    fade_in = min(6.0, seconds / 4)
    fade_out = min(8.0, seconds / 4)
    return _Plan(seconds, sr, n_samples, title, key, mode_name, voices, ir, kern, air_swell,
                 seed_int, fade_in, fade_out)


class _OLAConvolver:
    """Streaming FFT convolution (overlap-add) of (2, n) blocks with a fixed
    kernel; carries the kernel tail between blocks so chunk joins are seamless."""

    def __init__(self, kernel: np.ndarray, block: int):
        k = np.atleast_2d(kernel)
        self.taps = k.shape[-1]
        self.nfft = 1 << int(np.ceil(np.log2(block + self.taps - 1)))
        self.H = np.fft.rfft(k, n=self.nfft, axis=-1)
        self.tail = np.zeros((2, self.taps - 1), dtype=np.float32)

    def process(self, x: np.ndarray) -> np.ndarray:
        n = x.shape[-1]
        y = np.fft.irfft(np.fft.rfft(x, n=self.nfft, axis=-1) * self.H, n=self.nfft, axis=-1)
        y = y[:, : n + self.taps - 1]
        y[:, : self.taps - 1] += self.tail
        self.tail = y[:, n:].copy()
        return y[:, :n]


def _render(plan: _Plan) -> Iterator[np.ndarray]:
    """Yield float32 (2, n) blocks of the finished (pre-encode) master."""
    sr, N = plan.sample_rate, plan.n_samples
    L = plan.ir.shape[-1]
    # Block size: fill a power-of-two FFT around the reverb tail.
    nfft = 1 << int(np.ceil(np.log2(2 * L)))
    block = (nfft - L + 1) // _CTRL * _CTRL
    reverb = _OLAConvolver(plan.ir, block)
    air_f = _OLAConvolver(plan.air_kernel, block)
    noise_rng = np.random.default_rng(plan.seed_int ^ 0x5EED)

    voices = sorted(plan.voices, key=lambda v: v.start)
    for c0 in range(0, N, block):
        c1 = min(c0 + block, N)
        n = c1 - c0
        bus = np.zeros((2, 2, n), dtype=np.float32)   # [bus][channel][sample]
        for v in voices:
            if v.start >= c1:
                break
            if v.end <= c0:
                continue
            a, b = max(v.start, c0), min(v.end, c1)
            m = b - a
            # Phase accumulator, evaluated in closed form from the voice's own
            # start (float64 so hours-long drones keep exact pitch).
            ph = np.arange(a - v.start, b - v.start, dtype=np.float64)
            ph *= v.inc
            ph += v.phase0
            ph -= np.floor(ph)
            ph *= _TABLE_N            # exact (power of two), so stays < N
            i0 = ph.astype(np.int32)
            x = (ph - i0).astype(np.float32)
            lo = v.table[i0]
            y = lo + x * (v.table[i0 + 1] - lo)
            kt = (a + np.arange(0, m, _CTRL, dtype=np.float64)) / sr
            y *= np.repeat(v.env(kt).astype(np.float32), _CTRL)[:m]
            out = bus[v.bus, :, a - c0: b - c0]
            out[0] += v.gl * y
            out[1] += v.gr * y

        kt = (c0 + np.arange(0, n, _CTRL, dtype=np.float64)) / sr
        noise = noise_rng.standard_normal((2, n), dtype=np.float32)
        air = air_f.process(noise) * np.repeat(plan.air_swell(kt).astype(np.float32), _CTRL)[:n]

        pads, bells = bus[0], bus[1]
        wet = reverb.process(pads + 1.8 * bells + 0.6 * air)
        mix = 0.55 * pads + 0.45 * bells + 0.6 * air + 0.75 * wet

        fade = _smooth(kt / plan.fade_in) * _smooth((plan.seconds - kt) / plan.fade_out)
        mix *= np.repeat(fade.astype(np.float32), _CTRL)[:n]
        # Gentle master: at this gain the mix sits around -20 LUFS with peaks
        # near 0.4, where tanh is still almost linear and only rounds off the
        # rare stacked peak. Loudness is trimmed to target at encode time.
        mix *= 0.35
        np.tanh(mix, out=mix)
        yield mix


def render_pcm(seconds: float, seed: str, sample_rate: int = 44100) -> np.ndarray:
    """The whole piece as float32 (n, 2). For tests and short previews; long
    pieces should stream through compose_piece instead."""
    plan = _plan(seconds, seed, sample_rate)
    return np.concatenate(list(_render(plan)), axis=1).T.copy()


def compose_piece(out_path: Path, *, seconds: float, seed: str, sample_rate: int = 44100) -> Track:
    """Compose one piece to `out_path` (.m4a, AAC 192k stereo, TARGET_LUFS)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plan = _plan(seconds, seed, sample_rate)
    wav = out_path.with_name(f".{out_path.stem}.{os.getpid()}.{threading.get_ident()}.wav")
    peak = 0.0
    try:
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            for blk in _render(plan):
                peak = max(peak, float(np.max(np.abs(blk))))
                w.writeframes(np.round(blk.T * 32767.0).astype("<i2").tobytes())
        _encode_normalised(wav, out_path, peak)
    finally:
        wav.unlink(missing_ok=True)
    return Track(
        id=f"composed-{_slug(seed)}", path=out_path, genre="ambient", licence="owned",
        title=plan.title, artist="Space Screens",
        notes=f"composed by romanfeed.audio.composer, seed={seed}",
        tags=["composed", plan.mode, plan.key],
    )


def _encode_normalised(wav: Path, dest: Path, peak: float, *, target_lufs: float = TARGET_LUFS) -> None:
    """Measure integrated loudness, then encode AAC 192k with one linear gain.

    Same result as library.normalise_track's linear loudnorm (a single gain
    to target), at about half the ffmpeg CPU: loudnorm oversamples to 192 kHz
    for its true-peak analysis in both passes, while our master is soft-
    limited with ~12 dB of peak-to-loudness headroom, so the sample peak is
    the only guard needed: the gain never lifts it past -1 dBFS."""
    gain = target_lufs - measure_loudness(wav)
    if peak > 0:
        gain = min(gain, -1.0 - 20.0 * float(np.log10(peak)))
    ffmpeg.run(["-i", str(wav), "-af", f"volume={gain:.3f}dB", "-ar", "44100", "-ac", "2",
                "-c:a", "aac", "-b:a", "192k", str(dest)])


def measure_loudness(path: Path) -> float:
    """Integrated loudness (LUFS) of an audio file, via ffmpeg's ebur128."""
    res = subprocess.run(
        [ffmpeg.ffmpeg_path(), "-hide_banner", "-nostats", "-i", str(path),
         "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", res.stderr)
    if not m:
        raise RuntimeError(f"could not measure loudness of {path}: {res.stderr[-300:]}")
    return float(m[-1])


class ComposedLibrary:
    """Drop-in for MusicLibrary that composes its tracks instead of reading a
    manifest. Pieces are written lazily on the first for_genre() call (so a
    run that never needs audio composes nothing) and reused after that, e.g.
    by the extra-length cuts. Every piece is ambient; the composer has one
    style, so the requested genre does not change what comes back.

    Pieces are composed in parallel threads: numpy's FFT and ufuncs release
    the GIL on large arrays and the encode is an ffmpeg subprocess."""

    def __init__(self, work_dir: str | Path, *, count: int, seconds_each: float, seed: str,
                 workers: int | None = None):
        self.work_dir = Path(work_dir)
        self.manifest_path = self.work_dir   # build_soundtrack names it in its error message
        self.count = count
        self.seconds_each = seconds_each
        self.seed = seed
        self.workers = workers or min(count, os.cpu_count() or 1) or 1
        self.tracks: list[Track] = []

    def _compose_all(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        seeds = [f"{self.seed}-{i}" for i in range(self.count)]

        def one(s: str) -> Track:
            return compose_piece(self.work_dir / f"composed-{_slug(s)}.m4a", seconds=self.seconds_each, seed=s)

        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            self.tracks = list(pool.map(one, seeds))
        for t in self.tracks:
            log.info("composed %s: %s", t.id, t.title)

    def for_genre(self, genre: str, *, publishable_only: bool = True) -> list[Track]:
        if not self.tracks and self.count > 0:
            self._compose_all()
        return [t for t in self.tracks if t.path.exists()]


def open_library(audio, *, work_dir: Path, seed: str):
    """The soundtrack source a channel's AudioSettings asks for: the licensed
    manifest ("library") or freshly composed pieces ("composed")."""
    from romanfeed.audio.library import MusicLibrary

    if audio.source == "composed":
        return ComposedLibrary(Path(work_dir) / "composed", count=audio.composed_pieces,
                               seconds_each=audio.composed_seconds, seed=seed)
    return MusicLibrary(audio.library)


__all__ = ["ComposedLibrary", "compose_piece", "measure_loudness", "open_library", "render_pcm"]
