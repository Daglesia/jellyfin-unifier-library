import autosubsync
import glob
import os
import shutil
import subprocess

# ── Directories ──────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR  = os.path.join(BASE_DIR, 'input')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Constants ─────────────────────────────────────────────────────────────────
VIDEO_GLOBS        = ('*.mp4', '*.mkv')
# Subtitle priority: .ass is tried first (richer format), then .srt as fallback
SUBTITLE_PRIORITY  = ('.ass', '.srt')
FONT_EXTENSIONS    = {'.ttf', '.otf', '.woff', '.woff2'}

# MIME types used when attaching fonts via mkvmerge
FONT_MIME = {
    '.ttf':   'application/x-truetype-font',
    '.otf':   'application/vnd.ms-opentype',
    '.woff':  'font/woff',
    '.woff2': 'font/woff2',
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def find_subtitle(base: str) -> str | None:
    """
    Given a path stem (no extension), return the first matching subtitle file
    in SUBTITLE_PRIORITY order, or None if none exist.
    """
    for ext in SUBTITLE_PRIORITY:
        candidate = base + ext
        if os.path.exists(candidate):
            return candidate
    return None


def probe_subtitle_codec(video_path: str) -> str | None:
    """
    Use ffprobe to return the codec name of the first subtitle stream,
    or None if there are no subtitle streams or ffprobe is unavailable.
    """
    if not shutil.which('ffprobe'):
        return None

    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 's:0',
        '-show_entries', 'stream=codec_name',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    codec = result.stdout.strip()
    return codec if codec else None


def extract_subtitle_from_mkv(video_path: str) -> str | None:
    """
    Extract the first subtitle track from an MKV using ffmpeg.
    Saves '<stem>_extracted.ass' (text) or '<stem>_extracted.srt' next to
    the video and returns its path, or None on failure.

    Image-based codecs (PGS, VobSub) cannot be used with autosubsync and
    are skipped with a warning.
    """
    if not shutil.which('ffmpeg'):
        print("    ⚠  ffmpeg not found — cannot extract embedded subtitles.")
        return None

    IMAGE_CODECS = {'hdmv_pgs_subtitle', 'dvd_subtitle', 'dvdsub', 'pgssub'}
    codec = probe_subtitle_codec(video_path)

    if codec is None:
        print("    ⚠  No subtitle streams found inside MKV.")
        return None

    if codec in IMAGE_CODECS:
        print(
            f"    ⚠  Embedded subtitle is image-based ({codec}) — "
            "autosubsync requires text subtitles. Skipping."
        )
        return None

    # Pick output extension: ass for ass/ssa, srt for everything else
    out_ext  = '.ass' if codec in ('ass', 'ssa') else '.srt'
    stem     = video_path.rpartition('.')[0]
    out_sub  = stem + '_extracted' + out_ext

    cmd = ['ffmpeg', '-y', '-i', video_path, '-map', '0:s:0', out_sub]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0 or not os.path.exists(out_sub):
        print(f"    ⚠  ffmpeg could not extract subtitle track.")
        print(f"       {result.stderr.strip()}")
        return None

    print(f"    Extracted: {os.path.basename(out_sub)}  (codec: {codec})")
    return out_sub


def find_fonts(directory: str) -> list[str]:
    """Return all font files found in *directory*."""
    return [
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if os.path.splitext(f)[1].lower() in FONT_EXTENSIONS
    ]


def sync_subtitle(video_path: str, sub_path: str) -> str:
    """
    Synchronize *sub_path* against *video_path* with autosubsync.
    Writes '<stem>_synced<ext>' next to the original and returns its path.

    Note: autosubsync works natively with .srt.  For .ass files it still
    adjusts timing offsets, but complex override tags are preserved as-is.
    """
    stem, _, ext = sub_path.rpartition('.')
    synced_path = f"{stem}_synced.{ext}"
    print(f"    Syncing  : {os.path.basename(sub_path)}")
    autosubsync.synchronize(video_path, sub_path, synced_path)
    print(f"    → Saved  : {os.path.basename(synced_path)}")
    return synced_path


def embed_fonts_into_mkv(
    mkv_src: str,
    fonts: list[str],
    output_path: str,
) -> bool:
    """
    Use mkvmerge to copy *mkv_src* to *output_path* with *fonts* attached.
    Returns True on success, False if mkvmerge is unavailable or fails.
    """
    if not shutil.which('mkvmerge'):
        print(
            "    ⚠  mkvmerge not found — font embedding skipped.\n"
            "       Install MKVToolNix (https://mkvtoolnix.download/) to enable it."
        )
        return False

    cmd = ['mkvmerge', '-o', output_path, mkv_src]
    for font in fonts:
        ext  = os.path.splitext(font)[1].lower()
        mime = FONT_MIME.get(ext, 'application/octet-stream')
        cmd += [
            '--attachment-name',      os.path.basename(font),
            '--attachment-mime-type', mime,
            '--attach-file',          font,
        ]

    print(f"    Embedding {len(fonts)} font(s) → {os.path.basename(output_path)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    ✗ mkvmerge error:\n{result.stderr.strip()}")
        return False

    print(f"    ✓ Font-embedded MKV saved.")
    return True


# ── Main loop ─────────────────────────────────────────────────────────────────

print(f"Input dir : {INPUT_DIR}")
print(f"Exists    : {os.path.exists(INPUT_DIR)}")
print(f"Contents  : {os.listdir(INPUT_DIR)}\n")

processed = skipped = 0

for pattern in VIDEO_GLOBS:
    for video_path in sorted(glob.glob(os.path.join(INPUT_DIR, pattern))):
        filename = os.path.basename(video_path)
        stem, _, video_ext = video_path.rpartition('.')
        video_ext = video_ext.lower()

        print(f"┌ {filename}")

        # ── 1. Locate subtitle ────────────────────────────────────────────────
        sub_path = find_subtitle(stem)

        # Fallback: try extracting an embedded track from the MKV
        if sub_path is None and video_ext == 'mkv':
            print("│  No external subtitle found — trying embedded tracks...")
            sub_path = extract_subtitle_from_mkv(video_path)

        if sub_path is None:
            print("│  No subtitle found anywhere — skipping.\n└")
            skipped += 1
            continue

        # ── 2. Sync subtitles ─────────────────────────────────────────────────
        synced_sub = sync_subtitle(video_path, sub_path)

        # ── 3. MKV: embed fonts ───────────────────────────────────────────────
        if video_ext == 'mkv':
            fonts = find_fonts(INPUT_DIR)
            out_mkv = os.path.join(
                OUTPUT_DIR,
                os.path.basename(stem) + '_with_fonts.mkv'
            )

            if fonts:
                names = [os.path.basename(f) for f in fonts]
                print(f"    Fonts    : {names}")
                embed_fonts_into_mkv(video_path, fonts, out_mkv)
            else:
                print("    No font files found in input dir — skipping font embed.")

        processed += 1
        print("└")

print(f"\nDone. Processed: {processed}  |  Skipped: {skipped}")