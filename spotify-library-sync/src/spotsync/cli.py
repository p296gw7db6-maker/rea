"""Command-line interface for spotsync.

Pipeline:
    spotsync auth                      # one-time OAuth (read-only)
    spotsync export   --out data/spotify
    spotsync scan     --root /music --out data/library.json
    spotsync match    --spotify data/spotify --library data/library.json --out data/matches.json
    spotsync playlists --matches data/matches.json --out data/playlists [--base-path /srv/music]
    spotsync report   --matches data/matches.json --library data/library.json --out data/reports
    spotsync all      --root /music --out data   # export -> scan -> match -> playlists -> report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print_summary(title: str, summary: dict) -> None:
    print(f"\n{title}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def cmd_auth(args: argparse.Namespace) -> int:
    from .config import Config
    from . import spotify_auth

    cfg = Config.from_env()
    token = spotify_auth.authorize(cfg, open_browser=not args.no_browser)
    print("\nAuthorized. Scopes:", token.get("scope", "(none reported)"))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .config import Config
    from . import spotify_export

    cfg = Config.from_env()
    summary = spotify_export.export(cfg, Path(args.out))
    _print_summary("Export summary:", summary)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from . import library_scan

    summary = library_scan.scan(Path(args.root), Path(args.out))
    _print_summary("Scan summary:", summary)
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    from . import spotify_export, library_scan, match

    spotify_tracks = spotify_export.load_all_spotify_tracks(Path(args.spotify))
    library = library_scan.load_library(Path(args.library))
    if not spotify_tracks:
        print("No Spotify tracks found; run `spotsync export` first.", file=sys.stderr)
        return 2
    results = match.match_tracks(
        spotify_tracks,
        library,
        fuzzy_threshold=args.fuzzy_threshold,
        duration_tolerance_s=args.duration_tolerance,
    )
    match.write_matches(results, Path(args.out))
    _print_summary("Match summary:", match.summarize(results))
    return 0


def cmd_playlists(args: argparse.Namespace) -> int:
    from . import match, playlists

    results = match.load_matches(Path(args.matches))
    relative_to = Path(args.relative_to) if args.relative_to else None
    summary = playlists.write_playlists(
        results, Path(args.out), base_path=args.base_path, relative_to=relative_to
    )
    _print_summary("Playlist summary:", summary)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from . import match, report, library_scan

    results = match.load_matches(Path(args.matches))
    gap = report.write_gap_report(results, Path(args.out))
    dup = {"duplicate_groups": 0}
    if args.library:
        library = library_scan.load_library(Path(args.library))
        dupes = report.duplicates_from_library(library)
        dup = report.write_duplicate_report(dupes, Path(args.out))
    _print_summary("Report summary:", {**gap, **dup})
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    from .config import Config
    from . import spotify_export, library_scan, match, playlists, report

    out = Path(args.out)
    spotify_dir = out / "spotify"
    library_index = out / "library.json"
    matches_file = out / "matches.json"

    cfg = Config.from_env()
    _print_summary("Export summary:", spotify_export.export(cfg, spotify_dir))
    _print_summary("Scan summary:", library_scan.scan(Path(args.root), library_index))

    spotify_tracks = spotify_export.load_all_spotify_tracks(spotify_dir)
    library = library_scan.load_library(library_index)
    results = match.match_tracks(
        spotify_tracks, library,
        fuzzy_threshold=args.fuzzy_threshold,
        duration_tolerance_s=args.duration_tolerance,
    )
    match.write_matches(results, matches_file)
    _print_summary("Match summary:", match.summarize(results))

    _print_summary(
        "Playlist summary:",
        playlists.write_playlists(results, out / "playlists", base_path=args.base_path),
    )
    gap = report.write_gap_report(results, out / "reports")
    dup = report.write_duplicate_report(report.duplicates_from_library(library), out / "reports")
    _print_summary("Report summary:", {**gap, **dup})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spotsync",
        description="Export Spotify playlist/library metadata and match it "
        "against your own local music collection (read-only; no audio).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="Run the one-time OAuth (read-only) flow.")
    p_auth.add_argument("--no-browser", action="store_true", help="Print URL only.")
    p_auth.set_defaults(func=cmd_auth)

    p_export = sub.add_parser("export", help="Export playlists + saved tracks.")
    p_export.add_argument("--out", required=True, help="Output directory.")
    p_export.set_defaults(func=cmd_export)

    p_scan = sub.add_parser("scan", help="Scan a local music library.")
    p_scan.add_argument("--root", required=True, help="Library root directory.")
    p_scan.add_argument("--out", required=True, help="Output index JSON file.")
    p_scan.set_defaults(func=cmd_scan)

    p_match = sub.add_parser("match", help="Match Spotify tracks to the library.")
    p_match.add_argument("--spotify", required=True, help="Export directory.")
    p_match.add_argument("--library", required=True, help="Library index JSON.")
    p_match.add_argument("--out", required=True, help="Output matches JSON.")
    p_match.add_argument("--fuzzy-threshold", type=float, default=0.82)
    p_match.add_argument("--duration-tolerance", type=float, default=5.0,
                         help="Max duration delta (seconds) for a fuzzy match.")
    p_match.set_defaults(func=cmd_match)

    p_pl = sub.add_parser("playlists", help="Write local M3U8 playlists.")
    p_pl.add_argument("--matches", required=True, help="Matches JSON.")
    p_pl.add_argument("--out", required=True, help="Output directory.")
    p_pl.add_argument("--base-path", default=None,
                      help="Re-root file paths onto the server mount (by filename).")
    p_pl.add_argument("--relative-to", default=None,
                      help="Write paths relative to this directory.")
    p_pl.set_defaults(func=cmd_playlists)

    p_rep = sub.add_parser("report", help="Write gap (shopping list) + duplicate reports.")
    p_rep.add_argument("--matches", required=True, help="Matches JSON.")
    p_rep.add_argument("--library", default=None, help="Library index JSON (for duplicates).")
    p_rep.add_argument("--out", required=True, help="Output directory.")
    p_rep.set_defaults(func=cmd_report)

    p_all = sub.add_parser("all", help="Run the whole pipeline end to end.")
    p_all.add_argument("--root", required=True, help="Library root directory.")
    p_all.add_argument("--out", required=True, help="Output base directory.")
    p_all.add_argument("--base-path", default=None, help="Server mount re-root for playlists.")
    p_all.add_argument("--fuzzy-threshold", type=float, default=0.82)
    p_all.add_argument("--duration-tolerance", type=float, default=5.0)
    p_all.set_defaults(func=cmd_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as err:  # surface a clean message, not a traceback, to users
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
