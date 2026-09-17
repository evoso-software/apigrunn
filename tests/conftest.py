from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from apigrunn.tracks import TRACKS

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_html(track: str) -> str:
    return (FIXTURES / f"{track}.html").read_text(encoding="utf-8")


@pytest.fixture
def tech_html() -> str:
    return fixture_html("tech")


class FakeSite:
    """An in-memory aigrunn.org serving the saved fixtures.

    Tracks how many times each page was requested and honours
    ``If-None-Match`` so the conditional-request path is exercised offline.
    """

    def __init__(self, *, etag_suffix: str = "v1") -> None:
        self.requests: list[str] = []
        self.etag_suffix = etag_suffix
        self.fail: set[str] = set()
        #: Serve this HTML for a track instead of its fixture.
        self.override: dict[str, str] = {}

    def etag(self, track: str) -> str:
        return f'"{track}-{self.etag_suffix}"'

    def html(self, track: str) -> str:
        return self.override.get(track) or fixture_html(track)

    def handle(self, request: httpx.Request) -> httpx.Response:
        track = request.url.path.lstrip("/")
        self.requests.append(track)

        if track in self.fail:
            return httpx.Response(503, text="nope")
        if track not in TRACKS:
            return httpx.Response(404, text="not found")

        etag = self.etag(track)
        if request.headers.get("If-None-Match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(
            200,
            text=self.html(track),
            headers={
                "ETag": etag,
                "Last-Modified": "Mon, 14 Sep 2026 08:01:00 GMT",
                "Content-Type": "text/html; charset=utf-8",
            },
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def reset(self) -> None:
        self.requests.clear()


@pytest.fixture
def site() -> FakeSite:
    return FakeSite()
