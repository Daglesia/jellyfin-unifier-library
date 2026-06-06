# ── audio.py — audio inspection and re-encoding for Jellyfin MKVs ────────────
# Default: stream-copy (no quality loss).
# Optionally normalise volume or re-encode to AAC for client compatibility.
#
# Called by main.py, or directly:
#   python audio.py input.mkv  [output_dir] [--reencode] [--normalize]
#   python audio.py C:\Movies  [output_dir] [--reencode] [--normalize]

import subprocess
import sys
from pathlib import Path

from config import find_ffmpeg, find_ffprobe

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts", ".wmv"}

# AAC re-encode quality — only used when --reencode is passed
AAC_BITRATE = "192k"   # 128k=small  192k=good  256k=excellent


# ── Audio track inspection ───────────────────────────────────────────────────

def get_audio_tracks(ffprobe: str, path: Path) -> list[dict]:
    result = subprocess.run(
        [ffprobe, "-v", "error",
         "-select_streams", "a",
         "-show_entries",
         "stream=index,codec_name,channels,bit_rate:stream_tags=language,title",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    tracks = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split(",")
        tracks.append({
            "index":    parts[0] if len(parts) > 0 else "?",
            "codec":    parts[1] if len(parts) > 1 else "?",
            "channels": parts[2] if len(parts) > 2 else "?",
            "bitrate":  parts[3] if len(parts) > 3 else "?",
            "language": parts[4] if len(parts) > 4 else "",
            "title":    parts[5] if len(parts) > 5 else "",
        })
    return tracks


def print_audio_info(path: Path, ffprobe: str) -> None:
    tracks = get_audio_tracks(ffprobe, path)
    if not tracks:
        print(f"  ⚠ No audio tracks found in {path.name}")
        return
    print(f"  Audio tracks in {path.name}:")
    for t in tracks:
        ch_label = {"1": "mono", "2": "stereo", "6": "5.1", "8": "7.1"}.get(t["channels"], f"{t['channels']}ch")
        br = f"{int(t['bitrate'])//1000}k" if t["bitrate"].isdigit() else "?"
        print(f"    [{t['index']}] {t['codec']:8s}  {ch_label:8s}  {br:6s}  "
              f"lang={t['language'] or '?'}  title={t['title'] or '?'}")


# ── Processing ───────────────────────────────────────────────────────────────

def process_mkv(
    mkv_path: Path,
    output_dir: Path,
    ffmpeg: str,
    ffprobe: str,
    reencode: bool = False,
    normalize: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_reencode" if reencode else "_audio"
    out = output_dir / f"{mkv_path.stem}{suffix}.mkv"

    if out.exists():
        print(f"  ↷ Skipping (output exists): {out.name}\n")
        return

    print_audio_info(mkv_path, ffprobe)

    cmd = [ffmpeg, "-y", "-i", str(mkv_path),
           "-map", "0",             # keep all streams
           "-c:v", "copy",          # video: always stream-copy
           "-c:s", "copy"]          # subtitles: always stream-copy

    if normalize and reencode:
        # Normalize + re-encode: two-pass loudnorm
        print(f"  Re-encoding audio → AAC {AAC_BITRATE} with loudnorm...")
        cmd += [
            "-c:a", "aac",
            "-b:a", AAC_BITRATE,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        ]
    elif normalize:
        # Normalize only — must re-encode (can't normalize a copied stream)
        print(f"  Re-encoding audio → AAC {AAC_BITRATE} with loudnorm (normalize implies re-encode)...")
        cmd += [
            "-c:a", "aac",
            "-b:a", AAC_BITRATE,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        ]
    elif reencode:
        # Re-encode without normalizing — useful to convert DTS/TrueHD for
        # clients that can't decode them
        print(f"  Re-encoding audio → AAC {AAC_BITRATE} (no normalize)...")
        cmd += ["-c:a", "aac", "-b:a", AAC_BITRATE]
    else:
        # Default: stream-copy — zero quality loss
        print(f"  Audio: stream-copied (original codec preserved)")
        cmd += ["-c:a", "copy"]

    cmd += [str(out)]

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
    # Simple arg parsing (avoids requiring argparse when called from main.py)
    reencode  = "--reencode"  in args
    normalize = "--normalize" in args
    info_only = "--info"      in args
    clean_args = [a for a in args if not a.startswith("--")]

    if not clean_args:
        print("Usage: python audio.py <file_or_folder> [output_dir] [--reencode] [--normalize] [--info]")
        print()
        print("  (no flags)    Stream-copy audio — zero quality loss (default)")
        print("  --reencode    Re-encode to AAC (for clients that can't play AC3/DTS)")
        print("  --normalize   Re-encode + loudnorm volume levelling")
        print("  --info        Just print audio track info, no output file")
        sys.exit(0)

    ffmpeg  = find_ffmpeg()
    ffprobe = find_ffprobe()

    print(f"\n🔊 Audio utility")
    print(f"   Mode   : {'info only' if info_only else 'normalize+reencode' if normalize else 'reencode AAC' if reencode else 'stream-copy (lossless)'}")
    print(f"   ffmpeg : {ffmpeg}\n")

    input_path = Path(clean_args[0])
    output_dir = Path(clean_args[1]) if len(clean_args) > 1 else None

    if input_path.is_dir():
        out = output_dir or (input_path / "audio_out")
        files = [f for f in sorted(input_path.iterdir())
                 if f.suffix.lower() in VIDEO_EXTS]
        if not files:
            print(f"No video files found in: {input_path}")
            return
        print(f"Found {len(files)} file(s)\n")
        for f in files:
            print(f"▶ {f.name}")
            if info_only:
                print_audio_info(f, ffprobe)
            else:
                process_mkv(f, out, ffmpeg, ffprobe, reencode, normalize)

    elif input_path.suffix.lower() in VIDEO_EXTS:
        out = output_dir or (input_path.parent / "audio_out")
        print(f"▶ {input_path.name}")
        if info_only:
            print_audio_info(input_path, ffprobe)
        else:
            process_mkv(input_path, out, ffmpeg, ffprobe, reencode, normalize)

    else:
        print(f"Unsupported file type: {input_path.suffix}")
        sys.exit(1)


if __name__ == "__main__":
    run(sys.argv[1:])