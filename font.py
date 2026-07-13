# ── font.py — change subtitle font in MKV or standalone .srt/.ass files ─────

import re
import subprocess
import sys
import shutil
import tempfile
from pathlib import Path

from config import find_ffmpeg, find_ffprobe, FONT_FILE

# ── Top-level constants ───────────────────────────────────────────────────────
# Set FONT_NAME to the exact family name subtitle renderers will look up.
# This must match nameID 1 inside the font file (e.g. "Noto Sans", "Arial").
# If left as None, it is derived automatically from the font binary at startup.
FONT_NAME: str | None = None   # e.g. "Noto Sans" — override here

SUB_EXTS   = {".srt", ".ass", ".ssa"}
MKV_EXT    = ".mkv"
TEXT_CODECS = {"ass", "ssa", "subrip", "srt", "webvtt", "mov_text", "text"}


# ── Font name derivation ─────────────────────────────────────────────────────

def get_font_name(font_file: Path) -> str:
    """
    Read the font family name from the file's internal name table (nameID 1).
    This is the exact name subtitle renderers look up when honouring a
    Fontname= reference — guessing it from the filename is unreliable and
    will cause the embedded font to go unused.

    Falls back to stripping weight suffixes from the filename if fonttools
    is not installed  (pip install fonttools  to get the accurate behaviour).
    """
    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(font_file)
        for record in tt["name"].names:
            if record.nameID == 1:          # Family name record
                return record.toUnicode()
    except Exception:
        pass

    # Fallback: strip weight suffixes from filename
    stem = font_file.stem
    stem = re.sub(
        r"[-_](Regular|Bold|Italic|Light|Medium|Thin|Black|SemiBold|ExtraBold).*$",
        "", stem, flags=re.IGNORECASE,
    )
    return stem


# ── HTML → ASS conversion helper ────────────────────────────────────────────

def html_to_ass(text: str) -> str:
    """
    Convert HTML formatting tags commonly found in SRT files into ASS override
    codes.  Stripping them (as the original code did) silently removes all
    italic and bold formatting from dialogue.

    Any remaining tags (e.g. <font color=...>) that have no ASS equivalent
    are stripped afterwards.
    """
    for html_tag, ass_code in [
        ("<i>",  "{\\i1}"), ("</i>", "{\\i0}"),
        ("<b>",  "{\\b1}"), ("</b>", "{\\b0}"),
        ("<u>",  "{\\u1}"), ("</u>", "{\\u0}"),
        ("<s>",  "{\\s1}"), ("</s>", "{\\s0}"),
    ]:
        # Handle both lower-case (<i>) and upper-case (<I>) variants
        text = text.replace(html_tag, ass_code).replace(html_tag.upper(), ass_code)

    # Strip any remaining HTML tags
    return re.sub(r"<[^>]+>", "", text)


# ── ASS conversion / font replacement ───────────────────────────────────────

def srt_to_ass(srt_path: Path, font_name: str) -> str:
    """Convert SRT → ASS, embedding the given font in the Style header."""
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1280\n"
        "PlayResY: 720\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},52,&H00FFFFFF,&H000000FF,"
        "&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,24,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def to_ass_time(t: str) -> str:
        t = t.replace(",", ".")
        h, m, rest = t.split(":")
        s, ms = rest.split(".")
        # Round to centiseconds — truncating (ms[:2]) loses up to 9 ms per cue
        cs = str(round(int(ms) / 10)).zfill(2)
        return f"{int(h)}:{m}:{s}.{cs}"

    lines_out = []
    for block in re.split(r"\n\s*\n", srt_path.read_text(encoding="utf-8-sig", errors="replace").strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            start, end = re.split(r"\s*-->\s*", lines[1])
            text = r"\N".join(lines[2:])
            text = html_to_ass(text)           # convert, don't strip
            lines_out.append(
                f"Dialogue: 0,{to_ass_time(start.strip())},{to_ass_time(end.strip())},"
                f"Default,,0,0,0,,{text}"
            )
        except Exception:
            continue

    return header + "\n".join(lines_out) + "\n"


def replace_ass_font(ass_path: Path, font_name: str) -> str:
    """
    Replace every Fontname value in an ASS/SSA file.

    Two replacement sites:
      1. Style: lines  —  "Style: Name,OldFont,size,..."
      2. Inline \\fn override tags in Dialogue lines  —  {\\fnOldFont}
         These are per-line overrides that take precedence over the Style block
         and would cause the original font to bleed back in if left unchanged.
    """
    content = ass_path.read_text(encoding="utf-8-sig", errors="replace")

    # 1. Style lines
    content = re.sub(
        r"^(Style:[^,]+),([^,]+),",
        lambda m: f"{m.group(1)},{font_name},",
        content,
        flags=re.MULTILINE,
    )

    # 2. Inline \fn override tags: match \fn followed by anything up to } or \
    content = re.sub(r"\\fn[^}\\]+", f"\\\\fn{font_name}", content)

    return content


# ── Standalone subtitle file ─────────────────────────────────────────────────

def process_subtitle(sub_path: Path, output_dir: Path, font_name: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{sub_path.stem}_new_font.ass"
    ext = sub_path.suffix.lower()

    if ext == ".srt":
        print(f"  SRT → ASS  (font: {font_name})")
        content = srt_to_ass(sub_path, font_name)
    elif ext in (".ass", ".ssa"):
        print(f"  Replacing font in ASS  (font: {font_name})")
        content = replace_ass_font(sub_path, font_name)
    else:
        raise ValueError(f"Unsupported subtitle format: {ext}")

    out.write_text(content, encoding="utf-8")
    print(f"  ✓ {out.name}\n")
    return out


# ── MKV subtitle track helpers ───────────────────────────────────────────────

def get_subtitle_tracks(ffprobe: str, mkv_path: Path) -> list[dict]:
    result = subprocess.run(
        [ffprobe, "-v", "error",
         "-select_streams", "s",
         "-show_entries", "stream=index,codec_name:stream_tags=language,title",
         "-of", "csv=p=0", str(mkv_path)],
        capture_output=True, text=True,
    )
    tracks = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split(",")
        tracks.append({
            "index":    parts[0] if len(parts) > 0 else "?",
            "codec":    parts[1] if len(parts) > 1 else "unknown",
            "language": parts[2] if len(parts) > 2 else "",
            "title":    parts[3] if len(parts) > 3 else "",
        })
    return tracks


def get_attachment_count(ffprobe: str, mkv_path: Path) -> int:
    """Return the number of attachment streams already present in the file."""
    result = subprocess.run(
        [ffprobe, "-v", "error",
         "-select_streams", "t",
         "-show_entries", "stream=index",
         "-of", "csv=p=0", str(mkv_path)],
        capture_output=True, text=True,
    )
    return len([ln for ln in result.stdout.strip().splitlines() if ln.strip()])


# ── MKV processing ───────────────────────────────────────────────────────────

def process_mkv(mkv_path: Path, output_dir: Path, font_name: str, font_file: Path,
                ffmpeg: str, ffprobe: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{mkv_path.stem}_new_font.mkv"

    if out.exists():
        print(f"  ↷ Skipping (output exists): {out.name}\n")
        return

    tracks = get_subtitle_tracks(ffprobe, mkv_path)
    if not tracks:
        print(f"  ⚠ No subtitle tracks found — copying MKV as-is\n")
        shutil.copy2(mkv_path, out)
        return

    print(f"  Found {len(tracks)} subtitle track(s):")
    for t in tracks:
        action = "rewrite" if t["codec"].lower() in TEXT_CODECS else "copy (image-based)"
        print(f"    [{t['index']}] {t['codec']:16s}  "
              f"lang={t['language'] or '?'}  title={t['title'] or '?'}  → {action}")

    with tempfile.TemporaryDirectory() as _tmp:
        tmp = Path(_tmp)

        # processed   : [(track_dict, new_ass_path), ...]  — text tracks rewritten
        # unprocessed : [track_dict, ...]                  — image tracks, failed extracts
        processed:   list[tuple[dict, Path]] = []
        unprocessed: list[dict]              = []

        for track in tracks:
            codec = track["codec"].lower()

            if codec not in TEXT_CODECS:
                # PGS, VOBSUB, etc. — can't rewrite, preserve as-is
                unprocessed.append(track)
                continue

            src_ext = ".ass" if codec in ("ass", "ssa") else ".srt"
            raw     = tmp / f"track_{track['index']}{src_ext}"

            print(f"  Extracting [{track['index']}] ({codec})...")
            subprocess.run(
                [ffmpeg, "-y", "-i", str(mkv_path),
                 "-map", f"0:{track['index']}", "-c:s", "copy", str(raw)],
                capture_output=True, text=True,
            )
            if not raw.exists() or raw.stat().st_size == 0:
                print(f"    ⚠ Could not extract — preserving original")
                unprocessed.append(track)
                continue

            new_ass = tmp / f"track_{track['index']}_new.ass"
            content = (srt_to_ass(raw, font_name) if src_ext == ".srt"
                       else replace_ass_font(raw, font_name))
            new_ass.write_text(content, encoding="utf-8")
            processed.append((track, new_ass))

        if not processed:
            print("  ⚠ No text tracks could be processed\n")
            return

        # ── Build remux command ──────────────────────────────────────────────
        #
        # Input 0        = original MKV
        # Inputs 1..N    = new ASS files, one per processed track, in the
        #                  same order they appear in `processed`
        #
        # Subtitle tracks are mapped in their ORIGINAL order so that default
        # track selection in players is not disturbed.  Image-based tracks
        # (PGS/VOBSUB) and any text track that failed extraction are
        # stream-copied from input 0 instead of being dropped.

        cmd = [ffmpeg, "-y", "-i", str(mkv_path)]
        for _, new_ass in processed:
            cmd += ["-i", str(new_ass)]

        # Video, audio, chapters from original
        cmd += ["-map", "0:v", "-map", "0:a?", "-map_chapters", "0"]

        # Build a lookup: track index → (ffmpeg input index, new_ass path)
        processed_by_idx = {
            t["index"]: (i + 1, p)
            for i, (t, p) in enumerate(processed)
        }

        codec_args: list[str] = []
        meta_args:  list[str] = []
        out_sub_idx = 0

        for track in tracks:
            if track["index"] in processed_by_idx:
                input_num, _ = processed_by_idx[track["index"]]
                cmd        += ["-map", str(input_num)]
                codec_args += [f"-c:s:{out_sub_idx}", "ass"]
                if track["language"]:
                    meta_args += [f"-metadata:s:s:{out_sub_idx}",
                                  f"language={track['language']}"]
                meta_args  += [f"-metadata:s:s:{out_sub_idx}",
                               f"title={(track['title'] or 'Subtitles')} [{font_name}]"]
            else:
                # Image-based or failed extraction — stream-copy from original
                cmd        += ["-map", f"0:{track['index']}"]
                codec_args += [f"-c:s:{out_sub_idx}", "copy"]

            out_sub_idx += 1

        cmd += ["-c:v", "copy", "-c:a", "copy"]
        cmd += codec_args + meta_args

        # Preserve existing attachments from source (e.g. fonts already embedded)
        cmd += ["-map", "0:t?"]

        # Attach the new font.  Its output attachment index follows any that
        # were preserved from the source, so we count those first.
        n_existing = get_attachment_count(ffprobe, mkv_path)
        mime = "font/otf" if font_file.suffix.lower() == ".otf" else "font/ttf"
        cmd += [
            "-attach", str(font_file),
            f"-metadata:s:t:{n_existing}", f"mimetype={mime}",
            f"-metadata:s:t:{n_existing}", f"filename={font_file.name}",
        ]

        cmd += [str(out)]

        print(f"  Remuxing + embedding font '{font_file.name}' (no video/audio re-encode)...")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0 and out.exists():
            in_mb  = mkv_path.stat().st_size / 1024 / 1024
            out_mb = out.stat().st_size       / 1024 / 1024
            print(f"  ✓ {out.name}  ({in_mb:.1f} MB → {out_mb:.1f} MB)\n")
        else:
            print(f"  ✗ Failed!\n{result.stderr[-3000:]}\n")
            out.unlink(missing_ok=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def run(args: list[str]) -> None:
    if not args:
        print("Usage: python font.py <file_or_folder> [output_dir]")
        sys.exit(0)

    ffmpeg  = find_ffmpeg()
    ffprobe = find_ffprobe()

    font_file = Path(FONT_FILE)
    if not font_file.exists():
        print(f"\n✗ Font file not found: {font_file}")
        print("  Edit FONT_FILE in config.py to point to your .otf/.ttf file.")
        sys.exit(1)

    # Use the module-level constant if set; fall back to reading the font binary.
    font_name = FONT_NAME if FONT_NAME else get_font_name(font_file)

    # ... rest of run() unchanged ...

    print(f"\n🔤 Font changer")
    print(f"   Font file : {font_file.name}")
    print(f"   Font name : {font_name}")
    print(f"   ffmpeg    : {ffmpeg}\n")

    input_path = Path(args[0])
    output_dir = Path(args[1]) if len(args) > 1 else None

    if input_path.is_dir():
        out = output_dir or (input_path / "font_changed")
        files = (
            list(input_path.glob("*.mkv")) +
            list(input_path.glob("*.srt")) +
            list(input_path.glob("*.ass")) +
            list(input_path.glob("*.ssa"))
        )
        if not files:
            print(f"No MKV or subtitle files found in: {input_path}")
            return
        print(f"Found {len(files)} file(s)\n")
        for f in sorted(files):
            print(f"▶ {f.name}")
            if f.suffix.lower() == MKV_EXT:
                process_mkv(f, out, font_name, font_file, ffmpeg, ffprobe)
            else:
                process_subtitle(f, out, font_name)

    elif input_path.suffix.lower() == MKV_EXT:
        out = output_dir or (input_path.parent / "font_changed")
        print(f"▶ {input_path.name}")
        process_mkv(input_path, out, font_name, font_file, ffmpeg, ffprobe)

    elif input_path.suffix.lower() in SUB_EXTS:
        out = output_dir or (input_path.parent / "font_changed")
        print(f"▶ {input_path.name}")
        process_subtitle(input_path, out, font_name)

    else:
        print(f"Unsupported file type: {input_path.suffix}")
        sys.exit(1)


if __name__ == "__main__":
    run(sys.argv[1:])