from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from apigrunn import ApiGrunn, AsyncApiGrunn, FetchError
from apigrunn.tracks import TRACKS

from .conftest import FakeSite


@pytest.fixture
def client(tmp_path: Path, site: FakeSite) -> ApiGrunn:
    with ApiGrunn(db_path=tmp_path / "apigrunn.db", transport=site.transport) as c:
        yield c


def _dead_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network is down", request=request)

    return httpx.MockTransport(handler)


def test_refresh_stores_every_page(client: ApiGrunn, site: FakeSite) -> None:
    result = client.refresh()
    assert result.fetched == 8
    assert result.not_modified == 0
    assert result.errors == []
    assert result.talks == 66
    assert sorted(site.requests) == sorted(TRACKS)
    # What landed in the cache is the raw HTML, nothing derived.
    pages = client.pages()
    assert sorted(pages) == sorted(TRACKS)
    assert pages["tech"].lstrip().startswith("<!DOCTYPE html>")


def test_second_refresh_is_all_304s(client: ApiGrunn, site: FakeSite) -> None:
    client.refresh()
    site.reset()
    result = client.refresh()
    assert result.not_modified == 8
    assert result.fetched == 0
    assert result.talks == 66  # the stored HTML survived the 304s
    assert len(client.talks()) == 66


def test_force_refresh_ignores_validators(client: ApiGrunn) -> None:
    client.refresh()
    result = client.refresh(force=True)
    assert result.fetched == 8
    assert result.talks == 66
    assert len(client.talks()) == 66


def test_queries_refresh_on_first_use(client: ApiGrunn, site: FakeSite) -> None:
    assert len(client.talks()) == 66
    assert len(site.requests) == 8


def test_queries_do_not_refetch_within_the_ttl(client: ApiGrunn, site: FakeSite) -> None:
    client.talks()
    site.reset()
    client.talks()
    client.years()
    client.tracks()
    assert site.requests == []


def test_expired_ttl_triggers_a_conditional_refresh(tmp_path: Path, site: FakeSite) -> None:
    with ApiGrunn(db_path=tmp_path / "a.db", transport=site.transport, ttl=timedelta(0)) as c:
        c.talks()
        site.reset()
        c.talks()
    assert sorted(site.requests) == sorted(TRACKS)


def test_auto_refresh_off_never_touches_the_network(tmp_path: Path, site: FakeSite) -> None:
    with ApiGrunn(db_path=tmp_path / "a.db", transport=site.transport, auto_refresh=False) as c:
        assert c.talks() == []
        assert site.requests == []
        c.refresh()
        site.reset()
        assert len(c.talks()) == 66
        assert site.requests == []


def test_the_cache_survives_the_client(tmp_path: Path, site: FakeSite) -> None:
    db = tmp_path / "a.db"
    with ApiGrunn(db_path=db, transport=site.transport) as c:
        c.refresh()
    # A second client with no working network still answers from the cache.
    with ApiGrunn(db_path=db, transport=_dead_transport(), auto_refresh=False) as c:
        assert len(c.talks()) == 66
        assert c.years() == [2025, 2024, 2023]


def test_filters_and_lookups(client: ApiGrunn) -> None:
    assert len(client.talks(year=2024)) == 27
    assert len(client.talks(track="healthcare")) == 6
    assert len(client.talks(year=2023, track="healthcare")) == 1
    assert [t.title for t in client.talks(search="tractor")] == [
        "We Put YOLO on a Tractor — What could go wrong?"
    ]
    assert client.talks(search="RAG")  # description text
    assert client.talks(search="berco beute")  # speaker, case-insensitive
    talk = client.talk("ys__Y1mICwo")
    assert talk is not None
    assert len(talk.tracks) == 4
    assert client.talk("does-not-exist") is None


def test_tracks_report_counts(client: ApiGrunn) -> None:
    tracks = client.tracks()
    assert [t.slug for t in tracks] == list(TRACKS)
    by_slug = {t.slug: t for t in tracks}
    assert by_slug["tech"].talk_count == 66
    assert by_slug["healthcare"].talk_count == 6
    assert by_slug["healthcare"].name == "Healthcare & Life Sciences"
    assert by_slug["healthcare"].url == "https://www.aigrunn.org/healthcare"


def test_unknown_track_is_rejected(client: ApiGrunn) -> None:
    with pytest.raises(ValueError, match="unknown track 'nope'"):
        client.talks(track="nope")


def test_repeated_queries_parse_the_html_only_once(
    tmp_path: Path, site: FakeSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parsing all eight pages costs ~300ms, so it must not repeat per query."""
    import apigrunn.client as client_module

    parses: list[str] = []
    real = client_module.parse_track_page

    def counting(html: str, track: str):
        parses.append(track)
        return real(html, track)

    monkeypatch.setattr(client_module, "parse_track_page", counting)

    with ApiGrunn(db_path=tmp_path / "a.db", transport=site.transport) as c:
        c.refresh()
        assert sorted(parses) == sorted(TRACKS)  # once each, during the refresh
        parses.clear()

        c.talks()
        c.talks(year=2024)
        c.talks(track="healthcare")
        c.years()
        c.tracks()
        c.talk("ys__Y1mICwo")
        assert parses == []  # every one of those was answered from the memo


def test_changed_html_invalidates_the_memo(tmp_path: Path, site: FakeSite) -> None:
    with ApiGrunn(db_path=tmp_path / "a.db", transport=site.transport) as c:
        assert len(c.talks(track="healthcare")) == 6
        site.override["healthcare"] = site.html("healthcare").replace(
            'class="talk-card"', 'class="talk-card-withdrawn"', 2
        )
        site.etag_suffix = "v2"
        c.refresh()
        assert len(c.talks(track="healthcare")) == 4


def test_a_parser_fix_applies_without_recrawling(client: ApiGrunn, site: FakeSite) -> None:
    """The HTML is the cache, so re-parsing needs no network at all."""
    client.refresh()
    site.reset()
    client._parsed.clear()  # as a new process, or a new library version, would
    assert len(client.talks()) == 66
    assert site.requests == []


def test_one_failing_page_does_not_sink_the_refresh(tmp_path: Path, site: FakeSite) -> None:
    site.fail = {"healthcare"}
    with ApiGrunn(db_path=tmp_path / "a.db", transport=site.transport) as c:
        result = c.refresh()
        assert result.fetched == 7
        assert [p.track for p in result.errors] == ["healthcare"]
        assert len(c.talks()) == 66  # /tech still carries every talk
        assert c.talks(track="healthcare") == []


def test_a_failed_page_is_retried_rather_than_304d(tmp_path: Path, site: FakeSite) -> None:
    site.fail = {"healthcare"}
    with ApiGrunn(db_path=tmp_path / "a.db", transport=site.transport) as c:
        c.refresh()
        site.fail = set()
        result = c.refresh()
        assert [p.track for p in result.pages if p.status == "fetched"] == ["healthcare"]
        assert len(c.talks(track="healthcare")) == 6


def test_total_failure_raises(tmp_path: Path, site: FakeSite) -> None:
    site.fail = set(TRACKS)
    with (
        ApiGrunn(db_path=tmp_path / "a.db", transport=site.transport) as c,
        pytest.raises(FetchError, match="every aigrunn.org track page failed"),
    ):
        c.refresh()


def test_unreadable_page_does_not_break_the_others(tmp_path: Path, site: FakeSite) -> None:
    broken = '<div class="talks-year-group"><a class="talk-card" href="/nope"></a></div>'
    site.override["healthcare"] = broken
    with ApiGrunn(db_path=tmp_path / "a.db", transport=site.transport) as c:
        result = c.refresh()
        assert result.fetched == 8
        assert next(p for p in result.pages if p.track == "healthcare").cards == 0
        assert len(c.talks()) == 66
        assert c.talks(track="healthcare") == []


def test_a_talk_removed_from_the_site_disappears(tmp_path: Path, site: FakeSite) -> None:
    with ApiGrunn(db_path=tmp_path / "a.db", transport=site.transport) as c:
        c.refresh()
        assert c.talk("tP_kg20VAho") is not None
        site.override = {t: site.html(t).replace("tP_kg20VAho", "REPLACED-ID") for t in TRACKS}
        site.etag_suffix = "v2"
        c.refresh()
        assert c.talk("tP_kg20VAho") is None
        assert c.talk("REPLACED-ID") is not None


# ------------------------------------------------------------------ async


async def test_async_client_matches_the_sync_one(tmp_path: Path, site: FakeSite) -> None:
    async with AsyncApiGrunn(db_path=tmp_path / "a.db", transport=site.transport) as c:
        result = await c.refresh()
        assert result.fetched == 8
        assert result.talks == 66
        assert len(await c.talks()) == 66
        assert len(await c.talks(year=2024)) == 27
        assert await c.years() == [2025, 2024, 2023]
        assert (await c.talk("ys__Y1mICwo")).title
        assert [t.slug for t in await c.tracks()] == list(TRACKS)
        assert sorted(await c.pages()) == sorted(TRACKS)


async def test_async_queries_auto_refresh(tmp_path: Path, site: FakeSite) -> None:
    async with AsyncApiGrunn(db_path=tmp_path / "a.db", transport=site.transport) as c:
        assert len(await c.talks()) == 66
        assert sorted(site.requests) == sorted(TRACKS)
        site.reset()
        await c.talks()
        assert site.requests == []


async def test_async_second_refresh_is_all_304s(tmp_path: Path, site: FakeSite) -> None:
    async with AsyncApiGrunn(db_path=tmp_path / "a.db", transport=site.transport) as c:
        await c.refresh()
        result = await c.refresh()
        assert result.not_modified == 8
        assert result.talks == 66
