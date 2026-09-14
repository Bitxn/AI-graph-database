# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Stitch slide PNGs + narration audio into an MP4 by calling ffmpeg directly.

This streams through ffmpeg (per-slide clip → concat) instead of loading audio
into numpy like moviepy — so it's fast and doesn't blow up memory on large repos.
Uses the ffmpeg binary bundled by imageio-ffmpeg, so no system install is needed.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def build_video(items: list[tuple[Path, Path]], out_mp4: Path, fps: int = 24, pad: float = 0.7) -> Path:
    """items = [(slide_png, audio_file), …]. Each slide holds for its narration + `pad`."""
    ffmpeg = _ffmpeg_exe()
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="oneport-vid-"))
    clips: list[Path] = []
    try:
        for i, (png, audio) in enumerate(items):
            dur = _audio_duration(ffmpeg, audio) + pad
            clip = tmp / f"clip_{i:02d}.mp4"
            _run([
                ffmpeg, "-y",
                "-loop", "1", "-framerate", str(fps), "-i", str(png),
                "-i", str(audio),
                "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
                "-t", f"{dur:.2f}",
                "-vf", "scale=1280:720",
                str(clip),
            ])
            clips.append(clip)

        list_file = tmp / "concat.txt"
        list_file.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
        _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
              "-c", "copy", str(out_mp4)])
        return out_mp4
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"       # fall back to a system ffmpeg on PATH


def _audio_duration(ffmpeg: str, audio: Path) -> float:
    # ffmpeg prints "Duration: HH:MM:SS.ss" to stderr even with no output file.
    res = subprocess.run([ffmpeg, "-i", str(audio)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", res.stderr)
    if m:
        h, mn, s = m.groups()
        return int(h) * 3600 + int(mn) * 60 + float(s)
    return 5.0


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({cmd[-1]}):\n{res.stderr[-800:]}")
