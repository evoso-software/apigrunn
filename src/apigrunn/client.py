"""The public clients: :class:`ApiGrunn` and :class:`AsyncApiGrunn`."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

import httpx

from .cache import Cache
from .errors import FetchError, ParseError
from .fetcher import (
    DEFAULT_TIMEOUT,
    FetchResult,
    afetch_pages,
    build_async_client,
    build_client,
    fetch_pages,
)
from .models import PageResult, RefreshResult, Talk, Track
from .parser import ParsedCard, parse_track_page, talks_from_cards
from .tracks import TRACKS, track_url

logger = logging.getLogger(__name__)

DEFAULT_TTL = timedelta(hours=24)


class _BaseClient:
    """Cache handling, parsing and filtering shared by both clients.

    Everything here is synchronous and never touches the network, so the sync
    and async clients differ only in how they fetch and how they hand work to
    a thread.
    """

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        ttl: timedelta = DEFAULT_TTL,
        auto_refresh: bool = True,
    ) -> None:
        self._cache = Cache(db_path)
        self._ttl = ttl
        self._auto_refresh = auto_refresh
        # Parsing all eight pages costs ~300ms, so hold on to the result for
        # as long as the HTML behind it is unchanged. Keyed by content digest,
        # which makes the memo self-invalidating.
        self._parsed: dict[str, tuple[str, list[ParsedCard]]] = {}

    @property
    def db_path(self) -> Path:
        """Location of the SQLite cache backing this client."""
        return self._cache.path

    def _needs_refresh(self) -> bool:
        if self._cache.is_empty():
            return True
        if len(self._cache.page_meta()) < len(TRACKS):
            return True
        oldest = self._cache.oldest_fetch()
        return oldest is None or datetime.now(UTC) - oldest > self._ttl

    # --------------------------------------------------- parse on demand

    def _cards(self, track: str, html: str) -> list[ParsedCard]:
        digest = hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()
        cached = self._parsed.get(track)
        if cached is not None and cached[0] == digest:
            return cached[1]
        try:
            cards = parse_track_page(html, track)
        except ParseError as exc:
            # One unreadable page must not take the whole archive down; /tech
            # alone still carries every talk.
            logger.error("%s", exc)
            cards = []
        self._parsed[track] = (digest, cards)
        return cards

    def _all_talks(self) -> list[Talk]:
        """Parse every cached page and merge the cards into talks."""
        cards: list[ParsedCard] = []
        for track, page in self._cache.pages().items():
            cards.extend(self._cards(track, page.html))
        return talks_from_cards(cards)

    def _query(
        self,
        *,
        year: int | None = None,
        track: str | None = None,
        search: str | None = None,
    ) -> list[Talk]:
        talks = self._all_talks()
        if year is not None:
            talks = [t for t in talks if t.year == year]
        if track is not None:
            talks = [t for t in talks if track in t.tracks]
        if search:
            needle = search.casefold()
            talks = [
                t
                for t in talks
                if needle in t.title.casefold()
                or needle in t.description.casefold()
                or needle in t.speaker.casefold()
            ]
        return talks

    def _get(self, video_id: str) -> Talk | None:
        return next((t for t in self._all_talks() if t.video_id == video_id), None)

    def _years(self) -> list[int]:
        return sorted({t.year for t in self._all_talks() if t.year is not None}, reverse=True)

    def _tracks(self) -> list[Track]:
        talks = self._all_talks()
        counts: dict[str, int] = {}
        for talk in talks:
            for slug in talk.tracks:
                counts[slug] = counts.get(slug, 0) + 1
        return [
            Track(slug=slug, name=name, url=track_url(slug), talk_count=counts.get(slug, 0))
            for slug, name in TRACKS.items()
        ]

    # ------------------------------------------------------------- refresh

    def _store(self, results: Sequence[FetchResult]) -> RefreshResult:
        pages: list[PageResult] = []

        for result in results:
            if not result.ok:
                pages.append(
                    PageResult(track=result.track, url=result.url, status="error", error=result.error)
                )
                continue

            if result.not_modified:
                # Bump fetched_at so the TTL reflects the check we just made.
                self._cache.touch(result.track)
                pages.append(PageResult(track=result.track, url=result.url, status="not_modified"))
                continue

            self._cache.store(
                track=result.track,
                url=result.url,
                html=result.html or "",
                etag=result.etag,
                last_modified=result.last_modified,
            )
            pages.append(
                PageResult(
                    track=result.track,
                    url=result.url,
                    status="fetched",
                    cards=len(self._cards(result.track, result.html or "")),
                )
            )

        if pages and all(page.status == "error" for page in pages):
            raise FetchError(
                "every aigrunn.org track page failed: "
                + "; ".join(f"/{p.track}: {p.error}" for p in pages)
            )

        return RefreshResult(pages=pages, talks=len(self._all_talks()))


def _check_track(track: str | None) -> None:
    if track is not None and track not in TRACKS:
        raise ValueError(f"unknown track {track!r}; expected one of {', '.join(TRACKS)}")


class ApiGrunn(_BaseClient):
    """Query aiGrunn conference data, caching the crawled pages in SQLite.

    >>> with ApiGrunn() as client:
    ...     talks = client.talks(year=2024, track="healthcare")

    The cache stores the raw HTML of the eight track pages; talks are parsed
    out of it on demand. By default the first query crawls aigrunn.org and
    later queries are served locally until ``ttl`` expires. Pass
    ``auto_refresh=False`` to make queries pure cache reads.
    """

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        ttl: timedelta = DEFAULT_TTL,
        auto_refresh: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(db_path=db_path, ttl=ttl, auto_refresh=auto_refresh)
        self._http = build_client(timeout=timeout, transport=transport)

    # ------------------------------------------------------------- queries

    def talks(
        self,
        *,
        year: int | None = None,
        track: str | None = None,
        search: str | None = None,
    ) -> list[Talk]:
        """Return archived talks, newest year first.

        ``search`` is a case-insensitive substring match over title,
        description and speaker.
        """
        _check_track(track)
        self._ensure_fresh()
        return self._query(year=year, track=track, search=search)

    def talk(self, video_id: str) -> Talk | None:
        """Return a single talk by its YouTube video id, or ``None``."""
        self._ensure_fresh()
        return self._get(video_id)

    def years(self) -> list[int]:
        """Return the conference years present in the archive, newest first."""
        self._ensure_fresh()
        return self._years()

    def tracks(self) -> list[Track]:
        """Return the eight tracks with their cached talk counts."""
        self._ensure_fresh()
        return self._tracks()

    def pages(self) -> dict[str, str]:
        """Return the raw cached HTML of each track page, keyed by track slug."""
        self._ensure_fresh()
        return {track: page.html for track, page in self._cache.pages().items()}

    # ------------------------------------------------------------ refresh

    def refresh(self, *, force: bool = False) -> RefreshResult:
        """Re-crawl aigrunn.org into the cache.

        Uses conditional requests, so unchanged pages cost one ``304`` each.
        ``force=True`` ignores the cached validators and re-downloads
        everything.
        """
        meta = {} if force else self._cache.page_meta()
        results = fetch_pages(TRACKS, meta=meta, client=self._http)
        return self._store(results)

    def _ensure_fresh(self) -> None:
        if self._auto_refresh and self._needs_refresh():
            self.refresh()

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._http.close()
        self._cache.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class AsyncApiGrunn(_BaseClient):
    """Async twin of :class:`ApiGrunn` with the same method names.

    >>> async with AsyncApiGrunn() as client:
    ...     talks = await client.talks(year=2024)

    Track pages are fetched concurrently; SQLite reads and HTML parsing run in
    a worker thread so the event loop is never blocked.
    """

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        ttl: timedelta = DEFAULT_TTL,
        auto_refresh: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(db_path=db_path, ttl=ttl, auto_refresh=auto_refresh)
        self._http = build_async_client(timeout=timeout, transport=transport)

    # ------------------------------------------------------------- queries

    async def talks(
        self,
        *,
        year: int | None = None,
        track: str | None = None,
        search: str | None = None,
    ) -> list[Talk]:
        _check_track(track)
        await self._ensure_fresh()
        return await asyncio.to_thread(self._query, year=year, track=track, search=search)

    async def talk(self, video_id: str) -> Talk | None:
        await self._ensure_fresh()
        return await asyncio.to_thread(self._get, video_id)

    async def years(self) -> list[int]:
        await self._ensure_fresh()
        return await asyncio.to_thread(self._years)

    async def tracks(self) -> list[Track]:
        await self._ensure_fresh()
        return await asyncio.to_thread(self._tracks)

    async def pages(self) -> dict[str, str]:
        await self._ensure_fresh()
        pages = await asyncio.to_thread(self._cache.pages)
        return {track: page.html for track, page in pages.items()}

    # ------------------------------------------------------------ refresh

    async def refresh(self, *, force: bool = False) -> RefreshResult:
        meta = {} if force else await asyncio.to_thread(self._cache.page_meta)
        results = await afetch_pages(TRACKS, meta=meta, client=self._http)
        return await asyncio.to_thread(self._store, results)

    async def _ensure_fresh(self) -> None:
        if self._auto_refresh and await asyncio.to_thread(self._needs_refresh):
            await self.refresh()

    # ------------------------------------------------------------ lifecycle

    async def aclose(self) -> None:
        await self._http.aclose()
        self._cache.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
