"""Command-line interface for spotsync.

Local-library pipeline:
    spotsync auth                      # one-time OAuth (read-only)
    spotsync export   --out data/spotify
    spotsync scan     --root /music --out data/library.json
    spotsync match    --spotify data/spotify --library data/library.json --out data/matches.json
    spotsync playlists --spotify data/spotify --matches data/matches.json --out data/playlists \
                       [--path-map /music=/srv/music]
    spotsync report   --matches data/matches.json --library data/library.json --out data/reports
    spotsync all      --root /music --out data   # export -> scan -> match -> playlists -> report

Jellyfin (additive, preview-first):
    spotsync jellyfin-scan --url URL --api-key KEY [--user NAME] --out data/jellyfin.json
    spotsync match --spotify data/spotify --library data/jellyfin.json --out data/matches-jf.json
    spotsync jellyfin-push --spotify data/spotify --matches data/matches-jf.json \
                           [--url URL --api-key KEY --user NAME] [--apply]
"""

from __future__ import annotations

import argparse
import json
import os
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
    _print_summary("Export summary:", spotify_export.export(cfg, Path(args.out)))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from . import library_scan

    _print_summary("Scan summary:", library_scan.scan(Path(args.root), Path(args.out)))
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    from . import spotify_export, library_scan, match

    spotify_tracks = spotify_export.load_all_spotify_tracks(Path(args.spotify))
    library = library_scan.load_library(Path(args.library))
    if not spotify_tracks:
        print("No Spotify tracks found; run `spotsync export` first.", file=sys.stderr)
        return 2
    results = match.match_tracks(
        spotify_tracks, library,
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
    summary = playlists.build_playlists(
        Path(args.spotify), results, Path(args.out),
        path_maps=args.path_map, relative_to=relative_to,
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
        dup = report.write_duplicate_report(report.duplicates_from_library(library), Path(args.out))
    _print_summary("Report summary:", {**gap, **dup})
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    from .config import Config
    from . import spotify_export, library_scan, match, playlists, report

    out = Path(args.out)
    spotify_dir = out / "spotify"
    library_index = out / "library.json"

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
    match.write_matches(results, out / "matches.json")
    _print_summary("Match summary:", match.summarize(results))

    _print_summary(
        "Playlist summary:",
        playlists.build_playlists(spotify_dir, results, out / "playlists", path_maps=args.path_map),
    )
    gap = report.write_gap_report(results, out / "reports")
    dup = report.write_duplicate_report(report.duplicates_from_library(library), out / "reports")
    _print_summary("Report summary:", {**gap, **dup})
    return 0


def _jellyfin_creds(args: argparse.Namespace) -> tuple[str, str]:
    url = args.url or os.environ.get("SPOTSYNC_JELLYFIN_URL", "")
    key = args.api_key or os.environ.get("SPOTSYNC_JELLYFIN_API_KEY", "")
    if not url or not key:
        raise RuntimeError(
            "Jellyfin needs --url and --api-key (or SPOTSYNC_JELLYFIN_URL / "
            "SPOTSYNC_JELLYFIN_API_KEY)."
        )
    return url, key


def cmd_jellyfin_scan(args: argparse.Namespace) -> int:
    from . import jellyfin

    url, key = _jellyfin_creds(args)
    client = jellyfin.JellyfinClient(url, key)
    user_id = client.resolve_user_id(args.user)
    _print_summary("Jellyfin scan summary:", jellyfin.scan(client, Path(args.out), user_id=user_id))
    return 0


def cmd_jellyfin_push(args: argparse.Namespace) -> int:
    from . import match, jellyfin

    results = match.load_matches(Path(args.matches))
    plans = jellyfin.plan_playlists(Path(args.spotify), results)

    if not args.apply:
        # Preview is fully offline; show exactly what --apply would create.
        summary = jellyfin.push(plans, apply=False, name_prefix=args.name_prefix)
        summary["note"] = "preview only; re-run with --apply to create playlists"
        _print_summary("Jellyfin push PREVIEW:", summary)
        return 0

    url, key = _jellyfin_creds(args)
    client = jellyfin.JellyfinClient(url, key)
    user_id = client.resolve_user_id(args.user)
    summary = jellyfin.push(plans, apply=True, client=client, user_id=user_id, name_prefix=args.name_prefix)
    _print_summary("Jellyfin push APPLIED:", summary)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spotsync",
        description="Export Spotify playlist/library metadata (read-only) and "
        "match it against your own local or Jellyfin music collection. No audio.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("auth", help="Run the one-time OAuth (read-only) flow.")
    p.add_argument("--no-browser", action="store_true", help="Print URL only.")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("export", help="Export playlists + saved tracks.")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("scan", help="Scan a local music library.")
    p.add_argument("--root", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("match", help="Match Spotify tracks to a library index.")
    p.add_argument("--spotify", required=True, help="Export directory.")
    p.add_argument("--library", required=True, help="Library index JSON (local or jellyfin).")
    p.add_argument("--out", required=True)
    p.add_argument("--fuzzy-threshold", type=float, default=0.82)
    p.add_argument("--duration-tolerance", type=float, default=5.0)
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("playlists", help="Write local M3U8 playlists.")
    p.add_argument("--spotify", required=True, help="Export directory (owns playlist order/identity).")
    p.add_argument("--matches", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--path-map", action="append", default=None, metavar="LOCAL=SERVER",
                   help="Rewrite a local path prefix onto a server prefix (repeatable).")
    p.add_argument("--relative-to", default=None, help="Write paths relative to this directory.")
    p.set_defaults(func=cmd_playlists)

    p = sub.add_parser("report", help="Write gap (shopping list) + duplicate reports.")
    p.add_argument("--matches", required=True)
    p.add_argument("--library", default=None, help="Library index JSON (for duplicates).")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("all", help="Run the whole local pipeline end to end.")
    p.add_argument("--root", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--path-map", action="append", default=None, metavar="LOCAL=SERVER")
    p.add_argument("--fuzzy-threshold", type=float, default=0.82)
    p.add_argument("--duration-tolerance", type=float, default=5.0)
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("jellyfin-scan", help="Read Audio items from Jellyfin into a library index.")
    p.add_argument("--url", default=None, help="Jellyfin base URL (or SPOTSYNC_JELLYFIN_URL).")
    p.add_argument("--api-key", default=None, help="API key (or SPOTSYNC_JELLYFIN_API_KEY).")
    p.add_argument("--user", default=None, help="Jellyfin user name (required if >1 user).")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_jellyfin_scan)

    p = sub.add_parser("jellyfin-push", help="Create Jellyfin playlists from matches (preview by default).")
    p.add_argument("--spotify", required=True, help="Export directory.")
    p.add_argument("--matches", required=True, help="Matches JSON (matched against a jellyfin index).")
    p.add_argument("--url", default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--user", default=None)
    p.add_argument("--name-prefix", default="", help="Prefix for created playlist names.")
    p.add_argument("--apply", action="store_true", help="Actually create playlists (default: preview).")
    p.set_defaults(func=cmd_jellyfin_push)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as err:  # surface a clean message, not a traceback
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
