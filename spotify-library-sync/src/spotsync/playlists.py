"""Write local playlists (M3U8) from the Spotify export + match results.

Playlists are rebuilt from the *exported playlist structure* (which owns the
playlist identity and track order), joined to match results by Spotify track id.
This preserves order and keeps two playlists that happen to share a name as two
separate files. Output paths point at your own library files, so any player
(Jellyfin / Navidrome / Plex / VLC) can read them.

Path remapping uses prefix rules (local -> server), so the folder structure
below the prefix is preserved — not just the bare filename.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .match import MatchResult
from .models import Track

_SAFE = "-_.() "


def _safe_filename(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in _SAFE else "_" for c in name).strip()
    return (cleaned or "playlist")[:120]


class PathMapper:
    """Rewrite scanned local paths onto server paths by longest-prefix match.

    Each rule maps a local path prefix to a server prefix; the remainder of the
    path (subfolders + filename) is preserved. Rules are tried longest-first so
    a more specific mount wins over a more general one.
    """

    def __init__(self, rules: list[tuple[str, str]]):
        # Longest local prefix first for deterministic, specific-wins matching.
        self.rules = sorted(rules, key=lambda r: len(r[0]), reverse=True)

    @classmethod
    def parse(cls, entries: Optional[list[str]]) -> "PathMapper":
        rules: list[tuple[str, str]] = []
        for entry in entries or []:
            if "=" not in entry:
                raise ValueError(
                    f"Invalid --path-map '{entry}'; expected LOCAL=SERVER "
                    "(e.g. /home/me/music=/srv/music)."
                )
            local, server = entry.split("=", 1)
            rules.append((local, server))
        return cls(rules)

    def map(self, path: str) -> str:
        for local, server in self.rules:
            if path == local or path.startswith(local):
                return (server + path[len(local):]).replace("\\", "/")
        return path


def _match_index(results: list[MatchResult]) -> dict[str, Track]:
    """Spotify track id -> matched library/server Track (only matched ones)."""
    index: dict[str, Track] = {}
    for result in results:
        if result.library is not None and result.spotify.source_id:
            index.setdefault(result.spotify.source_id, result.library)
    return index


def load_export_sources(spotify_dir: Path) -> list[tuple[str, str, list[Track]]]:
    """Return [(playlist_name, stable_id, ordered spotify tracks)] including the
    saved/'Liked Songs' pseudo-playlist, straight from the export files."""
    sources: list[tuple[str, str, list[Track]]] = []
    pl_file = spotify_dir / "playlists.json"
    if pl_file.exists():
        data = json.loads(pl_file.read_text())
        for pl in data.get("playlists", []):
            tracks = [Track.from_dict(t) for t in pl.get("tracks", [])]
            sources.append((pl.get("name", "playlist"), pl.get("id", ""), tracks))
    saved_file = spotify_dir / "saved.json"
    if saved_file.exists():
        data = json.loads(saved_file.read_text())
        tracks = [Track.from_dict(t) for t in data.get("tracks", [])]
        if tracks:
            sources.append(("Liked Songs", "liked", tracks))
    return sources


def _extinf(spotify: Track, library: Track) -> str:
    secs = round(library.duration_ms / 1000) if library.duration_ms else -1
    return f"#EXTINF:{secs},{spotify.display}"


def _resolve_path(library: Track, mapper: PathMapper, relative_to: Optional[Path]) -> Optional[str]:
    path = library.path
    if not path:
        return None
    path = mapper.map(path)
    if relative_to is not None:
        try:
            path = os.path.relpath(path, relative_to)
        except ValueError:
            pass
    return path


def build_playlists(
    spotify_dir: Path,
    results: list[MatchResult],
    out_dir: Path,
    path_maps: Optional[list[str]] = None,
    relative_to: Optional[Path] = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    mapper = PathMapper.parse(path_maps)
    index = _match_index(results)

    used: set[str] = set()
    written: dict[str, dict] = {}

    for name, ident, tracks in load_export_sources(spotify_dir):
        lines = ["#EXTM3U"]
        matched = 0
        for sp in tracks:
            lib = index.get(sp.source_id)
            if lib is None:
                continue
            target = _resolve_path(lib, mapper, relative_to)
            if not target:
                continue
            lines.append(_extinf(sp, lib))
            lines.append(target)
            matched += 1

        # Collision-safe filename: distinct playlists keep distinct files.
        base = _safe_filename(name)
        filename = f"{base}.m3u8"
        if filename in used:
            filename = f"{base}-{(ident or 'x')[:8]}.m3u8"
        counter = 2
        while filename in used:
            filename = f"{base}-{(ident or 'x')[:8]}-{counter}.m3u8"
            counter += 1
        used.add(filename)

        (out_dir / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")
        written[filename] = {"matched": matched, "total": len(tracks)}

    return {"playlists_written": len(written), "files": written}
