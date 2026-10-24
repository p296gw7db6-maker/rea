"""Normalization + similarity helpers for matching.

Matching Spotify metadata against ripped/purchased files is fuzzy by nature:
"Song (Remastered 2011)" vs "Song", "Artist feat. X" vs "Artist", accents,
punctuation, casing. We derive stable keys and a similarity score so the
matcher can go from exact (ISRC) down to best-effort fuzzy.

Pure stdlib — no external fuzzy-matching dependency.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_FEAT_RE = re.compile(r"\b(feat\.?|featuring|ft\.?|with)\b.*", re.IGNORECASE)
_BRACKETS_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_DASH_SUFFIX_RE = re.compile(r"\s[-–—]\s.*$")  # " - Remastered", " – Live at ..."
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _collapse(text: str) -> str:
    text = _NON_ALNUM_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def normalize_title(title: str, aggressive: bool = True) -> str:
    """Normalize a title. aggressive=True drops bracketed/dash qualifiers
    (Remaster/Live/Remix tags) that routinely differ between Spotify and rips."""
    t = strip_accents(title or "").lower()
    if aggressive:
        t = _BRACKETS_RE.sub(" ", t)
        t = _DASH_SUFFIX_RE.sub(" ", t)
    return _collapse(t)


def normalize_artist(artist: str) -> str:
    a = strip_accents(artist or "").lower()
    a = _FEAT_RE.sub(" ", a)
    return _collapse(a)


def match_key(artist: str, title: str, aggressive: bool = True) -> str:
    """Composite key used for exact-bucket lookups."""
    return f"{normalize_artist(artist)}|{normalize_title(title, aggressive)}"


def tokens(text: str, min_len: int = 3) -> set[str]:
    return {tok for tok in normalize_title(text).split() if len(tok) >= min_len}


def ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def similarity(artist_a: str, title_a: str, artist_b: str, title_b: str) -> float:
    """Weighted title/artist similarity in [0, 1]. Title dominates because
    library artist tags are noisier (compilations, 'Various Artists', etc.)."""
    title_sim = ratio(normalize_title(title_a), normalize_title(title_b))
    artist_sim = ratio(normalize_artist(artist_a), normalize_artist(artist_b))
    return 0.7 * title_sim + 0.3 * artist_sim
