"""Smoke tests for the offline core (normalize + match + playlists + report).

These exercise the matching pipeline with in-memory Tracks — no network, no
real audio files. Run with:  PYTHONPATH=src python -m pytest tests/  (or the
plain-stdlib runner at the bottom:  PYTHONPATH=src python tests/test_smoke.py).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from spotsync.models import Track
from spotsync import normalize, match, playlists, report


def _write_export(spotify_dir: Path, named_playlists, saved=None):
    spotify_dir.mkdir(parents=True, exist_ok=True)
    pls = []
    for i, (name, tracks) in enumerate(named_playlists):
        pls.append({"id": f"pl{i}", "name": name, "tracks": [t.to_dict() for t in tracks]})
    (spotify_dir / "playlists.json").write_text(json.dumps({"user": "u", "playlists": pls}))
    (spotify_dir / "saved.json").write_text(
        json.dumps({"user": "u", "tracks": [t.to_dict() for t in (saved or [])]})
    )


def _sp(title, artist, isrc=None, dur=None, ctx=("Playlist A",)):
    return Track(title=title, artists=[artist], isrc=isrc, duration_ms=dur,
                 source="spotify", source_id="sp_" + title, context=list(ctx))


def _lib(title, artist, isrc=None, dur=None, path="/music/x.flac"):
    return Track(title=title, artists=[artist], isrc=isrc, duration_ms=dur,
                 source="library", source_id=path, path=path)


def test_normalize_drops_qualifiers():
    assert normalize.normalize_title("Song (Remastered 2011)") == "song"
    assert normalize.normalize_title("Track - Live at Wembley") == "track"
    assert normalize.normalize_artist("Artist feat. Someone") == "artist"
    assert normalize.strip_accents("Björk") == "Bjork"


def test_match_tiers():
    spotify = [
        _sp("Numb", "Linkin Park", isrc="USWB10300001", dur=186000),       # isrc
        _sp("In the End", "Linkin Park", dur=216000),                       # exact
        _sp("Crawling (Remastered)", "Linkin Park", dur=209000),           # normalized
        _sp("One Steep Closer", "Linkin Park", dur=217000),                # fuzzy (typo)
        _sp("Totally Missing", "Nobody", dur=100000),                       # none
    ]
    library = [
        _lib("Numb", "Linkin Park", isrc="USWB10300001", dur=186500, path="/music/numb.flac"),
        _lib("In the End", "Linkin Park", dur=216000, path="/music/end.flac"),
        _lib("Crawling", "Linkin Park", dur=209000, path="/music/crawl.flac"),
        _lib("One Step Closer", "Linkin Park", dur=217000, path="/music/osc.flac"),
    ]
    results = match.match_tracks(spotify, library)
    methods = {r.spotify.title: r.method for r in results}
    assert methods["Numb"] == "isrc"
    assert methods["In the End"] == "exact"
    assert methods["Crawling (Remastered)"] == "normalized"
    assert methods["One Steep Closer"] == "fuzzy"
    assert methods["Totally Missing"] == "none"

    summary = match.summarize(results)
    assert summary["matched"] == 4
    assert summary["unmatched"] == 1


def test_duration_gate_blocks_wrong_length():
    # Same text, very different length -> fuzzy should NOT match.
    spotify = [_sp("Intro", "Band", dur=20000)]
    library = [_lib("Intro", "Band", dur=240000, path="/music/intro.flac")]
    results = match.match_tracks(spotify, library)
    # exact key still matches here (text identical) -> that's fine; verify the
    # *fuzzy-only* path is gated by using a non-identical title.
    spotify2 = [_sp("Intro Reprise", "Band", dur=20000)]
    results2 = match.match_tracks(spotify2, library)
    assert results2[0].method == "none"


def test_path_mapper_preserves_subtree():
    mapper = playlists.PathMapper.parse(["/home/me/music=/srv/media"])
    assert mapper.map("/home/me/music/Artist/Album/01.flac") == "/srv/media/Artist/Album/01.flac"
    assert mapper.map("/other/x.flac") == "/other/x.flac"  # unmatched stays untouched
    # longest-prefix wins
    mapper2 = playlists.PathMapper.parse(["/a=/X", "/a/b=/Y"])
    assert mapper2.map("/a/b/c.flac") == "/Y/c.flac"


def test_playlists_and_reports_roundtrip():
    hit = _sp("Hit", "A", isrc="X1", dur=180000, ctx=("Favs",))
    gone = _sp("Gone", "B", dur=200000, ctx=("Favs",))  # missing locally
    library = [_lib("Hit", "A", isrc="X1", dur=180000, path="/music/hit.flac")]
    results = match.match_tracks([hit, gone], library)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        sp_dir = tmp / "spotify"
        _write_export(sp_dir, [("Favs", [hit, gone])])
        summary = playlists.build_playlists(
            sp_dir, results, tmp / "pl", path_maps=["/music=/srv/music"]
        )
        assert summary["playlists_written"] == 1
        assert summary["files"]["Favs.m3u8"] == {"matched": 1, "total": 2}
        m3u = (tmp / "pl" / "Favs.m3u8").read_text()
        assert "#EXTM3U" in m3u
        assert "/srv/music/hit.flac" in m3u  # path remapped onto server prefix
        assert "A - Hit" in m3u

        gap = report.write_gap_report(results, tmp / "rep")
        assert gap["missing_tracks"] == 1
        assert "Gone" in (tmp / "rep" / "gaps.csv").read_text()


def test_same_name_playlists_get_separate_files():
    t1 = _sp("S1", "A", isrc="I1", dur=1000)
    t2 = _sp("S2", "B", isrc="I2", dur=1000)
    library = [
        _lib("S1", "A", isrc="I1", dur=1000, path="/m/s1.flac"),
        _lib("S2", "B", isrc="I2", dur=1000, path="/m/s2.flac"),
    ]
    results = match.match_tracks([t1, t2], library)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        sp_dir = tmp / "spotify"
        _write_export(sp_dir, [("Chill", [t1]), ("Chill", [t2])])
        summary = playlists.build_playlists(sp_dir, results, tmp / "pl")
        assert summary["playlists_written"] == 2
        contents = {p.name: p.read_text() for p in (tmp / "pl").glob("*.m3u8")}
        assert len(contents) == 2
        assert any("/m/s1.flac" in c for c in contents.values())
        assert any("/m/s2.flac" in c for c in contents.values())
        # the two tracks must NOT end up merged into one file
        assert not any("/m/s1.flac" in c and "/m/s2.flac" in c for c in contents.values())


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
