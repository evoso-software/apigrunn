"""The cache stores parsed talks, so these tests are about writing, pruning
and querying rows — and about refusing a database this version cannot read."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apigrunn.cache import SCHEMA_VERSION, Cache, default_db_path
from apigrunn.errors import CacheVersionError
from apigrunn.parser import ParsedCard, parse_track_page
from apigrunn.tracks import TRACKS, track_url

from .conftest import fixture_html


def card(
    video_id: str,
    *,
    track: str = "tech",
    year: int | None = 2025,
    title: str = "T",
    thumbnail: str | None = None,
) -> ParsedCard:
    return ParsedCard(
        video_id=video_id,
        title=title,
        speaker="S",
        description="D",
        url=f"https://www.youtube.com/watch?v={video_id}",
        year=year,
        thumbnail_url=thumbnail,
        track=track,
    )


def all_fixture_cards() -> list[ParsedCard]:
    cards: list[ParsedCard] = []
    for track in TRACKS:
        cards.extend(parse_track_page(fixture_html(track), track))
    return cards


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    c = Cache(tmp_path / "apigrunn.db")
    yield c
    c.close()


def test_starts_empty(cache: Cache) -> None:
    assert cache.is_empty()
    assert cache.talks() == []
    assert cache.years() == []
    assert cache.track_counts() == {}
    assert cache.page_meta() == {}
    assert cache.oldest_fetch() is None


def test_apply_stores_talks_and_tracks(cache: Cache) -> None:
    result = cache.apply(all_fixture_cards(), refreshed_tracks=TRACKS)
    assert (result.added, result.removed, result.total) == (66, 0, 66)
    assert not cache.is_empty()
    assert cache.years() == [2025, 2024, 2023]
    assert len(cache.talks()) == 66
    assert cache.track_counts() == {
        "tech": 66,
        "business": 19,
        "government": 16,
        "education": 8,
        "sensible-ai": 13,
        "science": 9,
        "healthcare": 6,
        "hardware": 8,
    }
    assert sum(cache.track_counts().values()) == 145


def test_apply_is_idempotent(cache: Cache) -> None:
    cards = all_fixture_cards()
    cache.apply(cards, refreshed_tracks=TRACKS)
    result = cache.apply(cards, refreshed_tracks=TRACKS)
    assert (result.added, result.removed, result.total) == (0, 0, 66)
    assert len(cache.talks()) == 66
    assert sum(cache.track_counts().values()) == 145


def test_filters(cache: Cache) -> None:
    cache.apply(all_fixture_cards(), refreshed_tracks=TRACKS)
    assert len(cache.talks(year=2024)) == 27
    assert len(cache.talks(track="healthcare")) == 6
    assert len(cache.talks(year=2023, track="healthcare")) == 1
    assert [t.title for t in cache.talks(search="tractor")] == [
        "We Put YOLO on a Tractor — What could go wrong?"
    ]
    assert cache.talks(search="RAG")  # description text
    assert cache.talks(search="berco beute")  # speaker, case-insensitive


def test_talks_are_sorted_newest_year_then_title(cache: Cache) -> None:
    cache.apply(all_fixture_cards(), refreshed_tracks=TRACKS)
    keys = [(-(t.year or 0), t.title.casefold()) for t in cache.talks()]
    assert keys == sorted(keys)


def test_talk_lookup_carries_every_track(cache: Cache) -> None:
    cache.apply(all_fixture_cards(), refreshed_tracks=TRACKS)
    talk = cache.talk("ys__Y1mICwo")
    assert talk is not None
    assert talk.tracks == ["education", "government", "sensible-ai", "tech"]
    assert cache.talk("nope") is None


def test_refreshing_one_track_keeps_the_other_tags(cache: Cache) -> None:
    cache.apply(all_fixture_cards(), refreshed_tracks=TRACKS)
    # Re-apply only the healthcare page, as seven 304s would.
    healthcare = parse_track_page(fixture_html("healthcare"), "healthcare")
    result = cache.apply(healthcare, refreshed_tracks=["healthcare"])
    assert (result.added, result.removed, result.total) == (0, 0, 66)
    assert cache.track_counts()["tech"] == 66
    assert cache.track_counts()["healthcare"] == 6


def test_a_talk_on_no_page_is_pruned(cache: Cache) -> None:
    cache.apply([card("aaa"), card("bbb")], refreshed_tracks=["tech"])
    assert cache.track_counts() == {"tech": 2}

    result = cache.apply([card("aaa")], refreshed_tracks=["tech"])
    assert (result.added, result.removed, result.total) == (0, 1, 1)
    assert cache.talk("bbb") is None
    assert cache.track_counts() == {"tech": 1}


def test_a_talk_still_on_another_page_is_kept(cache: Cache) -> None:
    cache.apply(
        [card("aaa", track="tech"), card("aaa", track="science")],
        refreshed_tracks=["tech", "science"],
    )
    # /tech drops it, but /science still lists it.
    cache.apply([], refreshed_tracks=["tech"])
    talk = cache.talk("aaa")
    assert talk is not None
    assert talk.tracks == ["science"]


def test_pruning_cascades_to_track_tags(cache: Cache) -> None:
    cache.apply(
        [card("aaa", track="tech"), card("aaa", track="science")],
        refreshed_tracks=["tech", "science"],
    )
    cache.apply([], refreshed_tracks=["tech", "science"])
    assert cache.talks() == []
    assert cache.track_counts() == {}


def test_year_is_never_overwritten_with_null(cache: Cache) -> None:
    cache.apply([card("aaa", year=2023)], refreshed_tracks=["tech"])
    cache.apply(
        [card("aaa", year=2023), card("aaa", year=None, track="science")],
        refreshed_tracks=["science"],
    )
    assert cache.talk("aaa").year == 2023


def test_thumbnail_is_never_overwritten_with_null(cache: Cache) -> None:
    cache.apply([card("aaa", thumbnail="https://img/1.jpg")], refreshed_tracks=["tech"])
    cache.apply([card("aaa", thumbnail=None)], refreshed_tracks=["tech"])
    assert cache.talk("aaa").thumbnail_url == "https://img/1.jpg"


def test_dedupe_prefers_the_card_that_knows_the_year(cache: Cache) -> None:
    cache.apply(
        [card("aaa", year=None, track="science"), card("aaa", year=2024, track="tech")],
        refreshed_tracks=["tech", "science"],
    )
    talk = cache.talk("aaa")
    assert talk.year == 2024
    assert talk.tracks == ["science", "tech"]


# ------------------------------------------------------------- page state


def test_page_meta_roundtrip(cache: Cache) -> None:
    cache.record_page(
        track="tech",
        url=track_url("tech"),
        etag='"abc"',
        last_modified="Mon, 14 Sep 2026 08:01:00 GMT",
    )
    meta = cache.page_meta()["tech"]
    assert meta.url == "https://www.aigrunn.org/tech"
    assert meta.etag == '"abc"'
    assert meta.last_modified == "Mon, 14 Sep 2026 08:01:00 GMT"
    assert datetime.now(UTC) - meta.fetched_at < timedelta(seconds=30)


def test_a_304_keeps_the_previous_validators(cache: Cache) -> None:
    cache.record_page(track="tech", url=track_url("tech"), etag='"abc"', last_modified="then")
    before = cache.page_meta()["tech"].fetched_at
    cache.record_page(track="tech", url=track_url("tech"), etag=None, last_modified=None)
    after = cache.page_meta()["tech"]
    assert after.etag == '"abc"'
    assert after.last_modified == "then"
    assert after.fetched_at >= before


# -------------------------------------------------------- the version gate


def test_an_empty_file_initialises(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    path.touch()
    c = Cache(path)
    assert c.is_empty()
    c.close()


def test_reopening_our_own_database_is_fine(tmp_path: Path) -> None:
    path = tmp_path / "apigrunn.db"
    first = Cache(path)
    first.apply([card("aaa")], refreshed_tracks=["tech"])
    first.close()

    second = Cache(path)
    assert second.talk("aaa") is not None
    second.close()


def test_an_older_schema_raises(tmp_path: Path) -> None:
    path = tmp_path / "apigrunn.db"
    c = Cache(path)
    c._conn.execute("UPDATE schema_version SET version = 2")
    c._conn.commit()
    c.close()

    with pytest.raises(CacheVersionError) as excinfo:
        Cache(path)

    exc = excinfo.value
    assert exc.code == "APIGRUNN-E300"
    assert exc.path == path
    assert (exc.found, exc.expected) == (2, SCHEMA_VERSION)
    assert "APIGRUNN-E300" in str(exc)
    assert str(path) in str(exc)
    assert "delete the file" in str(exc)


def test_a_v2_html_database_raises(tmp_path: Path) -> None:
    """The real upgrade path: a cache that stored crawled HTML."""
    path = tmp_path / "apigrunn.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER NOT NULL);"
        "INSERT INTO schema_version (version) VALUES (2);"
        "CREATE TABLE pages (track TEXT PRIMARY KEY, url TEXT, html TEXT,"
        " etag TEXT, last_modified TEXT, fetched_at TEXT);"
    )
    conn.commit()
    conn.close()

    with pytest.raises(CacheVersionError, match="APIGRUNN-E300") as excinfo:
        Cache(path)
    assert excinfo.value.found == 2


def test_a_database_without_a_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "apigrunn.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE talks (video_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    with pytest.raises(CacheVersionError) as excinfo:
        Cache(path)
    assert excinfo.value.found is None
    assert "unknown apigrunn schema version" in str(excinfo.value)


def test_an_unrelated_database_is_left_alone(tmp_path: Path) -> None:
    """A file holding none of our tables is treated as blank, not clobbered."""
    path = tmp_path / "someone-elses.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    c = Cache(path)
    c.close()

    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "invoices" in tables
    assert {"talks", "talk_tracks", "pages", "schema_version"} <= tables


def test_default_db_path_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APIGRUNN_CACHE", str(tmp_path / "custom.db"))
    assert default_db_path() == tmp_path / "custom.db"

    monkeypatch.delenv("APIGRUNN_CACHE")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_db_path() == tmp_path / "apigrunn" / "apigrunn.db"
