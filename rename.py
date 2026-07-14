# ── rename.py — rename episode files to Jellyfin convention ──────────────────
#
#   Series Name S01E01.mkv
#
# Called by main.py, or directly:
#   python rename.py input.mkv     --show "Gino"
#   python rename.py C:\Downloads  --show "SONIa" [output_dir] [--season 2]
#
# Show name is passed as a flag (--show / -n) — nothing hardcoded, nothing
# guessed from the messy source filename.

import re
import shutil
import sys
from pathlib import Path

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts", ".wmv"}

# Explicit "S01E01" / "S01 E01" / "s1e1" anywhere in the name
SEASON_EP_RE = re.compile(r"[Ss](\d{1,2})\s*[Ee](\d{1,3})")

# Fallback: a bare episode number after a " - ", optionally "11v2"-style
# versioned, immediately followed by "(", "[" or end of name.
# e.g. "... - 11v2 (720p) ..." -> episode 11
BARE_EP_RE = re.compile(r"-\s*(\d{1,3})(?:v\d+)?\s*(?:\(|\[|$)")


def extract_season_episode(name: str, default_season: int) -> tuple[int, int] | tuple[None, None]:
    m = SEASON_EP_RE.search(name)
    if m:
        return int(m.group(1)), int(m.group(2))

    m = BARE_EP_RE.search(name)
    if m:
        return default_season, int(m.group(1))

    return None, None


def build_name(show: str, season: int, episode: int, ext: str) -> str:
    return f"{show} S{season:02d}E{episode:02d}{ext}"


def rename_one(path: Path, show: str, default_season: int, output_dir: Path | None) -> None:
    season, episode = extract_season_episode(path.stem, default_season)

    if episode is None:
        print(f"  ⚠  Couldn't find an episode number: {path.name}")
        return

    new_name = build_name(show, season, episode, path.suffix.lower())

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        dest = output_dir / new_name
        if dest.exists():
            print(f"  ↷ Skipping (output exists): {dest.name}")
            return
        shutil.copy2(path, dest)
        print(f"  ✓ {path.name}  →  {dest.name}  (copied)")
    else:
        dest = path.with_name(new_name)
        if dest.exists() and dest != path:
            print(f"  ↷ Skipping (target exists): {dest.name}")
            return
        path.rename(dest)
        print(f"  ✓ {path.name}  →  {dest.name}")


def parse_args(args: list[str]) -> tuple[list[str], str | None, int]:
    """Manual flag parsing: --show/-n NAME, --season N, everything else positional."""
    show: str | None = None
    season = 1
    positional: list[str] = []

    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--show", "-n") and i + 1 < len(args):
            show = args[i + 1]
            i += 2
        elif a == "--season" and i + 1 < len(args):
            season = int(args[i + 1])
            i += 2
        else:
            positional.append(a)
            i += 1

    return positional, show, season


def run(args: list[str]) -> None:
    if not args:
        print("Usage: python rename.py <file_or_folder> --show \"Series Name\" [output_dir] [--season N]")
        print()
        print("  --show / -n   Series name to rename into (required)")
        print("  --season      Default season number when none is found in the filename (default: 1)")
        sys.exit(0)

    positional, show, season = parse_args(args)

    if not show:
        print("  ✗ Missing required --show \"Series Name\" flag.")
        sys.exit(1)

    if not positional:
        print("  ✗ Missing input file or folder.")
        sys.exit(1)

    input_path = Path(positional[0])
    output_dir = Path(positional[1]) if len(positional) > 1 else None

    print(f"\n🏷  Renamer  (show: \"{show}\", default season: {season:02d})\n")

    if not input_path.exists():
        print(f"  ✗ Path not found: {input_path.resolve()}")
        sys.exit(1)

    if input_path.is_dir():
        files = [f for f in sorted(input_path.iterdir())
                 if f.suffix.lower() in VIDEO_EXTS]
        if not files:
            print(f"No video files found in: {input_path}")
            return
        print(f"Found {len(files)} video(s)\n")
        for f in files:
            rename_one(f, show, season, output_dir)

    elif input_path.is_file() and input_path.suffix.lower() in VIDEO_EXTS:
        rename_one(input_path, show, season, output_dir)

    else:
        print(f"  ✗ Not a video file or directory: {input_path.resolve()}")
        sys.exit(1)


if __name__ == "__main__":
    run(sys.argv[1:])