"""Extraction of talk metadata from an aiGrunn track page.

This module is deliberately pure: it takes HTML text and returns records. No
network, no database. That keeps the fragile part of the library — the part
coupled to someone else's markup — fully testable against saved fixtures.

The markup it targets ("Blast from the past" on every track page) looks like::

    <div class="talks-year-group">
      <span class="talks-year-badge y2025">2025</span>
      <div class="talks-lane" id="talksLane2025">
        <a href="https://www.youtube.com/watch?v=tP_kg20VAho" class="talk-card"
           data-title="..." data-speaker="..." data-desc="...">
          <div class="talk-thumb"><img src="https://img.youtube.com/vi/..."/></div>
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from .errors import ParseError
from .models import Talk

logger = logging.getLogger(__name__)

_YEAR_CLASS = re.compile(r"\by(\d{4})\b")
_YEAR_TEXT = re.compile(r"(\d{4})")


@dataclass(frozen=True, slots=True)
class ParsedCard:
    """One talk as it appears on one track page."""

    video_id: str
    title: str
    speaker: str
    description: str
    url: str
    year: int | None
    thumbnail_url: str | None
    track: str


def parse_track_page(html: str, track_slug: str) -> list[ParsedCard]:
    """Parse every talk card on a track page.

    Individual malformed cards are skipped with a warning — a partial refresh
    is more useful than a hard failure on a site we do not control. A
    :class:`~apigrunn.errors.ParseError` is raised only when the page plainly
    contains talk cards but none could be extracted.
    """
    soup = BeautifulSoup(html, "html.parser")

    cards: list[ParsedCard] = []
    seen: set[str] = set()

    for group in soup.select("div.talks-year-group"):
        year = _group_year(group)
        for anchor in group.select("a.talk-card"):
            card = _parse_card(anchor, year=year, track_slug=track_slug)
            if card is None:
                continue
            if card.video_id in seen:
                logger.debug("duplicate talk %s on /%s, keeping first", card.video_id, track_slug)
                continue
            seen.add(card.video_id)
            cards.append(card)

    total_anchors = len(soup.select("a.talk-card"))
    if not cards:
        if total_anchors or "talk-card" in html:
            raise ParseError(
                f"/{track_slug}: found {total_anchors} talk cards but could not extract any; "
                "the aigrunn.org markup has probably changed"
            )
        logger.info("/%s: no talk archive on this page", track_slug)
    elif total_anchors > len(cards):
        # Cards outside a year group would otherwise vanish silently.
        logger.warning(
            "/%s: %d talk cards on the page but only %d inside a year group",
            track_slug,
            total_anchors,
            len(cards),
        )

    return cards


def _group_year(group: Tag) -> int | None:
    badge = group.select_one("span.talks-year-badge")
    if badge is None:
        return None

    for cls in badge.get("class") or ():
        match = _YEAR_CLASS.fullmatch(cls)
        if match:
            return int(match.group(1))

    match = _YEAR_TEXT.search(badge.get_text(strip=True))
    return int(match.group(1)) if match else None


def _parse_card(anchor: Tag, *, year: int | None, track_slug: str) -> ParsedCard | None:
    href = (anchor.get("href") or "").strip()
    video_id = youtube_video_id(href)
    title = _attr_or_text(anchor, "data-title", "div.talk-title")

    if not video_id or not title:
        logger.warning("/%s: skipping talk card without a video id or title (href=%r)", track_slug, href)
        return None

    thumb = anchor.select_one("div.talk-thumb img")
    thumbnail_url = (thumb.get("src") or "").strip() or None if thumb is not None else None

    return ParsedCard(
        video_id=video_id,
        title=title,
        speaker=_attr_or_text(anchor, "data-speaker", "div.talk-speaker"),
        description=_attr_or_text(anchor, "data-desc", "div.talk-desc"),
        url=href,
        year=year,
        thumbnail_url=thumbnail_url,
        track=track_slug,
    )


def youtube_video_id(url: str) -> str | None:
    """Extract the video id from a YouTube watch or short-form URL."""
    if not url:
        return None
    parts = urlparse(url)
    host = parts.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        return parts.path.lstrip("/").split("/")[0] or None
    if host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parts.path.startswith(("/embed/", "/v/", "/shorts/")):
            return parts.path.split("/")[2] or None
        values = parse_qs(parts.query).get("v")
        return values[0] if values else None
    return None


def _attr_or_text(tag: Tag, attribute: str, selector: str) -> str:
    """Read a ``data-*`` attribute, falling back to the rendered element.

    The fallback applies only when the attribute is *absent*. An attribute that
    is present but empty is taken at face value: the visible element is not a
    reliable substitute — one card carries ``data-speaker=""`` next to a
    ``<div class="talk-speaker">aiGrunn 2024</div>`` label, which is a year, not
    a speaker.
    """
    value = tag.get(attribute)
    if isinstance(value, str):
        return _clean(value)
    found = tag.select_one(selector)
    return _clean(found.get_text(" ")) if found is not None else ""


def _clean(value: str) -> str:
    """Collapse whitespace, including the non-breaking spaces the site uses."""
    return " ".join(value.replace("\xa0", " ").split())


def talks_from_cards(cards: Iterable[ParsedCard]) -> list[Talk]:
    """Merge cards scraped from several track pages into deduplicated talks.

    The same talk appears on every track page it belongs to, so cards are
    collapsed on ``video_id`` and their track slugs gathered into
    :attr:`Talk.tracks`. Results are ordered newest year first, then by title.
    """
    merged: dict[str, dict] = {}
    tracks: dict[str, set[str]] = {}

    for card in cards:
        tracks.setdefault(card.video_id, set()).add(card.track)
        current = merged.get(card.video_id)
        # Prefer a card that knows the year; the lanes are the only source of it.
        if current is None or (current["year"] is None and card.year is not None):
            merged[card.video_id] = {
                "video_id": card.video_id,
                "title": card.title,
                "description": card.description,
                "speaker": card.speaker,
                "url": card.url,
                "year": card.year,
                "thumbnail_url": card.thumbnail_url,
            }

    talks = [Talk(**fields, tracks=sorted(tracks[vid])) for vid, fields in merged.items()]
    talks.sort(key=lambda t: (-(t.year or 0), t.title.casefold()))
    return talks
