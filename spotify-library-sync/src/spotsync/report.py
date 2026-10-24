"""Reports: the 'shopping list' (gaps) and library duplicates.

Gaps = Spotify tracks with no local match -> what you still need to acquire.
Written as both CSV (open in a spreadsheet) and JSON (machine-readable).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from .match import MatchResult
from . import normalize


def _gap_rows(results: list[MatchResult]) -> list[dict]:
    """One row per distinct missing track, with the playlists it appears in."""
    seen: dict[str, dict] = {}
    for result in results:
        if result.library is not None:
            continue
        sp = result.spotify
        key = sp.isrc or normalize.match_key(sp.primary_artist, sp.title)
        if key not in seen:
            seen[key] = {
                "title": sp.title,
                "artist": sp.primary_artist,
                "all_artists": ", ".join(sp.artists),
                "album": sp.album,
                "isrc": sp.isrc or "",
                "playlists": set(),
                "best_score": result.score,
            }
        seen[key]["playlists"].update(sp.context or [])
        seen[key]["best_score"] = max(seen[key]["best_score"], result.score)
    rows = []
    for entry in seen.values():
        entry["playlists"] = "; ".join(sorted(entry["playlists"]))
        entry["best_score"] = round(entry["best_score"], 4)
        rows.append(entry)
    rows.sort(key=lambda r: (r["artist"].lower(), r["title"].lower()))
    return rows


def write_gap_report(results: list[MatchResult], out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _gap_rows(results)

    fields = ["artist", "title", "album", "all_artists", "isrc", "playlists", "best_score"]
    csv_file = out_dir / "gaps.csv"
    with csv_file.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fields})

    (out_dir / "gaps.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    return {"missing_tracks": len(rows), "csv": str(csv_file)}


def write_duplicate_report(library_index_duplicates: dict[str, list[str]], out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    dupes = {k: paths for k, paths in library_index_duplicates.items() if len(paths) > 1}
    (out_dir / "duplicates.json").write_text(json.dumps(dupes, indent=2, ensure_ascii=False))
    return {"duplicate_groups": len(dupes)}


def duplicates_from_library(library) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for track in library:
        if not track.path:
            continue
        key = normalize.match_key(track.primary_artist, track.title, aggressive=True)
        groups[key].append(track.path)
    return groups
