"""The public clients: :class:`ApiGrunn` and :class:`AsyncApiGrunn`."""

from __future__ import annotations

import asyncio
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
from .parser import ParsedCard, parse_track_page
from .tracks import TRACKS, track_url

logger = logging.getLogger(__name__)

DEFAULT_TTL = timedelta(hours=24)


class _BaseClient:
    """Cache handling and refresh assembly shared by both clients.

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

    def _tracks(self) -> list[Track]:
        counts = self._cache.track_counts()
        return [
            Track(slug=slug, name=name, url=track_url(slug), talk_count=counts.get(slug, 0))
            for slug, name in TRACKS.items()
        ]

    # ------------------------------------------------------------- refresh

    def _store(self, results: Sequence[FetchResult]) -> RefreshResult:
        """Parse what came back and write it to the cache in one transaction."""
        pages: list[PageResult] = []
        cards: list[ParsedCard] = []
        refreshed: list[str] = []

        for result in results:
            if not result.ok:
                pages.append(
                    PageResult(track=result.track, url=result.url, status="error", error=result.error)
                )
                continue

            if result.not_modified:
                # Bump fetched_at so the TTL reflects the check we just made.
                self._cache.record_page(
                    track=result.track, url=result.url, etag=None, last_modified=None
                )
                pages.append(PageResult(track=result.track, url=result.url, status="not_modified"))
                continue

            try:
                parsed = parse_track_page(result.html or "", result.track)
            except ParseError as exc:
                # One unreadable page must not take the archive down; /tech alone
                # carries every talk. Its stored tags and validators are left
                # alone, so it is retried rather than 304'd forever.
                logger.error("%s", exc)
                pages.append(
                    PageResult(track=result.track, url=result.url, status="error", error=str(exc))
                )
                continue

            cards.extend(parsed)
            refreshed.append(result.track)
            # Record validators only after a successful parse, for the same reason.
            self._cache.record_page(
                track=result.track,
                url=result.url,
                etag=result.etag,
                last_modified=result.last_modified,
            )
            pages.append(
                PageResult(track=result.track, url=result.url, status="fetched", cards=len(parsed))
            )

        if pages and all(page.status == "error" for page in pages):
            raise FetchError(
                "every aigrunn.org track page failed: "
                + "; ".join(f"/{p.track}: {p.error}" for p in pages)
            )

        applied = self._cache.apply(cards, refreshed_tracks=refreshed)
        return RefreshResult(
            pages=pages,
            talks=applied.total,
            talks_added=applied.added,
            talks_removed=applied.removed,
        )


def _check_track(track: str | None) -> None:
    if track is not None and track not in TRACKS:
        raise ValueError(f"unknown track {track!r}; expected one of {', '.join(TRACKS)}")


class ApiGrunn(_BaseClient):
    """Query aiGrunn conference data, caching the parsed talks in SQLite.

    >>> with ApiGrunn() as client:
    ...     talks = client.talks(year=2024, track="healthcare")

    Track pages are parsed once, when the cache is refreshed; queries are plain
    SQL against the stored talks. By default the first query crawls
    aigrunn.org and later queries are served locally until ``ttl`` expires.
    Pass ``auto_refresh=False`` to make queries pure cache reads.
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
        return self._cache.talks(year=year, track=track, search=search)

    def talk(self, video_id: str) -> Talk | None:
        """Return a single talk by its YouTube video id, or ``None``."""
        self._ensure_fresh()
        return self._cache.talk(video_id)

    def years(self) -> list[int]:
        """Return the conference years present in the archive, newest first."""
        self._ensure_fresh()
        return self._cache.years()

    def tracks(self) -> list[Track]:
        """Return the eight tracks with their cached talk counts."""
        self._ensure_fresh()
        return self._tracks()

    # ------------------------------------------------------------ refresh

    def refresh(self, *, force: bool = False) -> RefreshResult:
        """Re-crawl aigrunn.org and re-parse it into the cache.

        Uses conditional requests, so unchanged pages cost one ``304`` each.
        ``force=True`` ignores the cached validators and re-downloads
        everything, which is also how a parser fix reaches already-cached data.
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

    Track pages are fetched concurrently; SQLite work and parsing run in a
    worker thread so the event loop is never blocked.
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
        return await asyncio.to_thread(
            self._cache.talks, year=year, track=track, search=search
        )

    async def talk(self, video_id: str) -> Talk | None:
        await self._ensure_fresh()
        return await asyncio.to_thread(self._cache.talk, video_id)

    async def years(self) -> list[int]:
        await self._ensure_fresh()
        return await asyncio.to_thread(self._cache.years)

    async def tracks(self) -> list[Track]:
        await self._ensure_fresh()
        return await asyncio.to_thread(self._tracks)

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
