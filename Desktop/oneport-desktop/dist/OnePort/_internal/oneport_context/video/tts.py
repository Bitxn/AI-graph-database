# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Text-to-speech — one consistent voice for the whole video.

Primary engine is **gTTS** (Google Translate TTS): a single natural voice, free,
no API key, no per-scene flipping. The engine is chosen once up front; a scene
that fails becomes a short silent gap in the SAME voice — never a different one.

Order: gTTS  →  pyttsx3 (offline, female)  →  silent.
Returns a `say(text, out_base) -> Path` closure (it picks .mp3/.wav itself).
"""
from __future__ import annotations

import time
import wave
from pathlib import Path

_SR = 24000
# --voice maps to a Google TTS accent (all natural, consistent female-leaning voices).
_TLD = {"us": "com", "uk": "co.uk", "au": "com.au", "in": "co.in", "ca": "ca", "ie": "ie"}


def build_voicer(api_key: str | None = None, voice: str = "us"):
    """Choose ONE engine up front. Returns (say_fn, engine_label)."""
    tld = _TLD.get(str(voice).lower(), "com")
    if _gtts_ok(tld):
        def say(text: str, out_base: Path) -> Path:
            return _gtts_say(text, out_base, tld)
        return say, f"gTTS (Google · {voice})"

    voice_id = _female_voice_id()
    if voice_id is not None or _pyttsx3_available():
        def say(text: str, out_base: Path) -> Path:
            return _pyttsx3_say(text, out_base, voice_id)
        return say, "pyttsx3 (offline, female)"

    def say(text: str, out_base: Path) -> Path:
        return _silent(text, out_base)
    return say, "silent (no network / no TTS)"


# ---- gTTS (primary) -------------------------------------------------------- #

def _gtts_ok(tld: str) -> bool:
    try:
        from gtts import gTTS
        import tempfile
        p = Path(tempfile.mktemp(suffix=".mp3"))
        gTTS(text="Ready.", lang="en", tld=tld).save(str(p))
        ok = p.exists() and p.stat().st_size > 200
        p.unlink(missing_ok=True)
        return ok
    except Exception:
        return False


def _gtts_say(text: str, out_base: Path, tld: str, retries: int = 3) -> Path:
    from gtts import gTTS
    out = out_base.with_suffix(".mp3")
    for attempt in range(retries):
        try:
            gTTS(text=text or "…", lang="en", tld=tld).save(str(out))
            if out.exists() and out.stat().st_size > 200:
                return out
        except Exception:
            time.sleep(1.5 * (attempt + 1))       # brief backoff; same voice throughout
    return _silent(text, out_base)                # a silent gap, not a voice change


# ---- pyttsx3 (offline female fallback) ------------------------------------- #

def _pyttsx3_available() -> bool:
    try:
        import pyttsx3  # noqa: F401
        return True
    except Exception:
        return False


def _female_voice_id() -> str | None:
    try:
        import pyttsx3
        eng = pyttsx3.init()
        voices = eng.getProperty("voices") or []
        eng.stop()
        for v in voices:
            blob = f"{getattr(v,'name','')} {getattr(v,'id','')}".lower()
            if any(t in blob for t in ("zira", "female", "hazel", "eva", "susan")):
                return v.id
        return voices[1].id if len(voices) > 1 else (voices[0].id if voices else None)
    except Exception:
        return None


def _pyttsx3_say(text: str, out_base: Path, voice_id: str | None) -> Path:
    out = out_base.with_suffix(".wav")
    try:
        import pyttsx3
        eng = pyttsx3.init()
        if voice_id:
            eng.setProperty("voice", voice_id)
        eng.setProperty("rate", 172)
        eng.save_to_file(text, str(out))
        eng.runAndWait()
        eng.stop()
        if out.exists() and out.stat().st_size > 44:
            return out
    except Exception:
        pass
    return _silent(text, out_base)


# ---- silent fallback ------------------------------------------------------- #

def _silent(text: str, out_base: Path) -> Path:
    out = out_base.with_suffix(".wav")
    seconds = max(2.0, len(text.split()) / 2.6)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SR)
        wf.writeframes(b"\x00\x00" * int(seconds * _SR))
    return out
