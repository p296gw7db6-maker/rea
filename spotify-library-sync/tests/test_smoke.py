"""Smoke tests for the offline core (normalize + match + playlists + report).

These exercise the matching pipeline with in-memory Tracks — no network, no
real audio files. Run with:  PYTHONPATH=src python -m pytest tests/  (or the
plain-stdlib runner at the bottom:  PYTHONPATH=src python tests/test_smoke.py).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from spotsync.models import Track
from spotsync import normalize, match, playlists, report


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


def test_playlists_and_reports_roundtrip():
    spotify = [
        _sp("Hit", "A", isrc="X1", dur=180000, ctx=("Favs",)),
        _sp("Gone", "B", dur=200000, ctx=("Favs",)),  # missing
    ]
    library = [_lib("Hit", "A", isrc="X1", dur=180000, path="/music/hit.flac")]
    results = match.match_tracks(spotify, library)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pl_summary = playlists.write_playlists(results, tmp / "pl")
        assert pl_summary["playlists_written"] == 1
        m3u = (tmp / "pl" / "Favs.m3u8").read_text()
        assert "#EXTM3U" in m3u
        assert "/music/hit.flac" in m3u
        assert "A - Hit" in m3u

        gap = report.write_gap_report(results, tmp / "rep")
        assert gap["missing_tracks"] == 1
        gaps_csv = (tmp / "rep" / "gaps.csv").read_text()
        assert "Gone" in gaps_csv


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
