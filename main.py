# ── main.py — Jellyfin converter toolkit ─────────────────────────────────────
#
# Usage:
#   python main.py                        # interactive menu
#   python main.py video  input [output]  # run video utility directly
#   python main.py font   input [output]  # run font utility directly
#   python main.py audio  input [output] [--reencode] [--normalize] [--info]
#   python main.py rename input --show "Series Name" [output] [--season N]

import sys

MENU = """
╔══════════════════════════════════════════╗
║       Jellyfin Converter Toolkit         ║
╠══════════════════════════════════════════╣
║  1 · Video   — downscale 1080p → 720p   ║
║  2 · Font    — change subtitle font      ║
║  3 · Audio   — inspect / fix audio       ║
║  4 · Rename  — Jellyfin episode naming   ║
║  q · Quit                                ║
╚══════════════════════════════════════════╝
"""

HELP = {
    "video": (
        "  Downscale 1080p → 720p using HEVC (H.265).\n"
        "  Audio is stream-copied — zero quality loss.\n\n"
        "  Usage : python main.py video <file_or_folder> [output_dir]\n"
        "  Config: edit VIDEO_CRF and VIDEO_PRESET in config.py"
    ),
    "font": (
        "  Change subtitle font in MKV or standalone .srt/.ass files.\n"
        "  The .otf font is embedded inside the output MKV.\n"
        "  Video and audio are stream-copied — zero re-encoding.\n\n"
        "  Usage : python main.py font <file_or_folder> [output_dir]\n"
        "  Config: edit FONT_FILE in config.py"
    ),
    "audio": (
        "  Inspect or fix audio tracks in MKV files.\n\n"
        "  Modes (add as flags):\n"
        "    (none)       Stream-copy — lossless passthrough (default)\n"
        "    --info       Print audio track info only, no output file\n"
        "    --reencode   Re-encode to AAC (for clients that can't play AC3/DTS)\n"
        "    --normalize  Re-encode + loudnorm volume levelling\n\n"
        "  Usage : python main.py audio <file_or_folder> [output_dir] [--flags]\n"
        "  Config: edit AAC_BITRATE in audio.py"
    ),
    "rename": (
        "  Rename episode files to Jellyfin's naming convention:\n"
        "    Series Name S01E01.mkv\n\n"
        "  Season/episode are read from the source filename when possible\n"
        "  (e.g. 'S02 E28', 's2e28'); otherwise the episode number is taken\n"
        "  from a trailing '- 11' / '- 11v2' style number and season\n"
        "  defaults to --season (or 1).\n\n"
        "  Usage : python main.py rename <file_or_folder> --show \"Series Name\" [output_dir] [--season N]\n"
        "  Flags : --show / -n   Series name (required)\n"
        "          --season      Default season number (default: 1)"
    ),
}


def prompt_path(label: str) -> str:
    val = input(f"  {label}: ").strip().strip('"').strip("'")
    if not val:
        print("  (cancelled)")
        sys.exit(0)
    return val


def interactive_video() -> None:
    import video
    print("\n── Video downscaler ─────────────────────────────")
    print(HELP["video"])
    print()
    inp = prompt_path("Input file or folder")
    out = input("  Output folder (leave blank for default): ").strip().strip('"').strip("'")
    args = [inp] + ([out] if out else [])
    video.run(args)


def interactive_font() -> None:
    import font
    print("\n── Font changer ─────────────────────────────────")
    print(HELP["font"])
    print()
    inp = prompt_path("Input file or folder")
    out = input("  Output folder (leave blank for default): ").strip().strip('"').strip("'")
    args = [inp] + ([out] if out else [])
    font.run(args)


def interactive_audio() -> None:
    import audio
    print("\n── Audio utility ────────────────────────────────")
    print(HELP["audio"])
    print()
    inp = prompt_path("Input file or folder")
    out = input("  Output folder (leave blank for default): ").strip().strip('"').strip("'")
    print()
    print("  Flags (space-separated, or leave blank for lossless copy):")
    print("    --info  --reencode  --normalize")
    flags = input("  Flags: ").strip().split()
    args = [inp] + ([out] if out else []) + flags
    audio.run(args)


def interactive_rename() -> None:
    import rename
    print("\n── Renamer ───────────────────────────────────────")
    print(HELP["rename"])
    print()
    inp = prompt_path("Input file or folder")
    show = input("  Series name: ").strip().strip('"').strip("'")
    if not show:
        print("  (cancelled — series name required)")
        sys.exit(0)
    out = input("  Output folder (leave blank to rename in place): ").strip().strip('"').strip("'")
    season = input("  Default season number (leave blank for 1): ").strip()
    args = [inp, "--show", show] + ([out] if out else []) + (["--season", season] if season else [])
    rename.run(args)


def run_interactive() -> None:
    print(MENU)
    choice = input("Choose [1/2/3/4/q]: ").strip().lower()
    print()

    if choice in ("1", "video"):
        interactive_video()
    elif choice in ("2", "font"):
        interactive_font()
    elif choice in ("3", "audio"):
        interactive_audio()
    elif choice in ("4", "rename"):
        interactive_rename()
    elif choice in ("q", "quit", "exit"):
        sys.exit(0)
    else:
        print(f"Unknown choice: '{choice}'")
        sys.exit(1)


def run_direct(args: list[str]) -> None:
    """Direct CLI: python main.py <utility> [args...]"""
    import video, font, audio, rename

    cmd  = args[0].lower()
    rest = args[1:]

    if cmd == "video":
        video.run(rest)
    elif cmd == "font":
        font.run(rest)
    elif cmd == "audio":
        audio.run(rest)
    elif cmd == "rename":
        rename.run(rest)
    elif cmd in ("help", "--help", "-h"):
        print("\nJellyfin Converter Toolkit\n")
        for name, text in HELP.items():
            print(f"── {name} ──")
            print(text)
            print()
    else:
        print(f"Unknown utility: '{cmd}'")
        print("Available: video, font, audio, rename")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_direct(sys.argv[1:])
    else:
        run_interactive()