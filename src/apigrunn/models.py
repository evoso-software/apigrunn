"""Public data models."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

_SPEAKER_SPLIT = re.compile(r"\s*(?:&|,| and )\s*")


class Talk(BaseModel):
    """A talk from a past aiGrunn conference.

    ``url`` points at the YouTube recording; ``video_id`` is its stable id and
    the primary key used by the cache. A talk can belong to several tracks, so
    ``tracks`` is a list of track slugs.
    """

    video_id: str
    title: str
    description: str = ""
    speaker: str = ""
    url: str
    year: int | None = None
    tracks: list[str] = Field(default_factory=list)
    thumbnail_url: str | None = None

    @property
    def speakers(self) -> list[str]:
        """``speaker`` split into individual names.

        The site publishes speakers as one free-text string ("A & B",
        "A, B and C"), so this is a best-effort convenience. ``speaker`` stays
        the source of truth.
        """
        if not self.speaker.strip():
            return []
        return [part for part in (p.strip() for p in _SPEAKER_SPLIT.split(self.speaker)) if part]

    def __str__(self) -> str:  # pragma: no cover - convenience only
        year = self.year or "????"
        return f"[{year}] {self.title}" + (f" — {self.speaker}" if self.speaker else "")


class Track(BaseModel):
    """One of the aiGrunn conference tracks."""

    slug: str
    name: str
    url: str
    talk_count: int = 0


class PageResult(BaseModel):
    """What happened to a single track page during a refresh."""

    track: str
    url: str
    status: Literal["fetched", "not_modified", "error"]
    cards: int = 0
    error: str | None = None


class RefreshResult(BaseModel):
    """Summary of a :meth:`~apigrunn.ApiGrunn.refresh` call."""

    pages: list[PageResult] = Field(default_factory=list)

    talks: int = 0
    """How many distinct talks the cache holds after this refresh."""

    talks_added: int = 0
    """Talks that were not in the cache before."""

    talks_removed: int = 0
    """Talks pruned because no track page lists them any more."""

    @property
    def fetched(self) -> int:
        return sum(1 for p in self.pages if p.status == "fetched")

    @property
    def not_modified(self) -> int:
        return sum(1 for p in self.pages if p.status == "not_modified")

    @property
    def errors(self) -> list[PageResult]:
        return [p for p in self.pages if p.status == "error"]

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return (
            f"RefreshResult(fetched={self.fetched}, not_modified={self.not_modified}, "
            f"errors={len(self.errors)}, talks={self.talks}, "
            f"added={self.talks_added}, removed={self.talks_removed})"
        )
