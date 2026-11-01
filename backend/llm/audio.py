"""Clean up the speech model's output.

The text-to-speech model adds two artifacts to EVERY clip (found by analysing real recordings):
  * a tiny click in the first ~20 ms, followed by silence, and
  * a loud noise burst (about 0.1-0.2 s, louder than the speech itself) at the very end, after a
    quarter second of digital silence.
Both are trimmed here. The rules are deliberately narrow so real speech is never cut: a burst is only
removed if it comes after a clear silence, is short, and is much louder than the speech."""
import io
import wave

import numpy as np

WINDOW_MS = 10
SILENCE = 0.004            # below this a 10 ms window counts as silence
SPEECH = 0.02              # above this it counts as speech
MIN_GAP_WINDOWS = 12       # the burst must follow >= 120 ms of silence (or >= 40 ms if it is a flat plateau)
SHORT_GAP_WINDOWS = 4
MAX_BURST_WINDOWS = 40     # ... and last <= 400 ms
LOUDER_THAN_SPEECH = 1.8   # ... and be much louder than the typical speech level
KEEP_SILENCE_WINDOWS = 12  # leave 120 ms of silence so the end sounds natural
MAX_BLIP_WINDOWS = 6       # a start click is <= 60 ms and followed by >= 60 ms of silence
LEAD_PAD_WINDOWS = 5       # keep 50 ms of silence before the first word


def _envelope(x: np.ndarray, rate: int):
    win = max(1, int(rate * WINDOW_MS / 1000))
    k = len(x) // win
    if k == 0:
        return np.zeros(0), win
    return np.sqrt((x[: k * win].reshape(k, win) ** 2).mean(axis=1)), win


def trim_artifacts(x: np.ndarray, rate: int = 24000) -> np.ndarray:
    """x: mono float samples in [-1, 1]. Returns the cleaned samples (possibly shorter)."""
    env, win = _envelope(x, rate)
    if len(env) < 50:
        return x
    voiced = env[env > SPEECH]
    if len(voiced) < 10:
        return x
    speech_level = float(np.median(voiced))

    # --- trailing burst: [speech] [silence >= 120 ms] [short, loud burst] [end]
    i = len(env) - 1
    while i >= 0 and env[i] >= SILENCE:                 # walk back over the trailing sound
        i -= 1
    burst = len(env) - 1 - i
    if 0 < burst <= MAX_BURST_WINDOWS and i >= 0:
        j = i
        while j >= 0 and env[j] < SILENCE:              # and over the silence in front of it
            j -= 1
        gap = i - j
        tail = env[i + 1:]
        # The burst is a flat plateau; real speech rises and falls with every syllable. A flat, very loud
        # tail is accepted after a short pause too, so it can be removed even when the gap is small.
        flat = len(tail) >= 6 and float(np.std(tail[1:-1]) / (np.mean(tail[1:-1]) + 1e-9)) < 0.15             and float(np.mean(tail[1:-1])) > 2.0 * speech_level
        if j >= 0 and gap >= SHORT_GAP_WINDOWS and tail.max() > LOUDER_THAN_SPEECH * speech_level                 and (gap >= MIN_GAP_WINDOWS or flat):
            x = x[: (j + 1 + min(gap, KEEP_SILENCE_WINDOWS)) * win]
            env, win = _envelope(x, rate)

    # --- leading click: [short sound <= 60 ms] [silence >= 60 ms] [speech]
    k = 0
    while k < len(env) and env[k] >= SILENCE:
        k += 1
    if 0 < k <= MAX_BLIP_WINDOWS:
        m = k
        while m < len(env) and env[m] < SILENCE:
            m += 1
        if m < len(env) and m - k >= 6:
            x = x[max(0, m - LEAD_PAD_WINDOWS) * win:]
    return x


def clean_wav(wav: bytes) -> bytes:
    """Same as trim_artifacts, for a 16-bit mono WAV file in memory. Returns the input if nothing changed."""
    with wave.open(io.BytesIO(wav)) as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        frames = w.readframes(w.getnframes())
    if width != 2 or channels != 1:
        return wav
    pcm = np.frombuffer(frames, dtype="<i2")
    cleaned = trim_artifacts(pcm.astype(np.float32) / 32768.0, rate)
    if len(cleaned) == len(pcm):
        return wav
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(cleaned, -1, 1) * 32767).astype("<i2").tobytes())
    return out.getvalue()
