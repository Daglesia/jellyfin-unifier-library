# ── font.py — change subtitle font in MKV or standalone .srt/.ass files ─────
# Embeds the .otf font inside the MKV so Jellyfin renders it on every client.
# Video and audio are stream-copied — zero re-encoding.
#
# Called by main.py, or directly:
#   python font.py input.mkv [output_dir]
#   python font.py subs.srt  [output_dir]
#   python font.py C:\Movies [output_dir]

import re
import subprocess
import sys
import shutil
import tempfile
from pathlib import Path

from config import find_ffmpeg, find_ffprobe, FONT_FILE

SUB_EXTS = {".srt", ".ass", ".ssa"}
MKV_EXT  = ".mkv"


# ── Font name derivation ─────────────────────────────────────────────────────

def get_font_name(font_file: Path) -> str:
    """Strip weight suffixes from filename to get the family name."""
    stem = font_file.stem
    stem = re.sub(
        r"[-_](Regular|Bold|Italic|Light|Medium|Thin|Black|SemiBold|ExtraBold).*$",
        "", stem, flags=re.IGNORECASE,
    )
    return stem


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
        return f"{int(h)}:{m}:{s}.{ms[:2]}"

    lines_out = []
    for block in re.split(r"\n\s*\n", srt_path.read_text(encoding="utf-8-sig", errors="replace").strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            start, end = re.split(r"\s*-->\s*", lines[1])
            text = r"\N".join(lines[2:])
            text = re.sub(r"<[^>]+>", "", text)
            lines_out.append(
                f"Dialogue: 0,{to_ass_time(start.strip())},{to_ass_time(end.strip())},"
                f"Default,,0,0,0,,{text}"
            )
        except Exception:
            continue

    return header + "\n".join(lines_out) + "\n"


def replace_ass_font(ass_path: Path, font_name: str) -> str:
    """Replace every Fontname value in an ASS/SSA file."""
    return re.sub(
        r"^(Style:[^,]+),([^,]+),",
        lambda m: f"{m.group(1)},{font_name},",
        ass_path.read_text(encoding="utf-8-sig", errors="replace"),
        flags=re.MULTILINE,
    )


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
        print(f"    [{t['index']}] {t['codec']:8s}  "
              f"lang={t['language'] or '?'}  title={t['title'] or '?'}")

    with tempfile.TemporaryDirectory() as _tmp:
        tmp = Path(_tmp)
        processed = []  # (i, new_ass_path, track_dict)

        for i, track in enumerate(tracks):
            codec   = track["codec"].lower()
            src_ext = ".ass" if codec in ("ass", "ssa") else ".srt"
            raw     = tmp / f"track_{i}{src_ext}"

            print(f"  Extracting [{track['index']}] ({codec})...")
            subprocess.run(
                [ffmpeg, "-y", "-i", str(mkv_path),
                 "-map", f"0:{track['index']}", "-c:s", "copy", str(raw)],
                capture_output=True, text=True,
            )
            if not raw.exists() or raw.stat().st_size == 0:
                print(f"    ⚠ Could not extract — skipping")
                continue

            new_ass = tmp / f"track_{i}_new.ass"
            content = srt_to_ass(raw, font_name) if src_ext == ".srt" \
                      else replace_ass_font(raw, font_name)
            new_ass.write_text(content, encoding="utf-8")
            processed.append((i, new_ass, track))

        if not processed:
            print("  ⚠ No tracks could be processed\n")
            return

        # Build remux command
        # Input 0     = original MKV
        # Inputs 1..N = new ASS files
        cmd = [ffmpeg, "-y", "-i", str(mkv_path)]
        for _, sub, _ in processed:
            cmd += ["-i", str(sub)]

        cmd += ["-map", "0:v", "-map", "0:a?"]          # video + audio from original
        for idx in range(len(processed)):
            cmd += ["-map", str(idx + 1)]               # new subtitle inputs

        cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "ass"]

        for idx, (_, _, track) in enumerate(processed):
            if track["language"]:
                cmd += [f"-metadata:s:s:{idx}", f"language={track['language']}"]
            cmd += [f"-metadata:s:s:{idx}",
                    f"title={(track['title'] or 'Subtitles')} [{font_name}]"]

        # Embed font as MKV attachment
        mime = "font/otf" if font_file.suffix.lower() == ".otf" else "font/ttf"
        cmd += [
            "-attach", str(font_file),
            "-metadata:s:t:0", f"mimetype={mime}",
            "-metadata:s:t:0", f"filename={font_file.name}",
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

    font_name = get_font_name(font_file)

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