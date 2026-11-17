#!/usr/bin/env python3
"""
Synthesizes a warm, cheerful Bossa Nova / Rhodes Lounge elevator music track
(stereo 44.1kHz WAV) tailored to the exact video duration.
"""

import math
import sys
import wave
import numpy as np

SR = 44100
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 56.5
OUT_WAV = sys.argv[2] if len(sys.argv) > 2 else "demo/elevator-music.wav"

BPM = 116.0
BEAT = 60.0 / BPM
STEP = BEAT / 2.0  # 8th note

N_SAMPLES = int(SR * DURATION)
left = np.zeros(N_SAMPLES, dtype=np.float64)
right = np.zeros(N_SAMPLES, dtype=np.float64)


def midi_to_freq(m: int) -> float:
    return 440.0 * (2.0 ** ((m - 69.0) / 12.0))


def add_stereo(buf_l: np.ndarray, buf_r: np.ndarray, start_s: float, sig: np.ndarray, pan: float = 0.0):
    idx = int(start_s * SR)
    if idx >= N_SAMPLES or idx < 0:
        return
    end = min(N_SAMPLES, idx + len(sig))
    seg = sig[: end - idx]
    # Constant power pan (-1.0 left .. +1.0 right)
    angle = (pan + 1.0) * math.pi / 4.0
    buf_l[idx:end] += seg * math.cos(angle)
    buf_r[idx:end] += seg * math.sin(angle)


def rhodes_note(freq: float, dur: float, vel: float = 0.25) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n, dtype=np.float64) / SR
    # Warm electric piano (fundamental + bell tine overtone with fast decay + subtle chorus)
    env = (1.0 - np.exp(-t * 140.0)) * np.exp(-t * 2.4)
    tine_env = np.exp(-t * 14.0)
    mod = 1.4 * np.exp(-t * 6.0) * np.sin(2.0 * math.pi * freq * 2.0 * t)
    sig = (
        0.75 * np.sin(2.0 * math.pi * freq * t + mod)
        + 0.20 * np.sin(2.0 * math.pi * (freq * 1.002) * t)
        + 0.18 * tine_env * np.sin(2.0 * math.pi * freq * 7.0 * t)
    )
    return sig * env * vel


def marimba_bell_note(freq: float, dur: float, vel: float = 0.22) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n, dtype=np.float64) / SR
    env = (1.0 - np.exp(-t * 300.0)) * np.exp(-t * 5.5)
    woody = np.exp(-t * 28.0) * np.sin(2.0 * math.pi * freq * 4.0 * t)
    sig = np.sin(2.0 * math.pi * freq * t) + 0.35 * woody + 0.12 * np.sin(2.0 * math.pi * freq * 2.0 * t)
    return sig * env * vel


def upright_bass_note(freq: float, dur: float, vel: float = 0.38) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n, dtype=np.float64) / SR
    env = (1.0 - np.exp(-t * 110.0)) * np.exp(-t * 3.8)
    pluck = np.exp(-t * 18.0) * 0.5 * np.sin(2.0 * math.pi * freq * 3.0 * t)
    sig = np.sin(2.0 * math.pi * freq * t) + 0.45 * np.sin(2.0 * math.pi * freq * 2.0 * t) + pluck
    return sig * env * vel


def shaker_hit(accent: float = 1.0) -> np.ndarray:
    n = int(SR * 0.065)
    t = np.arange(n, dtype=np.float64) / SR
    rng = np.random.default_rng(int(accent * 1000) + 7)
    noise = rng.standard_normal(n)
    # High-pass via simple diff twice
    hp = np.diff(np.diff(noise, prepend=0.0), prepend=0.0)
    env = np.sin(math.pi * t / 0.065) ** 2
    return hp * env * 0.018 * accent


def rim_click() -> np.ndarray:
    n = int(SR * 0.03)
    t = np.arange(n, dtype=np.float64) / SR
    env = np.exp(-t * 160.0)
    sig = np.sin(2.0 * math.pi * 1650.0 * t) + 0.6 * np.sin(2.0 * math.pi * 920.0 * t)
    return sig * env * 0.07


def soft_kick() -> np.ndarray:
    n = int(SR * 0.14)
    t = np.arange(n, dtype=np.float64) / SR
    freq_sweep = 52.0 + 75.0 * np.exp(-t * 45.0)
    phase = 2.0 * math.pi * np.cumsum(freq_sweep) / SR
    env = np.exp(-t * 22.0)
    return np.sin(phase) * env * 0.26


# Cheerful Bossa-Lounge Jazz Chord Progression (8-bar loop, 4 beats per bar):
# | Cmaj9  | A7b13  | Dm9    | G13    |
# | Em7    | A7     | Dm9    | G13    |
PROGRESSION = [
    # (root_midi, fifth_midi, chord_midis, scale_midis)
    (36, 43, [60, 64, 67, 71, 74], [72, 74, 76, 79, 83, 84]),  # Cmaj9
    (45, 52, [57, 61, 64, 67, 73], [73, 76, 79, 81, 82, 85]),  # A7
    (38, 45, [62, 65, 69, 72, 76], [74, 76, 77, 81, 84, 86]),  # Dm9
    (43, 50, [59, 62, 65, 69, 76], [71, 74, 76, 79, 81, 83]),  # G13
    (40, 47, [64, 67, 71, 74, 78], [71, 74, 76, 79, 83, 86]),  # Em9
    (45, 52, [57, 61, 67, 69, 73], [73, 76, 79, 81, 85, 88]),  # A7
    (38, 45, [62, 65, 69, 72, 76], [74, 77, 81, 84, 86, 88]),  # Dm9
    (43, 50, [59, 62, 65, 69, 74], [74, 76, 79, 83, 84, 86]),  # G13
]

# Pre-composed cheerful melody motifs (in 8th-note steps 0..7 within a bar, index into scale_midis)
MELODY_PATTERNS = [
    [(0, 0, 1.5), (3, 2, 1.5), (5, 3, 1.0), (6, 4, 2.0)],
    [(1, 3, 1.0), (2, 2, 1.0), (4, 1, 2.0), (6, 0, 2.0)],
    [(0, 1, 1.5), (3, 3, 1.5), (5, 4, 1.0), (6, 5, 2.5)],
    [(0, 4, 1.0), (2, 3, 1.0), (3, 2, 1.5), (5, 1, 2.5)],
    [(0, 2, 1.5), (3, 4, 1.5), (5, 3, 1.5), (7, 2, 1.5)],
    [(1, 4, 1.0), (2, 3, 1.0), (4, 2, 2.0), (6, 1, 2.0)],
    [(0, 0, 1.0), (1, 1, 1.0), (2, 2, 1.5), (5, 4, 2.5)],
    [(0, 5, 1.5), (3, 4, 1.5), (5, 2, 1.0), (6, 0, 2.5)],
]

# Bossa Nova clave comping steps (in 8th notes 0..7 per bar)
COMP_STEPS = [(0, 1.4), (3, 1.4), (6, 1.2)]
RIM_STEPS = [0, 3, 6]

total_bars = int(math.ceil(DURATION / (4.0 * BEAT))) + 1

for bar in range(total_bars):
    bar_start = bar * 4.0 * BEAT
    root, fifth, chord_notes, scale_notes = PROGRESSION[bar % len(PROGRESSION)]

    # 1. Upright Bossa Bass (dotted quarter + eighth feel on beats 1, 2.5, 3, 4.5)
    bass_events = [
        (0.0, root, 1.4 * BEAT, 0.34),
        (1.5 * BEAT, fifth, 0.45 * BEAT, 0.24),
        (2.0 * BEAT, fifth, 1.4 * BEAT, 0.32),
        (3.5 * BEAT, root + 12, 0.45 * BEAT, 0.22),
    ]
    for offset, m_note, b_dur, vel in bass_events:
        sig = upright_bass_note(midi_to_freq(m_note), b_dur, vel)
        add_stereo(left, right, bar_start + offset, sig, pan=-0.08)

    # 2. Soft kick on beats 1 and 3, shaker on all 8ths, bossa rim click
    for b in (0.0, 2.0 * BEAT):
        add_stereo(left, right, bar_start + b, soft_kick(), pan=0.0)
    for step_idx in range(8):
        t_step = bar_start + step_idx * STEP
        accent = 1.25 if step_idx % 2 == 0 else 0.75
        add_stereo(left, right, t_step, shaker_hit(accent), pan=0.35)
        if step_idx in RIM_STEPS:
            add_stereo(left, right, t_step, rim_click(), pan=-0.30)

    # 3. Warm Rhodes Piano Comping
    for step_idx, dur_steps in COMP_STEPS:
        t_comp = bar_start + step_idx * STEP
        for i, c_midi in enumerate(chord_notes):
            # Slight human strum (8ms between notes)
            sig = rhodes_note(midi_to_freq(c_midi), dur_steps * STEP * 1.15, vel=0.11)
            add_stereo(left, right, t_comp + i * 0.008, sig, pan=-0.22 + 0.08 * i)

    # 4. Cheerful Vibraphone / Marimba Melody
    mel_pat = MELODY_PATTERNS[bar % len(MELODY_PATTERNS)]
    for step_idx, scale_idx, dur_steps in mel_pat:
        t_mel = bar_start + step_idx * STEP
        m_midi = scale_notes[scale_idx % len(scale_notes)]
        sig = marimba_bell_note(midi_to_freq(m_midi), dur_steps * STEP * 1.1, vel=0.17)
        add_stereo(left, right, t_mel, sig, pan=0.18)

# Add a gentle stereo room echo (180ms & 270ms)
delay1 = int(0.18 * SR)
delay2 = int(0.27 * SR)
left[delay1:] += 0.16 * right[:-delay1]
right[delay2:] += 0.16 * left[:-delay2]

# Smooth fade-in (0.8s) and fade-out (2.0s)
fade_in = int(0.8 * SR)
fade_out = int(2.0 * SR)
left[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
right[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
left[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)
right[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)

# Normalize to comfortable background level (-12 dBFS peak so it's pleasant)
peak = max(np.max(np.abs(left)), np.max(np.abs(right)), 1e-6)
gain = 0.35 / peak
left = np.clip(left * gain, -1.0, 1.0)
right = np.clip(right * gain, -1.0, 1.0)

stereo_pcm = np.empty((N_SAMPLES * 2,), dtype=np.int16)
stereo_pcm[0::2] = (left * 32767).astype(np.int16)
stereo_pcm[1::2] = (right * 32767).astype(np.int16)

with wave.open(OUT_WAV, "wb") as wf:
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(SR)
    wf.writeframes(stereo_pcm.tobytes())

print(f"Generated cheerful bossa-lounge elevator track: {OUT_WAV} ({DURATION:.2f}s)")
