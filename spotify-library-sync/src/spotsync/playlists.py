"""Write local playlists (M3U8) from match results.

Playlists point at your own library files, so Navidrome / Jellyfin / Plex /
any player can read them. Only matched tracks are written; the gap report
covers what is missing.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from .match import MatchResult

_SAFE = "-_.() "


def _safe_filename(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in _SAFE else "_" for c in name).strip()
    return (cleaned or "playlist")[:120]


def _extinf(result: MatchResult) -> str:
    secs = -1
    if result.library and result.library.duration_ms:
        secs = round(result.library.duration_ms / 1000)
    return f"#EXTINF:{secs},{result.spotify.display}"


def _target_path(result: MatchResult, base_path: str | None, relative_to: Path | None) -> str | None:
    path = result.library.path if result.library else None
    if not path:
        return None
    if base_path:
        # Re-root the scanned path onto the server's mount point.
        path = str(Path(base_path) / Path(path).name) if base_path else path
    if relative_to is not None:
        try:
            return os.path.relpath(path, relative_to)
        except ValueError:
            return path
    return path


def write_playlists(
    results: list[MatchResult],
    out_dir: Path,
    base_path: str | None = None,
    relative_to: Path | None = None,
) -> dict:
    """Group matched tracks by their Spotify playlist context and write one
    .m3u8 per playlist. Returns a summary of files written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    by_playlist: dict[str, list[MatchResult]] = defaultdict(list)
    for result in results:
        if result.library is None:
            continue
        contexts = result.spotify.context or ["unsorted"]
        for ctx in contexts:
            by_playlist[ctx].append(result)

    written: dict[str, int] = {}
    for name, rows in sorted(by_playlist.items()):
        lines = ["#EXTM3U"]
        count = 0
        for row in rows:
            target = _target_path(row, base_path, relative_to)
            if not target:
                continue
            lines.append(_extinf(row))
            lines.append(target)
            count += 1
        out_file = out_dir / f"{_safe_filename(name)}.m3u8"
        out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written[out_file.name] = count

    return {"playlists_written": len(written), "files": written}
