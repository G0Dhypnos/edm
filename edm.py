"""Procedural EDM generator: every track is synthesized from scratch with
numpy (no samples, no loops from anyone else), so each result is original."""
import wave

import numpy as np

SR = 44100

PROGRESSIONS = [
    [(0, "m"), (8, "M"), (3, "M"), (10, "M")],
    [(0, "m"), (10, "M"), (8, "M"), (10, "M")],
    [(0, "m"), (5, "m"), (8, "M"), (10, "M")],
    [(0, "m"), (8, "M"), (10, "M"), (7, "M")],
]
CHORDS = {"m": (0, 3, 7), "M": (0, 4, 7)}


def _mid(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def _t(n):
    return np.arange(n, dtype=np.float64) / SR


def _lp(x, w):
    return np.convolve(x, np.ones(w) / w, mode="same")


def _saw(f, n):
    return 2 * ((f * _t(n)) % 1) - 1


def _kick():
    t = _t(int(0.4 * SR))
    f = 45 + 110 * np.exp(-t * 28)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t * 8)


def _hat(rng, decay):
    n = int(0.12 * SR)
    noise = np.diff(rng.standard_normal(n), prepend=0)
    return noise * np.exp(-_t(n) * decay)


def _clap(rng):
    n = int(0.2 * SR)
    t = _t(n)
    noise = _lp(np.diff(rng.standard_normal(n), prepend=0), 3)
    env = np.exp(-t * 18)
    for k in (0.0, 0.01, 0.02):
        off = int(k * SR)
        env[off:] += 0.6 * np.exp(-t[: n - off] * 60)
    return noise * env


def _pluck(f):
    n = int(0.18 * SR)
    t = _t(n)
    wave_ = 0.6 * _saw(f, n) + 0.4 * np.sign(np.sin(2 * np.pi * f * t))
    return _lp(wave_, 6) * np.exp(-t * 18)


def _bass(f, n):
    t = _t(n)
    body = 0.6 * _lp(_saw(f, n), 16) + 0.6 * np.sin(2 * np.pi * f * t)
    return body * np.minimum(1, t / 0.005) * np.exp(-t * 5)


def _pad(freqs, n):
    t = _t(n)
    sig = np.zeros(n)
    for f in freqs:
        for d in (0.997, 1.003):
            sig += _saw(f * d, n)
    sig = _lp(sig / (2 * len(freqs)), 14)
    env = np.minimum(1, t / 0.25) * np.clip((n / SR - t) / 0.3, 0, 1)
    return sig * env


def generate(bpm: int, bars: int, path, seed=None) -> float:
    """Render an EDM track to a stereo 16-bit WAV. Returns length in seconds."""
    rng = np.random.default_rng(seed)
    beat = int(round(60 / bpm * SR))
    bar = beat * 4
    sixt = beat // 4
    n = bar * bars + SR  # 1s tail
    drums = np.zeros((n, 2), np.float32)
    tonal = np.zeros((n, 2), np.float32)

    def put(buf, s, pos, gain, pan=0.5):
        if pos >= n:
            return
        end = min(n, pos + len(s))
        seg = (s[: end - pos] * gain).astype(np.float32)
        buf[pos:end, 0] += seg * np.sqrt(1 - pan)
        buf[pos:end, 1] += seg * np.sqrt(pan)

    pc = int(rng.integers(0, 12))
    prog = PROGRESSIONS[int(rng.integers(len(PROGRESSIONS)))]
    kick, clap = _kick(), _clap(rng)
    hat_c, hat_o = _hat(rng, 40), _hat(rng, 14)
    plucks = {}
    pat = [0, 1, 2, 3, 2, 3, 1, 2]

    for b in range(bars):
        deg, q = prog[b % 4]
        root = (pc + deg) % 12
        iv = CHORDS[q]
        start = b * bar
        intro = b < 4
        breakdown = bars >= 24 and (b % 16) in (12, 13, 14, 15)
        drop = not intro and not breakdown

        for k in range(4):
            pos = start + k * beat
            if not breakdown:
                put(drums, kick, pos, 0.95)
            if drop and k in (1, 3):
                put(drums, clap, pos, 0.45)
            put(drums, hat_o, pos + beat // 2, 0.28)
            put(drums, hat_c, pos + beat // 4, 0.12)
            put(drums, hat_c, pos + 3 * beat // 4, 0.12)
            if drop:
                bm = 36 + root
                if bm >= 44:
                    bm -= 12
                put(tonal, _bass(_mid(bm), beat // 2), pos + beat // 2, 0.55)

        base = 48 + root
        put(tonal, _pad([_mid(base + i) for i in iv], bar), start, 0.16)

        if b >= 4 or breakdown:
            tones = [iv[0], iv[1], iv[2], 12]
            for s in range(16):
                note = 60 + root + tones[pat[s % 8]]
                if note not in plucks:
                    plucks[note] = _pluck(_mid(note))
                pos = start + s * sixt
                put(tonal, plucks[note], pos, 0.2, 0.35)
                put(tonal, plucks[note], pos + 3 * sixt, 0.09, 0.7)

    # sidechain pump: tonal layers duck on every kick
    phase = (np.arange(n) % beat) / beat
    duck = (1 - 0.8 * np.exp(-phase * 7)).astype(np.float32)
    mix = drums + tonal * duck[:, None]

    fade = np.ones(n, np.float32)
    L = min(n, bar + SR)
    fade[-L:] = np.linspace(1, 0, L, dtype=np.float32)
    mix *= fade[:, None]
    mix *= 0.89 / max(1e-6, float(np.abs(mix).max()))

    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype("<i2").tobytes())
    return n / SR
