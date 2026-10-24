"""Scan a local music library and read tags into normalized `Track`s.

Reads metadata only (via mutagen): title, artist, album, duration and — where
the format carries it — ISRC. Files without tags fall back to the filename so
they still participate in matching.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

from .models import Track

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".m4b", ".aac", ".ogg", ".opus",
    ".wav", ".aiff", ".aif", ".wma", ".alac",
}

try:
    from mutagen import File as _MutagenFile
    from mutagen.easyid3 import EasyID3

    # Expose ISRC (ID3 TSRC frame) through the EasyID3 interface.
    try:
        EasyID3.RegisterTextKey("isrc", "TSRC")
    except Exception:
        pass
    _HAS_MUTAGEN = True
except Exception:  # pragma: no cover - import guard
    _MutagenFile = None  # type: ignore[assignment]
    _HAS_MUTAGEN = False


def _first(tags, key: str) -> str | None:
    if not tags:
        return None
    value = tags.get(key)
    if not value:
        return None
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else None
    return str(value)


def read_track(path: Path) -> Track | None:
    if not _HAS_MUTAGEN:
        raise RuntimeError("mutagen is not installed; run `pip install mutagen`.")
    try:
        audio = _MutagenFile(str(path), easy=True)
    except Exception:
        audio = None
    if audio is None:
        # Unsupported/corrupt: still index by filename so it can be matched.
        return Track(
            title=path.stem,
            artists=[],
            source="library",
            source_id=str(path),
            path=str(path),
        )

    tags = audio.tags
    title = _first(tags, "title") or path.stem
    artist = _first(tags, "artist") or ""
    album = _first(tags, "album") or ""
    isrc = _first(tags, "isrc")
    length = getattr(getattr(audio, "info", None), "length", None)
    duration_ms = int(length * 1000) if length else None

    return Track(
        title=title,
        artists=[artist] if artist else [],
        album=album,
        duration_ms=duration_ms,
        isrc=isrc,
        source="library",
        source_id=str(path),
        path=str(path),
    )


def iter_audio_files(root: Path) -> Iterator[Path]:
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                yield Path(dirpath) / name


def scan(root: Path, out_file: Path, progress_every: int = 500) -> dict:
    """Walk `root`, read tags, write a flat index JSON. Returns a summary."""
    root = root.expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"Library root is not a directory: {root}")

    tracks: list[Track] = []
    untagged = 0
    for count, path in enumerate(iter_audio_files(root), start=1):
        track = read_track(path)
        if track is not None:
            tracks.append(track)
            if not track.title or not track.artists:
                untagged += 1
        if count % progress_every == 0:
            print(f"  scanned {count} files...")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps(
            {"root": str(root), "tracks": [t.to_dict() for t in tracks]},
            indent=2,
            ensure_ascii=False,
        )
    )
    return {"files": len(tracks), "untagged_or_partial": untagged, "index": str(out_file)}


def load_library(index_file: Path) -> list[Track]:
    data = json.loads(index_file.read_text())
    return [Track.from_dict(t) for t in data.get("tracks", [])]
