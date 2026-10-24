"""Match Spotify tracks against the local library.

Tiered strategy, strongest first:
  1. isrc        — exact ISRC on both sides (authoritative)
  2. exact       — identical strict normalized artist+title key
  3. normalized  — identical aggressive key (ignores Remaster/Live/brackets)
  4. fuzzy       — best similarity above threshold, duration-gated

Fuzzy only runs for the leftovers and is bounded by a title-token inverted
index, so it stays practical on libraries of tens of thousands of files
instead of degrading to O(spotify x library).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from .models import Track
from . import normalize


@dataclass
class MatchResult:
    spotify: Track
    library: Optional[Track]
    method: str   # isrc | exact | normalized | fuzzy | none
    score: float

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "score": round(self.score, 4),
            "spotify": self.spotify.to_dict(),
            "library": self.library.to_dict() if self.library else None,
        }


class _LibraryIndex:
    def __init__(self, library: list[Track]):
        self.by_isrc: dict[str, Track] = {}
        self.by_strict: dict[str, Track] = {}
        self.by_aggressive: dict[str, Track] = {}
        self.token_index: dict[str, list[int]] = defaultdict(list)
        self.library = library
        self.duplicates: dict[str, list[str]] = defaultdict(list)

        for idx, track in enumerate(library):
            if track.isrc:
                self.by_isrc.setdefault(track.isrc.upper(), track)
            strict = normalize.match_key(track.primary_artist, track.title, aggressive=False)
            aggressive = normalize.match_key(track.primary_artist, track.title, aggressive=True)
            self.by_strict.setdefault(strict, track)
            self.by_aggressive.setdefault(aggressive, track)
            if track.path:
                self.duplicates[aggressive].append(track.path)
            for tok in normalize.tokens(track.title):
                self.token_index[tok].append(idx)

    def fuzzy_candidates(self, spotify: Track) -> set[int]:
        candidates: set[int] = set()
        for tok in normalize.tokens(spotify.title):
            candidates.update(self.token_index.get(tok, ()))
        return candidates


def _within_duration(a: Track, b: Track, tolerance_s: float) -> bool:
    if a.duration_ms is None or b.duration_ms is None:
        return True  # unknown duration should not veto a strong text match
    return abs(a.duration_ms - b.duration_ms) <= tolerance_s * 1000


def match_tracks(
    spotify_tracks: list[Track],
    library: list[Track],
    fuzzy_threshold: float = 0.82,
    duration_tolerance_s: float = 5.0,
) -> list[MatchResult]:
    index = _LibraryIndex(library)
    results: list[MatchResult] = []

    for sp in spotify_tracks:
        # 1. ISRC
        if sp.isrc and sp.isrc.upper() in index.by_isrc:
            results.append(MatchResult(sp, index.by_isrc[sp.isrc.upper()], "isrc", 1.0))
            continue
        # 2. strict key
        strict = normalize.match_key(sp.primary_artist, sp.title, aggressive=False)
        if strict in index.by_strict:
            results.append(MatchResult(sp, index.by_strict[strict], "exact", 0.95))
            continue
        # 3. aggressive key
        aggressive = normalize.match_key(sp.primary_artist, sp.title, aggressive=True)
        if aggressive in index.by_aggressive:
            results.append(MatchResult(sp, index.by_aggressive[aggressive], "normalized", 0.88))
            continue
        # 4. fuzzy over bounded candidate set
        best: Optional[Track] = None
        best_score = 0.0
        for cand_idx in index.fuzzy_candidates(sp):
            cand = index.library[cand_idx]
            score = normalize.similarity(
                sp.primary_artist, sp.title, cand.primary_artist, cand.title
            )
            if score > best_score and _within_duration(sp, cand, duration_tolerance_s):
                best_score = score
                best = cand
        if best is not None and best_score >= fuzzy_threshold:
            results.append(MatchResult(sp, best, "fuzzy", best_score))
        else:
            results.append(MatchResult(sp, None, "none", best_score))

    return results


def summarize(results: list[MatchResult]) -> dict:
    counts: dict[str, int] = defaultdict(int)
    for r in results:
        counts[r.method] += 1
    matched = sum(v for k, v in counts.items() if k != "none")
    return {
        "total": len(results),
        "matched": matched,
        "unmatched": counts.get("none", 0),
        "by_method": dict(counts),
    }


def write_matches(results: list[MatchResult], out_file: Path) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps(
            {"summary": summarize(results), "matches": [r.to_dict() for r in results]},
            indent=2,
            ensure_ascii=False,
        )
    )


def load_matches(matches_file: Path) -> list[MatchResult]:
    data = json.loads(matches_file.read_text())
    out: list[MatchResult] = []
    for row in data.get("matches", []):
        lib = row.get("library")
        out.append(
            MatchResult(
                spotify=Track.from_dict(row["spotify"]),
                library=Track.from_dict(lib) if lib else None,
                method=row.get("method", "none"),
                score=row.get("score", 0.0),
            )
        )
    return out
