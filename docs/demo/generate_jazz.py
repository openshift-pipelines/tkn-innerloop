#!/usr/bin/env python3
"""
Synthesizes a small jazz combo backing track (stereo 44.1kHz WAV) for the demo:
medium swing, walking upright bass, comping piano, ride cymbal and vibraphone
head over a ii-V-I loop in F. Length follows the video duration.
"""

import math
import sys
import wave

import numpy as np

SR = 44100
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
OUT_WAV = sys.argv[2] if len(sys.argv) > 2 else "docs/demo/jazz-backing.wav"

BPM = 126.0
BEAT = 60.0 / BPM
SWING = 2.0 / 3.0  # the second eighth of a beat lands on the last triplet

N = int(SR * DURATION)
left = np.zeros(N, dtype=np.float64)
right = np.zeros(N, dtype=np.float64)


def freq(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def place(buf_l: np.ndarray, buf_r: np.ndarray, start_s: float, sig: np.ndarray, pan: float = 0.0) -> None:
    i = int(start_s * SR)
    if i < 0 or i >= N:
        return
    j = min(N, i + len(sig))
    seg = sig[: j - i]
    angle = (pan + 1.0) * math.pi / 4.0
    buf_l[i:j] += seg * math.cos(angle)
    buf_r[i:j] += seg * math.sin(angle)


def piano(f: float, dur: float, vel: float = 0.20) -> np.ndarray:
    """Comping voicing: soft attack, slightly detuned pair, bell-ish upper partial."""
    n = int(SR * dur)
    t = np.arange(n) / SR
    env = (1.0 - np.exp(-t * 220.0)) * np.exp(-t * 2.9)
    body = np.sin(2 * math.pi * f * t) + 0.34 * np.sin(2 * math.pi * 2 * f * t)
    honky = 0.16 * np.sin(2 * math.pi * (f * 1.0035) * t) * np.exp(-t * 7.0)
    return (body + honky) * env * vel


def upright(f: float, dur: float, vel: float = 0.34) -> np.ndarray:
    """Walking bass: round fundamental, short finger noise on the attack."""
    n = int(SR * dur)
    t = np.arange(n) / SR
    env = (1.0 - np.exp(-t * 150.0)) * np.exp(-t * 4.4)
    thump = 0.4 * np.exp(-t * 26.0) * np.sin(2 * math.pi * f * 2.8 * t)
    return (np.sin(2 * math.pi * f * t) + 0.3 * np.sin(2 * math.pi * 2 * f * t) + thump) * env * vel


def vibe(f: float, dur: float, vel: float = 0.17) -> np.ndarray:
    """Vibraphone: fast sine tremolo, bell partial, long tail."""
    n = int(SR * dur)
    t = np.arange(n) / SR
    trem = 1.0 - 0.22 * (1.0 + np.sin(2 * math.pi * 5.4 * t)) / 2.0
    env = (1.0 - np.exp(-t * 420.0)) * np.exp(-t * 2.2)
    bell = 0.3 * np.exp(-t * 9.0) * np.sin(2 * math.pi * 4 * f * t)
    return (np.sin(2 * math.pi * f * t) + bell) * env * trem * vel


def ride(accent: float = 1.0) -> np.ndarray:
    """Ride cymbal ping: bright, short, with a little wash."""
    n = int(SR * 0.20)
    t = np.arange(n) / SR
    rng = np.random.default_rng(int(accent * 7919) + 3)
    noise = rng.standard_normal(n)
    hp = np.diff(noise, prepend=0.0)  # tilt towards the highs
    ping = np.sin(2 * math.pi * 3200.0 * t) + 0.5 * np.sin(2 * math.pi * 5100.0 * t)
    env = np.exp(-t * 22.0)
    wash = 0.25 * np.exp(-t * 9.0)
    return (0.5 * hp + 0.55 * ping * env + 0.35 * hp * wash) * 0.02 * accent


def hihat_foot() -> np.ndarray:
    n = int(SR * 0.10)
    t = np.arange(n) / SR
    rng = np.random.default_rng(4242)
    hp = np.diff(np.diff(rng.standard_normal(n), prepend=0.0), prepend=0.0)
    return hp * np.exp(-t * 70.0) * 0.012


def brush_kick() -> np.ndarray:
    """A feathered bass drum, felt more than heard."""
    n = int(SR * 0.16)
    t = np.arange(n) / SR
    sweep = 48.0 + 60.0 * np.exp(-t * 40.0)
    phase = 2 * math.pi * np.cumsum(sweep) / SR
    return np.sin(phase) * np.exp(-t * 24.0) * 0.16


# ii-V-I in F, with a turnaround: (walking-bass roots, piano voicing, melody scale)
PROGRESSION = [
    (43, [58, 63, 69, 72], [69, 72, 74, 76, 77, 79, 81]),  # Gm9
    (36, [58, 64, 69, 70], [70, 72, 74, 76, 77, 79, 81]),  # C13
    (41, [57, 60, 64, 67], [69, 72, 74, 76, 77, 81, 84]),  # Fmaj9
    (38, [57, 60, 64, 65], [65, 69, 72, 74, 76, 77, 81]),  # Dm9
    (43, [58, 63, 69, 72], [69, 72, 74, 76, 77, 79, 81]),  # Gm9
    (36, [58, 64, 69, 70], [70, 72, 74, 76, 77, 79, 81]),  # C13
    (45, [55, 60, 64, 67], [67, 69, 72, 76, 79, 81, 84]),  # Am7
    (43, [57, 63, 65, 69], [65, 69, 70, 72, 74, 77, 79]),  # D7 / Gm7 turnaround
]

# A swinging head: (swung eighth index in the bar, scale degree, length in eighths)
HEAD = [
    [(0, 3, 1), (1, 4, 1), (3, 3, 2), (6, 1, 2)],
    [(0, 2, 1), (1, 3, 1), (2, 5, 2), (5, 4, 1), (6, 2, 2)],
    [(0, 0, 2), (2, 2, 1), (3, 4, 1), (5, 3, 3)],
    [(0, 2, 1), (1, 1, 1), (2, 3, 2), (5, 5, 3)],
    [(0, 4, 1), (1, 3, 1), (3, 2, 2), (6, 0, 2)],
    [(0, 5, 2), (3, 4, 1), (4, 2, 1), (6, 3, 2)],
    [(0, 1, 1), (1, 3, 1), (2, 4, 2), (5, 1, 3)],
    [(0, 3, 1), (1, 2, 1), (2, 0, 2), (4, 2, 2), (6, 1, 2)],
]

# Comping punches, as swung eighth positions in the bar.
COMP = {0: [(0, 2), (3, 2), (6, 2)], 1: [(2, 2), (4, 3)], 2: [(0, 3), (4, 2)], 3: [(3, 2), (6, 2)]}

bars = math.ceil(DURATION / (4 * BEAT)) + 1
for bar in range(bars):
    t0 = bar * 4 * BEAT
    root, voicing, scale = PROGRESSION[bar % len(PROGRESSION)]

    # Walking bass: one note per beat, chromatic approach into the next root.
    next_root = PROGRESSION[(bar + 1) % len(PROGRESSION)][0]
    walk = [root, root + (7 if bar % 2 else 5), root + 12 - 2, next_root - 1]
    for beat, note in enumerate(walk):
        place(left, right, t0 + beat * BEAT, upright(freq(note), BEAT * 0.92, 0.34), pan=-0.05)

    # Ride pattern (spang-a-lang) and hi-hat on 2 and 4.
    for beat in range(4):
        place(left, right, t0 + beat * BEAT, ride(1.15 if beat % 2 == 0 else 0.8), pan=0.34)
        place(left, right, t0 + (beat + SWING) * BEAT, ride(0.62 if beat in (0, 2) else 0.75), pan=0.40)
        if beat in (1, 3):
            place(left, right, t0 + beat * BEAT, hihat_foot(), pan=-0.34)
        place(left, right, t0 + beat * BEAT, brush_kick(), pan=0.0)

    # Piano comping, off-beat and sparse.
    for step, length in COMP[bar % len(COMP)]:
        when = t0 + (step // 2) * BEAT + (0 if step % 2 == 0 else SWING * BEAT)
        for i, note in enumerate(voicing):
            place(left, right, when + i * 0.006, piano(freq(note), length * (BEAT / 2) * 1.2, 0.085), pan=-0.24 + 0.05 * i)

    # Vibraphone head, swung.
    for step, degree, length in HEAD[bar % len(HEAD)]:
        when = t0 + (step // 2) * BEAT + (0 if step % 2 == 0 else SWING * BEAT)
        note = scale[degree % len(scale)]
        place(left, right, when, vibe(freq(note), length * (BEAT / 2) * 1.15, 0.16), pan=0.2)

# Small club room: one early reflection pair, then a short tail.
for delay, gain in ((0.011, 0.20), (0.019, 0.14), (0.031, 0.09)):
    d = int(delay * SR)
    left[d:] += gain * right[:-d]
    right[d:] += gain * left[:-d]

fade = int(1.2 * SR)
left[:fade] *= np.linspace(0.0, 1.0, fade)
right[:fade] *= np.linspace(0.0, 1.0, fade)
left[-int(2.5 * SR):] *= np.linspace(1.0, 0.0, int(2.5 * SR))
right[-int(2.5 * SR):] *= np.linspace(1.0, 0.0, int(2.5 * SR))

peak = max(np.max(np.abs(left)), np.max(np.abs(right)), 1e-9)
left = np.clip(left * (0.34 / peak), -1.0, 1.0)
right = np.clip(right * (0.34 / peak), -1.0, 1.0)

pcm = np.empty(N * 2, dtype=np.int16)
pcm[0::2] = (left * 32767).astype(np.int16)
pcm[1::2] = (right * 32767).astype(np.int16)
with wave.open(OUT_WAV, "wb") as wf:
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(SR)
    wf.writeframes(pcm.tobytes())

print(f"Generated jazz swing backing track: {OUT_WAV} ({DURATION:.2f}s)")
