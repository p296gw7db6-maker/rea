"""Shared data model.

A single normalized `Track` representation is used for both Spotify metadata
and the local library, so the matcher compares like with like. We only ever
hold *metadata* here (title/artist/album/duration/ISRC) — never audio.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Track:
    title: str
    artists: list[str] = field(default_factory=list)
    album: str = ""
    duration_ms: Optional[int] = None
    isrc: Optional[str] = None

    # Provenance — kept so we can always trace a row back to its origin.
    source: str = ""        # "spotify" | "library"
    source_id: str = ""     # spotify track id, or absolute local file path
    path: Optional[str] = None  # local file path (library tracks only)

    # Free-form extra context (e.g. which playlist a spotify track came from).
    context: list[str] = field(default_factory=list)

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    @property
    def display(self) -> str:
        who = ", ".join(self.artists) if self.artists else "?"
        return f"{who} - {self.title}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Track":
        known = {k: d.get(k) for k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        # Preserve explicit defaults for missing keys.
        if known.get("artists") is None:
            known["artists"] = []
        if known.get("context") is None:
            known["context"] = []
        return cls(**known)  # type: ignore[arg-type]
