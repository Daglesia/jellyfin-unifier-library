# ── pipeline.py — chain rename + font + audio + video: one input, one output ──
#
# Called by main.py, or directly:
#   python pipeline.py <file_or_folder> --show "Series Name" [output_dir]
#                      [--season N] [--steps font,audio,video]
#                      [--reencode] [--normalize]
#
# Two phases:
#   1. ANALYSIS   - inspect every file, ask every question up front
#                   (which English/Polish subtitles to keep, missing audio languages)
#   2. PROCESSING - run font -> audio -> video on every file with no prompts
#
# Only the final file is written to output_dir, named "Series Name S01E01.mkv".
# If --show is omitted, the original filename is kept.

import json
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import audio
import font
import rename
import video
from config import find_ffmpeg, find_ffprobe

ALL_STEPS = ("font", "audio", "video")
VIDEO_EXTS = video.VIDEO_EXTS


# ── Argument parsing ─────────────────────────────────────────────────────────

def parse_args(args: list[str]) -> dict:
    opts = {
        "show": None, "season": 1, "steps": list(ALL_STEPS),
        "reencode": False, "normalize": False, "positional": [],
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--show", "-n") and i + 1 < len(args):
            opts["show"] = args[i + 1]; i += 2
        elif a == "--season" and i + 1 < len(args):
            opts["season"] = int(args[i + 1]); i += 2
        elif a == "--steps" and i + 1 < len(args):
            opts["steps"] = [s.strip().lower() for s in args[i + 1].split(",") if s.strip()]
            i += 2
        elif a == "--reencode":
            opts["reencode"] = True; i += 1
        elif a == "--normalize":
            opts["normalize"] = True; i += 1
        else:
            opts["positional"].append(a); i += 1
    return opts


# ── Setup helpers ────────────────────────────────────────────────────────────

def build_font_context() -> dict:
    """Resolve mkvmerge/mkvextract and the font file once, up front."""
    font_asset = Path(font.FONT_FILE)
    if not font_asset.is_absolute() and not font_asset.exists():
        candidate = Path(__file__).parent / font_asset
        if candidate.exists():
            font_asset = candidate
    if not font_asset.exists():
        print(f"\n✗ Font file not found: {font.FONT_FILE}  (set FONT_FILE in config.py)")
        sys.exit(1)

    return {
        "mkvmerge":   font.find_binary("mkvmerge"),
        "mkvextract": font.find_binary("mkvextract"),
        "font_file":  font_asset,
        "font_name":  font.FONT_NAME or font.get_font_name(font_asset),
        "track_name": font.SUBTITLE_TRACK_NAME,
    }


def final_stem(src: Path, show: str | None, season: int) -> str:
    """Jellyfin-style name without extension, or the original stem as fallback."""
    if not show:
        return src.stem
    s, e = rename.extract_season_episode(src.stem, season)
    if e is None:
        print(f"  ⚠ {src.name}: couldn't find an episode number — keeping original name")
        return src.stem
    return rename.build_name(show, s, e, "")


def only_output(stage_dir: Path) -> Path | None:
    files = [f for f in stage_dir.iterdir() if f.suffix.lower() == ".mkv"]
    return files[0] if files else None


# ── Phase 1: analysis (all questions happen here) ────────────────────────────

def read_subtitle_tracks(mkvmerge: str, path: Path) -> list[dict]:
    """Same track list font.process_mkv builds, so choices map 1:1."""
    result = subprocess.run(
        [mkvmerge, "-J", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode not in (0, 1):
        return []
    tracks = []
    for t in json.loads(result.stdout).get("tracks", []):
        if t.get("type") != "subtitles":
            continue
        props = t.get("properties", {})
        tracks.append({
            "id":       t.get("id"),
            "codec":    t.get("codec", "unknown"),
            "default":  "1" if props.get("default_track") else "0",
            "forced":   "1" if props.get("forced_track") else "0",
            "language": props.get("language_ietf") or props.get("language", ""),
            "title":    props.get("track_name", ""),
        })
    return tracks


def analyze_file(src: Path, opts: dict, ctx: dict | None, ffprobe: str) -> dict:
    """Ask every question this file will need and return the answers."""
    decisions: dict = {}

    # Font step: which English/Polish subtitle tracks to keep
    # (asks only when a language has more than one track)
    if "font" in opts["steps"] and src.suffix.lower() == ".mkv":
        sub_tracks = read_subtitle_tracks(ctx["mkvmerge"], src)
        if sub_tracks:
            chosen = font.select_subtitles(sub_tracks)
            decisions["sub_ids"] = [t["id"] for _, t in chosen]

    # Audio step: language for tracks with no tag (asks only if untagged + multiple)
    if "audio" in opts["steps"]:
        tracks = audio.get_audio_tracks(ffprobe, src)
        if tracks:
            decisions["audio_args"] = audio.build_title_args(tracks)

    return decisions


@contextmanager
def apply_decisions(decisions: dict):
    """
    Swap the interactive helpers in font.py / audio.py for lookups of the
    answers collected during analysis, so processing never prompts.
    """
    orig_select = font.select_subtitles
    orig_titles = audio.build_title_args

    def select(tracks):
        ids = decisions.get("sub_ids")
        if ids is None:
            return orig_select(tracks)          # nothing recorded -> normal behaviour

        picked = []
        for label, tags in font.KEEP_LANGS:     # keep English-first ordering
            for t in tracks:
                if t["id"] in ids and font.lang_base(t["language"]) in tags:
                    picked.append((label, t))
                    break
        return picked                           # [] = no subs, same as analysis

    def titles(tracks):
        args = decisions.get("audio_args")
        return args if args is not None else orig_titles(tracks)

    font.select_subtitles = select
    audio.build_title_args = titles
    try:
        yield
    finally:
        font.select_subtitles = orig_select
        audio.build_title_args = orig_titles


# ── Phase 2: processing (no prompts) ─────────────────────────────────────────

def process_file(plan: dict, out_dir: Path, opts: dict, ctx: dict | None,
                 ffmpeg: str, ffprobe: str) -> None:
    src: Path = plan["src"]
    stem: str = plan["stem"]
    current = src

    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".work_") as tmp_name, \
         apply_decisions(plan["decisions"]):
        tmp = Path(tmp_name)

        for step in opts["steps"]:
            stage = tmp / step
            stage.mkdir()
            print(f"  ── step: {step}")

            if step == "font":
                if current.suffix.lower() != ".mkv":
                    print("  ↷ Font step needs an MKV — skipping\n")
                    continue
                font.process_mkv(current, stage, ctx["font_name"], ctx["font_file"],
                                 ctx["mkvmerge"], ctx["mkvextract"], ctx["track_name"])
            elif step == "audio":
                audio.process_mkv(current, stage, ffmpeg, ffprobe,
                                  opts["reencode"], opts["normalize"])
            elif step == "video":
                video.convert(current, stage)

            result = only_output(stage)
            if result is None:
                print(f"  ↷ '{step}' produced no output — carrying previous file forward\n")
                continue

            # Free disk space: drop the previous intermediate (never the source)
            if current != src:
                current.unlink(missing_ok=True)
            current = result

        dest = out_dir / f"{stem}{current.suffix.lower()}"
        if current == src:
            shutil.copy2(current, dest)
        else:
            shutil.move(str(current), dest)

    in_mb  = src.stat().st_size  / 1024 / 1024
    out_mb = dest.stat().st_size / 1024 / 1024
    print(f"  ✓ {src.name}  →  {dest.name}  ({in_mb:.1f} MB → {out_mb:.1f} MB)\n")


# ── Entry point ──────────────────────────────────────────────────────────────

def run(args: list[str]) -> None:
    opts = parse_args(args)

    if not opts["positional"]:
        print('Usage: python pipeline.py <file_or_folder> --show "Series Name" [output_dir]')
        print("                          [--season N] [--steps font,audio,video]")
        print("                          [--reencode] [--normalize]")
        sys.exit(0)

    bad = [s for s in opts["steps"] if s not in ALL_STEPS]
    if bad:
        print(f"  ✗ Unknown step(s): {', '.join(bad)}  (valid: {', '.join(ALL_STEPS)})")
        sys.exit(1)

    input_path = Path(opts["positional"][0])
    output_dir = Path(opts["positional"][1]) if len(opts["positional"]) > 1 else None

    if not input_path.exists():
        print(f"  ✗ Path not found: {input_path.resolve()}")
        sys.exit(1)

    ffmpeg  = find_ffmpeg()
    ffprobe = find_ffprobe()
    ctx = build_font_context() if "font" in opts["steps"] else None

    if input_path.is_dir():
        out_dir = output_dir or (input_path / "final")
        files = [f for f in sorted(input_path.iterdir())
                 if f.is_file() and f.suffix.lower() in VIDEO_EXTS]
    elif input_path.suffix.lower() in VIDEO_EXTS:
        out_dir = output_dir or (input_path.parent / "final")
        files = [input_path]
    else:
        print(f"  ✗ Not a video file or directory: {input_path.resolve()}")
        sys.exit(1)

    if not files:
        print(f"No video files found in: {input_path}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n⛓  Pipeline  ({' → '.join(opts['steps'])})")
    print(f"   Show   : {opts['show'] or '(keep original names)'}")
    print(f"   Audio  : {'two-pass loudnorm' if opts['normalize'] else 'AAC re-encode' if opts['reencode'] else 'stream-copy'}")
    print(f"   Output : {out_dir}\n")

    # ── Phase 1: analysis — every question is asked here ─────────────────────
    print("━━ Phase 1/2 · Analysis (questions) ━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    plans = []
    for f in files:
        stem = final_stem(f, opts["show"], opts["season"])
        if (out_dir / f"{stem}.mkv").exists():
            print(f"▶ {f.name}\n  ↷ Output already exists ({stem}.mkv) — will skip\n")
            continue
        print(f"▶ {f.name}")
        decisions = analyze_file(f, opts, ctx, ffprobe)
        print(f"  → {stem}\n")
        plans.append({"src": f, "stem": stem, "decisions": decisions})

    if not plans:
        print("Nothing to process.")
        return

    # ── Phase 2: processing — no more input needed ───────────────────────────
    print("━━ Phase 2/2 · Processing (hands-off) ━━━━━━━━━━━━━━━━━━━━━━━\n")
    for n, plan in enumerate(plans, 1):
        print(f"[{n}/{len(plans)}] {plan['src'].name}")
        process_file(plan, out_dir, opts, ctx, ffmpeg, ffprobe)

    print(f"Done. Processed: {len(plans)}")


if __name__ == "__main__":
    run(sys.argv[1:])