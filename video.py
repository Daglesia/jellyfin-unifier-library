# ── video.py — downscale 1080p → 720p with HEVC, lossless audio passthrough ──
#
# Called by main.py, or directly:
#   python video.py input.mkv [output_dir]
#   python video.py C:\Movies  [output_dir]
import subprocess
import sys
from pathlib import Path
from config import find_ffmpeg, find_ffprobe, VIDEO_CRF, VIDEO_PRESET

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts", ".wmv"}


def get_video_info(ffprobe: str, path: Path) -> dict:
    result = subprocess.run(
        [ffprobe, "-v", "error",
         "-select_streams", "v:0",
         "-show_entries", "stream=width,height,codec_name",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        parts = result.stdout.strip().split(",")
        return {
            "width":  int(parts[0]),
            "height": int(parts[1]),
            "codec":  parts[2].lower(),   # e.g. "h264", "hevc", "av1"
        }
    except Exception:
        return {"width": 0, "height": 0, "codec": "unknown"}


def convert(input_path: Path, output_dir: Path) -> None:
    ffmpeg  = find_ffmpeg()
    ffprobe = find_ffprobe()

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{input_path.stem}_720p.mkv"

    if out.exists():
        print(f"  ↷ Skipping (output exists): {out.name}\n")
        return

    info    = get_video_info(ffprobe, input_path)
    height  = info["height"]
    codec   = info["codec"]
    is_hevc = codec in {"hevc", "h265"}

    if height > 0 and height <= 720 and is_hevc:
        print(f"  ↷ Skipping {input_path.name} — already {height}p HEVC\n")
        return
    elif height > 0 and height <= 720 and not is_hevc:
        print(f"  ↷ Re-encoding {input_path.name} — {height}p but codec is {codec}, converting to HEVC\n")
    elif height > 720 and is_hevc:
        print(f"  Downscaling {input_path.name} — already HEVC but {height}p, downscaling to 720p\n")
    # else: >720p + non-HEVC — normal encode path, label printed below

    cmd = [
        ffmpeg, "-y", "-i", str(input_path),
        "-vf", "scale=-2:720",          # downscale, keep aspect ratio
        "-c:v", "libx265",              # HEVC — best quality/size ratio
        "-pix_fmt", "yuv420p",      # ← force 8-bit 4:2:0, prevents Pi green-screen
        "-profile:v", "main",       # ← matches yuv420p 8-bit, max HW-decoder compat
        "-crf", str(VIDEO_CRF),
        "-preset", VIDEO_PRESET,
        "-tag:v", "hvc1",               # Apple/Jellyfin compatibility tag
        "-c:a", "copy",                 # audio passthrough — NO re-encode
        "-map", "0",                    # keep all streams (audio, subs, chapters)
        "-c:s", "copy",                 # copy subtitles as-is
        str(out),
    ]

    if height > 720:
        label = f"{height}p → 720p"
    else:
        label = f"{height}p {codec} → 720p HEVC"

    print(f"  Encoding ({label})  crf={VIDEO_CRF}  preset={VIDEO_PRESET}")
    print(f"  Audio: stream-copied (no quality loss)\n")

    result = subprocess.run(cmd)
    if result.returncode == 0 and out.exists():
        in_mb  = input_path.stat().st_size / 1024 / 1024
        out_mb = out.stat().st_size        / 1024 / 1024
        saved  = 100 * (1 - out_mb / in_mb)
        print(f"  ✓ {input_path.name}  {in_mb:.1f} MB → {out_mb:.1f} MB  ({saved:.0f}% smaller)\n")
    else:
        print(f"  ✗ Failed: {input_path.name}\n")
        out.unlink(missing_ok=True)


def run(args: list[str]) -> None:
    if not args:
        print("Usage: python video.py <file_or_folder> [output_dir]")
        sys.exit(0)

    input_path = Path(args[0])
    output_dir = Path(args[1]) if len(args) > 1 else None

    ffmpeg  = find_ffmpeg()   # validate early
    ffprobe = find_ffprobe()

    print(f"\n📽  Video downscaler  (720p HEVC, audio passthrough)")
    print(f"    ffmpeg: {ffmpeg}\n")

    # ── guard: path must exist ──────────────────────────────────────────────
    if not input_path.exists():
        print(f"  ✗ Path not found: {input_path.resolve()}")
        sys.exit(1)

    if input_path.is_dir():
        out = output_dir or (input_path / "converted_720p")
        files = [f for f in sorted(input_path.iterdir())
                 if f.suffix.lower() in VIDEO_EXTS]
        if not files:
            print(f"No video files found in: {input_path}")
            return
        print(f"Found {len(files)} video(s)\n")
        for f in files:
            print(f"▶ {f.name}")
            convert(f, out)

    elif input_path.is_file() and input_path.suffix.lower() in VIDEO_EXTS:
        out = output_dir or (input_path.parent / "converted_720p")
        print(f"▶ {input_path.name}")
        convert(input_path, out)

    else:
        print(f"  ✗ Not a video file or directory: {input_path.resolve()}")
        print(f"    Suffix detected: '{input_path.suffix}' — supported: {VIDEO_EXTS}")
        sys.exit(1)


if __name__ == "__main__":
    run(sys.argv[1:])