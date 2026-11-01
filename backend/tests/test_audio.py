"""The speech model adds a click at the start and a loud burst at the end. The cleaner must remove both
and must never cut real speech."""
import io
import wave

import numpy as np

from llm.audio import clean_wav, trim_artifacts

SR = 24000


def ms(n):
    return int(SR * n / 1000)


def speech(seconds, level=0.2):
    """Syllable-like sound: a tone whose loudness rises and falls 4 times a second (never fully silent)."""
    t = np.arange(int(SR * seconds)) / SR
    return (level * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t)) * np.sin(2 * np.pi * 180 * t)).astype(np.float32)


def silence(n_ms):
    return np.zeros(ms(n_ms), dtype=np.float32)


def burst(n_ms, level=0.9):
    t = np.arange(ms(n_ms)) / SR
    return (level * np.sin(2 * np.pi * 900 * t)).astype(np.float32)        # flat and loud, like the real artifact


def to_wav(x):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return out.getvalue()


def test_removes_start_click_and_end_burst_but_keeps_all_speech():
    s = speech(3.0)
    x = np.concatenate([burst(20, 0.85), silence(150), s, silence(250), burst(140)])
    y = trim_artifacts(x, SR)
    # click+gap trimmed to 50 ms of lead-in; burst and most of the silence before it removed (120 ms kept)
    assert abs(len(y) / SR - (0.050 + 3.0 + 0.120)) < 0.02
    assert np.abs(y[-ms(300):]).max() < 0.3                      # no loud noise at the end any more
    assert np.abs(y[:ms(40)]).max() < 0.01                       # no click at the start
    start = ms(50)
    assert np.array_equal(y[start:start + len(s)], s)            # the speech itself is untouched, sample for sample


def test_flat_burst_after_a_short_pause_is_removed():
    s = speech(2.0)
    x = np.concatenate([s, silence(60), burst(130)])
    y = trim_artifacts(x, SR)
    assert np.abs(y[-ms(200):]).max() < 0.3 and len(y) < len(x) - ms(100)


def test_real_speech_is_never_cut():
    for x in (speech(3.0),                                                           # plain speech
              np.concatenate([speech(2.0), silence(300)]),                           # speech then silence
              np.concatenate([speech(2.0), silence(80), speech(0.3, 0.25)]),         # short word after a short pause
              np.concatenate([silence(200), speech(2.0)])):                           # quiet lead-in
        assert len(trim_artifacts(x, SR)) == len(x)


def test_clean_wav_roundtrip_and_idempotent():
    x = np.concatenate([burst(20, 0.85), silence(150), speech(2.0), silence(250), burst(140)])
    wav = to_wav(x)
    once = clean_wav(wav)
    assert len(once) < len(wav)
    with wave.open(io.BytesIO(once)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SR)
    assert clean_wav(once) == once                                # cleaning again changes nothing
    assert clean_wav(to_wav(speech(2.0))) == to_wav(speech(2.0))  # a clean clip is returned as it was


def test_very_short_or_silent_audio_is_left_alone():
    assert len(trim_artifacts(np.zeros(100, dtype=np.float32), SR)) == 100
    assert len(trim_artifacts(silence(500), SR)) == ms(500)
