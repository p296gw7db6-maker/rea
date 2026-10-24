"""Export playlists + saved tracks from Spotify as metadata (read-only).

Uses the official Web API. Handles pagination, 401 (one token refresh) and
429 (Retry-After) transparently. The `fields` query parameter keeps payloads —
and therefore network + memory cost — small.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator

from .config import Config
from .models import Track
from . import spotify_auth

_API = "https://api.spotify.com/v1"
_PLAYLIST_FIELDS = (
    "items(track(id,name,duration_ms,external_ids(isrc),"
    "artists(name),album(name))),next"
)


class SpotifyClient:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._token = spotify_auth.get_access_token(cfg)

    def _request(self, url: str, _retried_auth: bool = False) -> dict:
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as err:
            if err.code == 401 and not _retried_auth:
                self._token = spotify_auth.get_access_token(self._cfg)
                return self._request(url, _retried_auth=True)
            if err.code == 429:
                retry_after = int(err.headers.get("Retry-After", "2"))
                time.sleep(min(retry_after, 30) + 1)
                return self._request(url, _retried_auth=_retried_auth)
            raise

    def get(self, path: str, **params) -> dict:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        return self._request(f"{_API}{path}{query}")

    def paginate(self, path: str, **params) -> Iterator[dict]:
        """Yield each `items` element across all pages following `next`."""
        page = self.get(path, **params)
        while page is not None:
            for item in page.get("items", []):
                yield item
            nxt = page.get("next")
            page = self._request(nxt) if nxt else None


def _track_from_api(obj: dict, context: list[str]) -> Track | None:
    if not obj or obj.get("id") is None:
        return None  # local/unavailable tracks have no id
    return Track(
        title=obj.get("name", ""),
        artists=[a.get("name", "") for a in obj.get("artists", []) if a.get("name")],
        album=(obj.get("album") or {}).get("name", ""),
        duration_ms=obj.get("duration_ms"),
        isrc=(obj.get("external_ids") or {}).get("isrc"),
        source="spotify",
        source_id=obj.get("id", ""),
        context=list(context),
    )


def export(cfg: Config, out_dir: Path) -> dict:
    """Fetch user playlists + saved tracks. Returns a summary dict and writes
    `playlists.json` and `saved.json` under out_dir."""
    client = SpotifyClient(cfg)
    me = client.get("/me")
    user_id = me.get("id", "?")
    out_dir.mkdir(parents=True, exist_ok=True)

    playlists: list[dict] = []
    for pl in client.paginate("/me/playlists", limit=50):
        pid = pl.get("id")
        name = pl.get("name", "")
        if not pid:
            continue
        tracks: list[Track] = []
        for item in client.paginate(f"/playlists/{pid}/tracks", limit=100, fields=_PLAYLIST_FIELDS):
            track = _track_from_api(item.get("track") or {}, context=[name])
            if track:
                tracks.append(track)
        playlists.append(
            {
                "id": pid,
                "name": name,
                "owner": (pl.get("owner") or {}).get("id", ""),
                "tracks": [t.to_dict() for t in tracks],
            }
        )
        print(f"  playlist: {name} ({len(tracks)} tracks)")

    saved: list[Track] = []
    for item in client.paginate("/me/tracks", limit=50):
        track = _track_from_api(item.get("track") or {}, context=["Liked Songs"])
        if track:
            saved.append(track)
    print(f"  liked songs: {len(saved)} tracks")

    (out_dir / "playlists.json").write_text(
        json.dumps({"user": user_id, "playlists": playlists}, indent=2, ensure_ascii=False)
    )
    (out_dir / "saved.json").write_text(
        json.dumps(
            {"user": user_id, "tracks": [t.to_dict() for t in saved]},
            indent=2,
            ensure_ascii=False,
        )
    )

    total = sum(len(p["tracks"]) for p in playlists) + len(saved)
    return {
        "user": user_id,
        "playlists": len(playlists),
        "playlist_tracks": sum(len(p["tracks"]) for p in playlists),
        "saved_tracks": len(saved),
        "total_track_rows": total,
    }


def load_all_spotify_tracks(spotify_dir: Path) -> list[Track]:
    """Flatten exported playlists + saved into a single de-dup-tagged list.
    Each row keeps its playlist context; the same track can appear in several."""
    rows: list[Track] = []
    pl_file = spotify_dir / "playlists.json"
    if pl_file.exists():
        data = json.loads(pl_file.read_text())
        for pl in data.get("playlists", []):
            for t in pl.get("tracks", []):
                rows.append(Track.from_dict(t))
    saved_file = spotify_dir / "saved.json"
    if saved_file.exists():
        data = json.loads(saved_file.read_text())
        for t in data.get("tracks", []):
            rows.append(Track.from_dict(t))
    return rows
