"""Tests for the Jellyfin module's pure logic (no live server).

The HTTP client itself is not exercised here (it needs a real Jellyfin
instance); these cover tick/metadata conversion, plan building from an export +
match results, and the additive push in both preview and apply modes using a
fake client.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from spotsync.models import Track
from spotsync import match, jellyfin
from spotsync.jellyfin import ticks_to_ms, item_to_track


def _sp(title, artist, isrc, sid):
    return Track(title=title, artists=[artist], isrc=isrc, duration_ms=180000,
                 source="spotify", source_id=sid)


def _jf(title, artist, isrc, jid):
    return Track(title=title, artists=[artist], isrc=isrc, duration_ms=180000,
                 source="jellyfin", source_id=jid, path=f"/srv/{jid}.flac")


def _write_export(spotify_dir: Path, named_playlists):
    spotify_dir.mkdir(parents=True, exist_ok=True)
    pls = [{"id": f"pl{i}", "name": n, "tracks": [t.to_dict() for t in ts]}
           for i, (n, ts) in enumerate(named_playlists)]
    (spotify_dir / "playlists.json").write_text(json.dumps({"user": "u", "playlists": pls}))
    (spotify_dir / "saved.json").write_text(json.dumps({"user": "u", "tracks": []}))


class _FakeClient:
    def __init__(self):
        self.created: list[tuple] = []

    def create_playlist(self, name, ids, user_id):
        self.created.append((name, ids, user_id))
        return {"Id": "new_" + name}


def test_ticks_and_item_mapping():
    assert ticks_to_ms(1_800_000_000) == 180_000  # 180 s
    assert ticks_to_ms(None) is None
    track = item_to_track({
        "Id": "abc", "Name": "Song", "Artists": ["A"], "Album": "Alb",
        "RunTimeTicks": 1_800_000_000, "Path": "/srv/x.flac",
    })
    assert track.source == "jellyfin"
    assert track.source_id == "abc"
    assert track.duration_ms == 180_000
    assert track.path == "/srv/x.flac"
    assert track.isrc is None


def test_item_mapping_falls_back_to_album_artist():
    track = item_to_track({"Id": "z", "Name": "T", "AlbumArtist": "Band"})
    assert track.artists == ["Band"]


def test_plan_and_push_preview_then_apply():
    sp1 = _sp("Numb", "Linkin Park", "I1", "s1")
    sp2 = _sp("Faint", "Linkin Park", "I2", "s2")
    jf = [_jf("Numb", "Linkin Park", "I1", "J1"), _jf("Faint", "Linkin Park", "I2", "J2")]
    results = match.match_tracks([sp1, sp2], jf)
    assert all(r.method == "isrc" for r in results)  # matched via ISRC

    with tempfile.TemporaryDirectory() as tmp:
        sp_dir = Path(tmp) / "spotify"
        _write_export(sp_dir, [("Rock", [sp1, sp2])])
        plans = jellyfin.plan_playlists(sp_dir, results)
        assert len(plans) == 1
        plan, total = plans[0]
        assert plan.name == "Rock"
        assert plan.item_ids == ["J1", "J2"]  # order preserved, Jellyfin ids
        assert total == 2

        # Preview: offline, creates nothing.
        preview = jellyfin.push(plans, apply=False, name_prefix="Spotify • ")
        assert preview["applied"] is False
        assert preview["created"] == 0
        assert preview["would_create"] == 1
        assert preview["playlists"][0]["name"] == "Spotify • Rock"

        # Apply: uses the fake client, creates exactly one playlist.
        fake = _FakeClient()
        applied = jellyfin.push(plans, apply=True, client=fake, user_id="u1", name_prefix="Spotify • ")
        assert applied["created"] == 1
        assert fake.created == [("Spotify • Rock", ["J1", "J2"], "u1")]


def test_push_apply_requires_client():
    with tempfile.TemporaryDirectory() as tmp:
        sp_dir = Path(tmp) / "spotify"
        _write_export(sp_dir, [("X", [])])
        plans = jellyfin.plan_playlists(sp_dir, [])
        try:
            jellyfin.push(plans, apply=True)  # no client/user -> must raise
            raised = False
        except RuntimeError:
            raised = True
        assert raised


def _run_all():
    import traceback
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
                passed += 1
            except Exception:
                print(f"FAIL {name}")
                traceback.print_exc()
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
