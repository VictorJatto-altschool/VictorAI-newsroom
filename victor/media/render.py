"""Render original media for a post: a square card image, and a short clip from it when ffmpeg exists.

This is the "render" media mode: no third-party footage, nothing to license, plays natively on X.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import DATA_DIR

log = logging.getLogger(__name__)
MEDIA_DIR = DATA_DIR / "media"
SIZE = 1080
BG, CARD, BORDER, TEXT, MUTED, ACCENT = "#000000", "#111111", "#2F3336", "#FFFFFF", "#71767B", "#1D9BF0"
FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
FONT_REGULAR = [
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in (FONT_CANDIDATES if bold else FONT_REGULAR):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def render_card(headline: str, facts: list[str], handle: str, label: str, out_path: Path | None = None) -> Path:
    """1080x1080 dark card: category label, headline, up to three facts, handle. Returns the PNG path."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = out_path or MEDIA_DIR / "card.png"
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    pad = 72
    d.rounded_rectangle((pad // 2, pad // 2, SIZE - pad // 2, SIZE - pad // 2), radius=28, fill=CARD, outline=BORDER, width=2)
    d.text((pad, pad), label.upper(), font=_font(30), fill=ACCENT)
    head_lines = textwrap.wrap(headline, width=24)[:4]
    fact_lines = [textwrap.wrap(f, width=44)[:2] for f in facts[:3]]
    block = len(head_lines) * 78 + 24 + sum(len(fl) * 48 + 10 for fl in fact_lines)
    top, bottom = pad + 70, SIZE - pad - 110
    y = top + max((bottom - top - block) // 2, 0)  # vertically centre the text block
    head_font = _font(64)
    for line in head_lines:
        d.text((pad, y), line, font=head_font, fill=TEXT)
        y += 78
    y += 24
    fact_font = _font(36, bold=False)
    for fl in fact_lines:
        for i, line in enumerate(fl):
            d.text((pad + (0 if i == 0 else 36), y), ("• " if i == 0 else "") + line, font=fact_font, fill=TEXT if i == 0 else MUTED)
            y += 48
        y += 10
        if y > bottom:
            break
    d.line((pad, SIZE - pad - 70, SIZE - pad, SIZE - pad - 70), fill=BORDER, width=2)
    d.text((pad, SIZE - pad - 50), handle, font=_font(32), fill=MUTED)
    img.save(out_path, "PNG")
    return out_path


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def render_clip(card_png: Path, seconds: int = 8, out_path: Path | None = None) -> Path | None:
    """Slow push-in over the card, H.264 mp4 that X accepts. Returns None if ffmpeg is missing or fails."""
    if not ffmpeg_available():
        return None
    out_path = out_path or card_png.with_suffix(".mp4")
    frames = seconds * 30
    vf = (f"scale=2160:2160,zoompan=z='min(zoom+0.0008,1.12)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1080:fps=30,"
          f"format=yuv420p")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(card_png), "-vf", vf, "-t", str(seconds),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart", str(out_path)]
    try:
        subprocess.run(cmd, check=True, timeout=120, capture_output=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        log.warning("ffmpeg failed: %s", getattr(e, "stderr", b"")[:300] if hasattr(e, "stderr") else e)
        return None
    return out_path


def facts_from_text(text: str) -> list[str]:
    """Turn the post body into card bullet points: skip the hook line, keep short factual lines."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    body = lines[1:] if len(lines) > 1 else lines
    return [ln for ln in body if not ln.lower().startswith(("why it matters", "for you", "source"))][:3]
