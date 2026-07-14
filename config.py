# ── config.py — shared settings, imported by all modules ────────────────────
# Edit the values in this file only; nothing else needs to change.

import shutil
from pathlib import Path

# Paths to ffmpeg/ffprobe executables
FFMPEG_PATH  = r""
FFPROBE_PATH = r""

# Full path to your .otf or .ttf font file (used by the font utility)
FONT_FILE = "./Rosario-Regular.ttf"

SUBTITLE_TRACK_NAME = "Daglesia's Subuwutitles"
# SUBTITLE_TRACK_NAME = "[DagSubs] Polish"

# Video quality: CRF for libx265 (18=near-lossless, 22=great, 28=small)
VIDEO_CRF = 22

# Video encode speed (ultrafast/fast/medium/slow/veryslow)
# slower = better compression, same quality
VIDEO_PRESET = "slow"


# ── Tool finder (used by all utilities) ─────────────────────────────────────

def find_ffmpeg() -> str:
    return _find("ffmpeg", FFMPEG_PATH)

def find_ffprobe() -> str:
    return _find("ffprobe", FFPROBE_PATH)

def _find(name: str, hardcoded: str) -> str:
    path = shutil.which(name)
    if path:
        return path
    if Path(hardcoded).exists():
        return hardcoded
    raise FileNotFoundError(
        f"\nCannot find {name}.exe!\n"
        f"Expected at: {hardcoded}\n"
        f"Edit {name.upper()}_PATH in config.py, or add {name} to your system PATH.\n"
        f'PowerShell search: Get-ChildItem C:\\ -Recurse -Filter "{name}.exe" '
        f"-ErrorAction SilentlyContinue"
    )