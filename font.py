# ── font.py ──────────────────────────────────────────────────────────────────
# Change the primary subtitle font in MKV or standalone .srt/.ass files using MKVToolNix
#
# Subtitle retention rules for MKV files:
#   English only        -> keep English
#   English + Polish    -> keep both (English first / default)
#   Polish only         -> keep Polish
#   Neither             -> no subtitles in the output

import re
import subprocess
import sys
import shutil
import tempfile
import json
from pathlib import Path
from collections import Counter

# Safe configuration defaults
FONT_FILE = "sub_font.ttf"
FONT_NAME = None
SUBTITLE_TRACK_NAME = None
SUBTITLE_TRACK_NAMES = {}   # optional per-language names, e.g. {"English": "...", "Polish": "..."}

# Import defaults individually to prevent a partial config from breaking the script
try:
    import config
    FONT_FILE = getattr(config, "FONT_FILE", FONT_FILE)
    FONT_NAME = getattr(config, "FONT_NAME", FONT_NAME)
    SUBTITLE_TRACK_NAME = getattr(config, "SUBTITLE_TRACK_NAME", SUBTITLE_TRACK_NAME)
    SUBTITLE_TRACK_NAMES = getattr(config, "SUBTITLE_TRACK_NAMES", SUBTITLE_TRACK_NAMES)
except ImportError:
    pass  # Fall back smoothly to defaults if config.py is completely missing

# ── Top-level constants ───────────────────────────────────────────────────────
SUB_EXTS = {".srt", ".ass", ".ssa"}
MKV_EXT = ".mkv"

# MKVMerge identification strings for text-based subtitle systems
TEXT_CODECS = {"substationalpha", "subrip/srt", "webvtt", "text/utf8"}

# Languages to keep, in output order (the first one found becomes the default track).
# Each entry: (display label, accepted language tags)
KEEP_LANGS = [
    ("English", {"eng", "en"}),
    ("Polish",  {"pol", "pl"}),
]


# ── Binary Finders (MKVToolNix) ──────────────────────────────────────────────

def find_binary(name: str) -> str:
    """Locate mkvmerge or mkvextract in system PATH or common installation slots."""
    binary = shutil.which(name)
    if binary:
        return binary

    # Common local/OS paths if not exposed to global environments
    common_paths = [
        r"C:\Program Files\MKVToolNix",
        r"C:\Program Files (x86)\MKVToolNix",
        "/usr/bin",
        "/usr/local/bin",
        "/opt/homebrew/bin"
    ]
    suffix = ".exe" if sys.platform == "win32" else ""
    for p in common_paths:
        chk = Path(p) / f"{name}{suffix}"
        if chk.exists():
            return str(chk)

    print(f"✗ Dependency Missing: Could not find '{name}' binary.")
    print("  Please install MKVToolNix and add it to your system PATH environment.")
    sys.exit(1)


# ── Font metadata inspection ─────────────────────────────────────────────────

def get_font_name(font_file: Path) -> str:
    """Read the layout engine font family name from nameID 1 table inside the binary."""
    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(font_file)
        candidates = [r for r in tt["name"].names if r.nameID == 1]
        windows = [r for r in candidates if r.platformID == 3]
        if windows:
            en_us = [r for r in windows if r.langID == 0x409]
            return (en_us[0] if en_us else windows[0]).toUnicode()
        if candidates:
            return candidates[0].toUnicode()
    except Exception:
        pass

    # Fallback parsing strategy using file stem structure adjustments
    stem = font_file.stem
    stem = re.sub(
        r"[-_](Regular|Bold|Italic|Light|Medium|Thin|Black|SemiBold|ExtraBold).*$",
        "", stem, flags=re.IGNORECASE,
    )
    return stem


# ── Advanced SubStation Alpha (ASS) Advanced Font Scanners ───────────────────

def find_most_used_font(ass_path: Path) -> str | None:
    """
    Scans the ASS style layout rules and lines to map out font allocations,
    tallying usage weight to safely spot the absolute dominant dialogue font.
    """
    content = ass_path.read_text(encoding="utf-8-sig", errors="replace")
    styles = {}        # style_name -> font_name
    style_counts = {}  # font_name -> cumulative text line assignments
    style_format = []

    in_styles = False
    in_events = False

    for line in content.splitlines():
        line_strip = line.strip()
        if not line_strip:
            continue

        normalized = line_strip.lower()
        if normalized == "[v4+ styles]":
            in_styles = True; in_events = False; continue
        elif normalized == "[events]":
            in_events = True; in_styles = False; continue
        elif normalized.startswith("[") and normalized.endswith("]"):
            in_styles = False; in_events = False; continue

        if in_styles:
            if line_strip.startswith("Format:"):
                style_format = [f.strip().lower() for f in line_strip.split(":", 1)[1].split(",")]
            elif line_strip.startswith("Style:"):
                if not style_format:
                    continue
                parts = [p.strip() for p in line_strip.split(":", 1)[1].split(",")]
                try:
                    name_idx = style_format.index("name")
                    font_idx = style_format.index("fontname")
                    if name_idx < len(parts) and font_idx < len(parts):
                        styles[parts[name_idx]] = parts[font_idx]
                except ValueError:
                    continue

        elif in_events:
            if line_strip.startswith("Dialogue:"):
                parts = line_strip.split(",", 9)
                if len(parts) >= 10:
                    style_name = parts[3].strip()
                    text_field = parts[9]

                    # Target default font referenced directly via script style configurations
                    base_font = styles.get(style_name)

                    # Capture tag style adjustments manually added inline inside brackets (\fnFontName)
                    inline_overrides = re.findall(r"\\fn([^}\\]+)", text_field)
                    if inline_overrides:
                        for font in inline_overrides:
                            f_clean = font.strip()
                            style_counts[f_clean] = style_counts.get(f_clean, 0) + 1
                    elif base_font:
                        style_counts[base_font] = style_counts.get(base_font, 0) + 1

    if style_counts:
        return max(style_counts, key=style_counts.get)
    if styles:
        return Counter(styles.values()).most_common(1)[0][0]
    return None


def replace_ass_font(ass_path: Path, font_name: str) -> str:
    """Swaps out only the single most heavily used font name within the script target."""
    old_font = find_most_used_font(ass_path)
    content = ass_path.read_text(encoding="utf-8-sig", errors="replace")

    if not old_font:
        print(f"  ⚠ Could not isolate a dominant font template. Skipping modifications.")
        return content

    print(f"  Targeted Replacement: Changing '{old_font}' → '{font_name}' (Other styles left intact)")

    lines = content.splitlines()
    style_format = []
    out_lines = []
    in_styles = False

    for line in lines:
        line_strip = line.strip()
        if line_strip.lower() == "[v4+ styles]":
            in_styles = True
            out_lines.append(line)
            continue
        elif line_strip.startswith("[") and line_strip.endswith("]"):
            in_styles = False
            out_lines.append(line)
            continue

        if in_styles:
            if line_strip.startswith("Format:"):
                style_format = [f.strip().lower() for f in line_strip.split(":", 1)[1].split(",")]
                out_lines.append(line)
            elif line_strip.startswith("Style:"):
                if style_format:
                    prefix, rest = line.split(":", 1)
                    parts = [p.strip() for p in rest.split(",")]
                    try:
                        font_idx = style_format.index("fontname")
                        if font_idx < len(parts) and parts[font_idx].lower() == old_font.lower():
                            parts[font_idx] = font_name
                            out_lines.append(f"{prefix}: " + ",".join(parts))
                        else:
                            out_lines.append(line)
                    except ValueError:
                        out_lines.append(line)
                else:
                    out_lines.append(line)
            else:
                out_lines.append(line)
        else:
            # Inline execution sweep matching specifically text instances matching old target layout structures
            escaped_old = re.escape(old_font)
            modified_line = re.sub(r"\\fn" + escaped_old + r"(?=[}\\])", lambda m: f"\\fn{font_name}", line, flags=re.IGNORECASE)
            out_lines.append(modified_line)

    return "\n".join(out_lines) + "\n"


# ── HTML -> ASS String Conversion Helpers ────────────────────────────────────

def html_to_ass(text: str) -> str:
    """Converts basic web formatting rules into structural ASS rendering markers."""
    for html_tag, ass_code in [
        ("<i>", "{\\i1}"), ("</i>", "{\\i0}"),
        ("<b>", "{\\b1}"), ("</b>", "{\\b0}"),
        ("<u>", "{\\u1}"), ("</u>", "{\\u0}"),
        ("<s>", "{\\s1}"), ("</s>", "{\\s0}"),
    ]:
        text = text.replace(html_tag, ass_code).replace(html_tag.upper(), ass_code)
    return re.sub(r"<[^>]+>", "", text)


def srt_to_ass(srt_path: Path, font_name: str) -> str:
    """Transforms structural SubRip data structures natively into ASS tables."""
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,24,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def to_ass_time(t: str) -> str:
        t = t.replace(",", ".")
        h, m, rest = t.split(":")
        s, ms = rest.split(".")
        cs = min(round(int(ms) / 10), 99)
        return f"{int(h)}:{m}:{s}.{cs:02d}"

    lines_out = []
    blocks = re.split(r"\n\s*\n", srt_path.read_text(encoding="utf-8-sig", errors="replace").strip())
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            start, end = re.split(r"\s*-->\s*", lines[1])
            text = r"\N".join(lines[2:])
            text = html_to_ass(text)
            lines_out.append(f"Dialogue: 0,{to_ass_time(start.strip())},{to_ass_time(end.strip())},Default,,0,0,0,,{text}")
        except Exception:
            continue
    return header + "\n".join(lines_out) + "\n"


# ── Subtitle selection (English and/or Polish) ───────────────────────────────

def lang_base(code: str) -> str:
    """'en-US' -> 'en', 'POL' -> 'pol'."""
    return (code or "").lower().split("-")[0]


def select_subtitles(sub_tracks: list[dict]) -> list[tuple[str, dict]]:
    """
    Return [(label, track), ...] for the languages in KEEP_LANGS that exist
    in the file, in KEEP_LANGS order. Prompts when a language has more than
    one track.
    """
    chosen = []
    for label, tags in KEEP_LANGS:
        matches = [t for t in sub_tracks if lang_base(t["language"]) in tags]
        if not matches:
            continue
        if len(matches) == 1:
            chosen.append((label, matches[0]))
            continue

        print(f"  Multiple {label} subtitle tracks found:")
        for i, t in enumerate(matches, 1):
            print(f"    {i}. id={t['id']}  codec={t['codec']}  title={t['title'] or '?'}")
        while True:
            choice = input(f"  Which {label} one to keep [1-{len(matches)}]: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(matches):
                chosen.append((label, matches[int(choice) - 1]))
                break
            print("  Invalid choice, try again.")
    return chosen


# ── Backward-compatibility shims (for pipeline.py and other callers) ─────────

ENGLISH_TAGS = KEEP_LANGS[0][1]


def select_english_subtitle(sub_tracks: list[dict]) -> dict | None:
    """
    Legacy single-track API: returns the first kept track (English if present,
    else Polish), or None. Callers that can handle several tracks should use
    select_subtitles() instead so Polish is kept alongside English.
    """
    chosen = select_subtitles(sub_tracks)
    return chosen[0][1] if chosen else None


def track_name_override_for(label: str, global_override: str | None) -> str | None:
    """Per-language name wins, then the global SUBTITLE_TRACK_NAME, else None."""
    return SUBTITLE_TRACK_NAMES.get(label) or global_override


# ── Main processing endpoints ───────────────────────────────────────────────

def process_subtitle(sub_path: Path, output_dir: Path, font_name: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{sub_path.stem}_new_font.ass"
    ext = sub_path.suffix.lower()

    if ext == ".srt":
        print(f"  SRT → ASS Conversion (font: {font_name})")
        content = srt_to_ass(sub_path, font_name)
    elif ext in (".ass", ".ssa"):
        print(f"  Parsing font mapping configuration patterns...")
        content = replace_ass_font(sub_path, font_name)
    else:
        raise ValueError(f"Format profile unsupported: {ext}")

    out.write_text(content, encoding="utf-8")
    print(f"  ✓ Written: {out.name}\n")
    return out


def process_mkv(mkv_path: Path, output_dir: Path, font_name: str, font_file: Path,
                mkvmerge: str, mkvextract: str, track_name_override: str | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{mkv_path.stem}_new_font.mkv"

    if out.exists():
        print(f"  Skipping (Output file already initialized): {out.name}\n")
        return

    # Request track manifest extraction using json data maps
    result = subprocess.run(
        [mkvmerge, "-J", str(mkv_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print(f"  ✗ Identification failure on source target asset.")
        return

    mkv_info = json.loads(result.stdout)
    tracks = mkv_info.get("tracks", [])

    sub_tracks = []
    for t in tracks:
        if t.get("type") == "subtitles":
            props = t.get("properties", {})
            language = props.get("language_ietf") or props.get("language", "")
            sub_tracks.append({
                "id": t.get("id"),
                "codec": t.get("codec", "unknown"),
                "default": "1" if props.get("default_track") else "0",
                "forced": "1" if props.get("forced_track") else "0",
                "language": language,
                "title": props.get("track_name", ""),
            })

    if not sub_tracks:
        print(f"  ⚠ No subtitle tracks found — copying container as-is.\n")
        shutil.copy2(mkv_path, out)
        return

    print(f"  Discovered {len(sub_tracks)} subtitle track(s):")
    for st in sub_tracks:
        print(f"    [{st['id']}] {st['codec']:22s} lang={st['language'] or '?'} title={st['title'] or '?'}")

    mime_type = "font/otf" if font_file.suffix.lower() == ".otf" else "font/ttf"
    font_args = [
        "--attachment-mime-type", mime_type,
        "--attachment-name", font_file.name,
        "--attach-file", str(font_file),
    ]

    def finish(cmd: list[str]) -> None:
        print(f"  Remuxing via mkvmerge...")
        status = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        # mkvmerge exit code 1 = finished with non-fatal warnings
        if status.returncode in (0, 1) and out.exists():
            src_mb = mkv_path.stat().st_size / 1024 / 1024
            out_mb = out.stat().st_size / 1024 / 1024
            print(f"  ✓ Process Complete: {out.name} ({src_mb:.1f} MB → {out_mb:.1f} MB)\n")
        else:
            print(f"  ✗ Process Interrupted! Error Log details:\n{status.stdout[-2000:]}\n")
            out.unlink(missing_ok=True)

    # ── Pick English and/or Polish ────────────────────────────────────────────
    chosen = select_subtitles(sub_tracks)

    if not chosen:
        print(f"  ⚠ No English or Polish subtitle track found — output will have no subtitles.")
        finish([mkvmerge, "-o", str(out), "-S", str(mkv_path)] + font_args)
        return

    kept_labels = " + ".join(label for label, _ in chosen)
    dropped = len(sub_tracks) - len(chosen)
    print(f"  Keeping: {kept_labels} — dropping {dropped} other track(s)")

    # First kept track (English if present, else Polish) is the default
    default_id = chosen[0][1]["id"]

    with tempfile.TemporaryDirectory() as temp_dir:
        tmp = Path(temp_dir)
        rewritten = []        # (label, track_meta, rewritten_ass_path)
        passthrough = []      # (label, track_meta): image-based or failed extractions, keep original

        for label, track in chosen:
            codec_lower = track["codec"].lower()

            if codec_lower not in TEXT_CODECS:
                print(f"  ⚠ {label} track [{track['id']}] is image-based ({track['codec']}) — keeping as-is.")
                passthrough.append((label, track))
                continue

            src_ext = ".ass" if "substationalpha" in codec_lower else ".srt"
            raw = tmp / f"track_{track['id']}{src_ext}"

            print(f"  Extracting {label} subtitle track [{track['id']}]...")
            subprocess.run(
                [mkvextract, "tracks", str(mkv_path), f"{track['id']}:{raw}"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )

            if raw.exists() and raw.stat().st_size > 0:
                content = (srt_to_ass(raw, font_name) if src_ext == ".srt"
                           else replace_ass_font(raw, font_name))
                new_file = tmp / f"track_{track['id']}_modified.ass"
                new_file.write_text(content, encoding="utf-8")
                rewritten.append((label, track, new_file))
            else:
                print(f"    ⚠ Extraction empty — keeping original {label} track unmodified.")
                passthrough.append((label, track))

        # ── Build mkvmerge command ────────────────────────────────────────────
        cmd = [mkvmerge, "-o", str(out)]

        # Source file: keep only the passthrough subtitle tracks (or none at all)
        if passthrough:
            cmd += ["-s", ",".join(str(t["id"]) for _, t in passthrough)]
            for _, t in passthrough:
                cmd += ["--default-track-flag", f"{t['id']}:{'yes' if t['id'] == default_id else 'no'}"]
        else:
            cmd += ["-S"]
        cmd += [str(mkv_path)]

        # Rewritten tracks, added as separate inputs
        for label, track, file_path in rewritten:
            if track["language"]:
                cmd += ["--language", f"0:{track['language']}"]

            name = (
                track_name_override_for(label, track_name_override)
                or track["title"]
                or f"{label} Subtitles"
            )
            cmd += ["--track-name", f"0:{name}"]
            cmd += ["--default-track-flag", f"0:{'yes' if track['id'] == default_id else 'no'}"]
            cmd += ["--forced-display-flag", f"0:{'yes' if track['forced'] == '1' else 'no'}"]
            cmd += [str(file_path)]

        # Attach custom target design font configuration file
        cmd += font_args
        finish(cmd)


# ── Runtime Wrapper Configuration entrypoint ─────────────────────────────────

def run(args: list[str]) -> None:
    if not args:
        print("Usage pattern validation error: python font.py <file_or_folder> [output_dir]")
        sys.exit(0)

    mkvmerge_bin = find_binary("mkvmerge")
    mkvextract_bin = find_binary("mkvextract")

    # If not found in the current working directory, look inside the script's folder
    font_asset = Path(FONT_FILE)
    if not font_asset.is_absolute() and not font_asset.exists():
        script_relative = Path(__file__).parent / font_asset
        if script_relative.exists():
            font_asset = script_relative

    if not font_asset.exists():
        print(f"\n✗ Asset Configuration Error: Target path configuration absent: {FONT_FILE}")
        print("  Update 'FONT_FILE' definition metrics inside your configuration script to mount font asset profiles.")
        sys.exit(1)

    resolved_font_name = FONT_NAME if FONT_NAME else get_font_name(font_asset)

    print(f"\n🔤 MKVToolNix Dynamic Font Swapper Engine Setup")
    print(f"   Target Font File  : {font_asset.name}")
    print(f"   Resolved Name Key : {resolved_font_name}")
    print(f"   Keep Languages    : {', '.join(label for label, _ in KEEP_LANGS)}")
    print(f"   Override Track Name: {repr(SUBTITLE_TRACK_NAME) if SUBTITLE_TRACK_NAME is not None else 'None (using source)'}")
    print(f"   Per-language Names: {SUBTITLE_TRACK_NAMES or 'None'}")
    print(f"   mkvmerge Binary   : {mkvmerge_bin}")
    print(f"   mkvextract Binary : {mkvextract_bin}\n")

    input_path = Path(args[0])
    output_directory = Path(args[1]) if len(args) > 1 else None

    if input_path.is_dir():
        out_target = output_directory or (input_path / "font_changed")
        files = (
            list(input_path.glob("*.mkv")) +
            list(input_path.glob("*.srt")) +
            list(input_path.glob("*.ass")) +
            list(input_path.glob("*.ssa"))
        )
        if not files:
            print(f"Data discovery validation failed: empty of matched target types at: {input_path}")
            return
        print(f"Located {len(files)} operational targets for deployment.\n")
        for f in sorted(files):
            print(f"▶ Processing Target Asset: {f.name}")
            if f.suffix.lower() == MKV_EXT:
                process_mkv(f, out_target, resolved_font_name, font_asset, mkvmerge_bin, mkvextract_bin, SUBTITLE_TRACK_NAME)
            else:
                process_subtitle(f, out_target, resolved_font_name)

    elif input_path.suffix.lower() == MKV_EXT:
        out_target = output_directory or (input_path.parent / "font_changed")
        print(f"▶ Processing Target Asset: {input_path.name}")
        process_mkv(input_path, out_target, resolved_font_name, font_asset, mkvmerge_bin, mkvextract_bin, SUBTITLE_TRACK_NAME)

    elif input_path.suffix.lower() in SUB_EXTS:
        out_target = output_directory or (input_path.parent / "font_changed")
        print(f"▶ Processing Target Asset: {input_path.name}")
        process_subtitle(input_path, out_target, resolved_font_name)

    else:
        print(f"Unsupported file format exception encountered on type extension profile: {input_path.suffix}")
        sys.exit(1)


if __name__ == "__main__":
    run(sys.argv[1:])