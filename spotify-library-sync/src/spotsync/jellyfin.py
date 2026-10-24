"""Jellyfin integration (additive, preview-first).

Two jobs:
  * scan    — read the Audio items from a Jellyfin server into a library index
              (same shape as `library_scan`), so the generic matcher can run.
  * push    — create Jellyfin playlists from match results. PREVIEW by default
              (fully offline: no server call); writes only with apply=True, and
              only ever *creates* playlists — it never edits or deletes.

Endpoints/schemas follow the official Jellyfin OpenAPI spec (12.3.0):
  GET  /Users                       -> [UserDto{Name,Id}]
  GET  /Items?recursive&includeItemTypes=Audio&fields=Path -> {Items,TotalRecordCount}
  POST /Playlists  (CreatePlaylistDto) -> {Id}
Jellyfin exposes no ISRC, so matching against it is text/duration based.

UNVERIFIED: the HTTP paths below are written to the spec but have not been run
against a live server in this environment. The auth header value format
(`Authorization: MediaBrowser Token="..."`) and comma-joined array params are
the established Jellyfin conventions; if a real server rejects them, that is the
first thing to adjust.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from .models import Track
from .match import MatchResult
from .playlists import load_export_sources

# 1 tick = 100 ns; 10_000 ticks = 1 ms.
_TICKS_PER_MS = 10_000


def ticks_to_ms(ticks: Optional[int]) -> Optional[int]:
    if not ticks:
        return None
    return int(ticks / _TICKS_PER_MS)


def item_to_track(item: dict) -> Track:
    artists = item.get("Artists") or []
    if not artists and item.get("AlbumArtist"):
        artists = [item["AlbumArtist"]]
    return Track(
        title=item.get("Name", ""),
        artists=[a for a in artists if a],
        album=item.get("Album", "") or "",
        duration_ms=ticks_to_ms(item.get("RunTimeTicks")),
        isrc=None,  # Jellyfin does not expose ISRC
        source="jellyfin",
        source_id=item.get("Id", ""),
        path=item.get("Path"),
    )


class JellyfinClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        if not base_url or not api_key:
            raise RuntimeError("Jellyfin base URL and API key are required.")
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f'MediaBrowser Token="{self.api_key}"',
            "Accept": "application/json",
        }

    @staticmethod
    def _encode_params(params: dict) -> str:
        pairs: dict[str, str] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                pairs[key] = "true" if value else "false"
            elif isinstance(value, (list, tuple)):
                pairs[key] = ",".join(str(v) for v in value)
            else:
                pairs[key] = str(value)
        return urllib.parse.urlencode(pairs)

    def _get(self, path: str, **params) -> dict:
        url = f"{self.base}{path}"
        query = self._encode_params(params)
        if query:
            url += "?" + query
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode())

    def _post_json(self, path: str, body: dict) -> dict:
        data = json.dumps(body).encode()
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.base}{path}", data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}

    def resolve_user_id(self, user_name: Optional[str] = None) -> str:
        users = self._get("/Users")
        if not isinstance(users, list) or not users:
            raise RuntimeError("Jellyfin returned no users.")
        names = [u.get("Name") for u in users]
        if user_name:
            for u in users:
                if (u.get("Name") or "").lower() == user_name.lower():
                    return u.get("Id", "")
            raise RuntimeError(f"User '{user_name}' not found. Available: {names}")
        if len(users) == 1:
            return users[0].get("Id", "")
        raise RuntimeError(f"Multiple users found; pass --user. Available: {names}")

    def iter_audio(self, user_id: Optional[str] = None, page: int = 500) -> Iterator[dict]:
        start = 0
        while True:
            res = self._get(
                "/Items",
                recursive=True,
                includeItemTypes=["Audio"],
                fields=["Path"],
                userId=user_id,
                startIndex=start,
                limit=page,
                enableImages=False,
            )
            items = res.get("Items", [])
            for item in items:
                yield item
            start += len(items)
            total = res.get("TotalRecordCount")
            if not items or (total is not None and start >= total):
                break

    def create_playlist(self, name: str, ids: list[str], user_id: str) -> dict:
        body = {
            "Name": name,
            "Ids": ids,
            "UserId": user_id,
            "MediaType": "Audio",
            "IsPublic": False,
        }
        return self._post_json("/Playlists", body)


def scan(client: JellyfinClient, out_file: Path, user_id: Optional[str] = None) -> dict:
    tracks = [item_to_track(item) for item in client.iter_audio(user_id=user_id)]
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps(
            {"root": "jellyfin", "tracks": [t.to_dict() for t in tracks]},
            indent=2,
            ensure_ascii=False,
        )
    )
    return {"items": len(tracks), "index": str(out_file)}


@dataclass
class PlaylistPlan:
    name: str
    spotify_id: str
    item_ids: list[str] = field(default_factory=list)  # ordered Jellyfin item ids

    @property
    def matched(self) -> int:
        return len(self.item_ids)


def plan_playlists(spotify_dir: Path, results: list[MatchResult]) -> list[tuple[PlaylistPlan, int]]:
    """Build an ordered Jellyfin playlist plan per Spotify playlist.

    Returns (plan, total_spotify_tracks) pairs. Pure/offline: it only reads the
    export files and the match results (whose library side carries Jellyfin ids).
    """
    index: dict[str, str] = {}
    for result in results:
        if result.library is not None and result.spotify.source_id and result.library.source_id:
            index.setdefault(result.spotify.source_id, result.library.source_id)

    plans: list[tuple[PlaylistPlan, int]] = []
    for name, ident, tracks in load_export_sources(spotify_dir):
        item_ids = [index[sp.source_id] for sp in tracks if sp.source_id in index]
        plans.append((PlaylistPlan(name=name, spotify_id=ident, item_ids=item_ids), len(tracks)))
    return plans


def push(
    plans: list[tuple[PlaylistPlan, int]],
    apply: bool,
    client: Optional[JellyfinClient] = None,
    user_id: Optional[str] = None,
    name_prefix: str = "",
) -> dict:
    """Preview (apply=False) or create (apply=True) the planned playlists.

    Creation is additive only: it calls POST /Playlists and never edits or
    deletes. Re-running with apply=True creates *additional* playlists — this
    tool deliberately does not mutate existing ones.
    """
    if apply and (client is None or not user_id):
        raise RuntimeError("apply=True requires a Jellyfin client and a resolved user id.")

    report: list[dict] = []
    for plan, total in plans:
        full_name = f"{name_prefix}{plan.name}"
        entry: dict = {
            "name": full_name,
            "matched": plan.matched,
            "total": total,
            "created": False,
            "playlist_id": None,
        }
        if plan.matched == 0:
            entry["skipped"] = "no matched items"
        elif apply:
            result = client.create_playlist(full_name, plan.item_ids, user_id)  # type: ignore[union-attr]
            entry["created"] = True
            entry["playlist_id"] = result.get("Id")
        report.append(entry)

    return {
        "applied": apply,
        "created": sum(1 for e in report if e["created"]),
        "would_create": sum(1 for e in report if e["matched"] > 0 and not e["created"]),
        "playlists": report,
    }
