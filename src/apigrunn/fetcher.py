"""HTTP retrieval of aiGrunn track pages, sync and async.

aigrunn.org serves ``ETag`` and ``Last-Modified``, so refreshes send
conditional requests and a page that has not changed costs one ``304`` with an
empty body. ``robots.txt`` is ``Allow: /``; we still identify ourselves and keep
concurrency low.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import httpx

from ._version import __version__
from .cache import PageMeta
from .tracks import track_url

logger = logging.getLogger(__name__)

USER_AGENT = f"apigrunn/{__version__} (+https://github.com/apigrunn/apigrunn)"
DEFAULT_TIMEOUT = 10.0
DEFAULT_CONCURRENCY = 4


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Outcome of requesting one track page."""

    track: str
    url: str
    status_code: int | None
    html: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304

    @property
    def ok(self) -> bool:
        return self.error is None


def default_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}


def build_client(*, timeout: float = DEFAULT_TIMEOUT, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers=default_headers(), timeout=timeout, follow_redirects=True, transport=transport
    )


def build_async_client(
    *, timeout: float = DEFAULT_TIMEOUT, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=default_headers(), timeout=timeout, follow_redirects=True, transport=transport
    )


def _conditional_headers(meta: PageMeta | None) -> dict[str, str]:
    if meta is None:
        return {}
    headers = {}
    if meta.etag:
        headers["If-None-Match"] = meta.etag
    if meta.last_modified:
        headers["If-Modified-Since"] = meta.last_modified
    return headers


def _from_response(track: str, url: str, response: httpx.Response) -> FetchResult:
    if response.status_code == 304:
        logger.debug("/%s: not modified", track)
        return FetchResult(track=track, url=url, status_code=304)
    if response.status_code >= 400:
        return FetchResult(
            track=track,
            url=url,
            status_code=response.status_code,
            error=f"HTTP {response.status_code}",
        )
    return FetchResult(
        track=track,
        url=url,
        status_code=response.status_code,
        html=response.text,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
    )


def fetch_pages(
    tracks: Iterable[str],
    *,
    meta: Mapping[str, PageMeta] | None = None,
    client: httpx.Client,
) -> list[FetchResult]:
    """Fetch each track page in turn, reusing one connection."""
    meta = meta or {}
    results: list[FetchResult] = []
    for track in tracks:
        url = track_url(track)
        try:
            response = client.get(url, headers=_conditional_headers(meta.get(track)))
        except httpx.HTTPError as exc:
            logger.warning("/%s: request failed: %s", track, exc)
            results.append(FetchResult(track=track, url=url, status_code=None, error=str(exc)))
        else:
            results.append(_from_response(track, url, response))
    return results


async def afetch_pages(
    tracks: Iterable[str],
    *,
    meta: Mapping[str, PageMeta] | None = None,
    client: httpx.AsyncClient,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[FetchResult]:
    """Fetch the track pages concurrently, but politely."""
    meta = meta or {}
    semaphore = asyncio.Semaphore(concurrency)

    async def one(track: str) -> FetchResult:
        url = track_url(track)
        async with semaphore:
            try:
                response = await client.get(url, headers=_conditional_headers(meta.get(track)))
            except httpx.HTTPError as exc:
                logger.warning("/%s: request failed: %s", track, exc)
                return FetchResult(track=track, url=url, status_code=None, error=str(exc))
        return _from_response(track, url, response)

    return list(await asyncio.gather(*(one(track) for track in tracks)))
