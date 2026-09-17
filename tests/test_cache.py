"""The cache now stores crawled HTML and nothing else, so these tests are
about round-tripping pages and validators — not about talks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apigrunn.cache import Cache, default_db_path
from apigrunn.tracks import track_url


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    c = Cache(tmp_path / "apigrunn.db")
    yield c
    c.close()


def store(cache: Cache, track: str = "tech", html: str = "<html>hi</html>", **kwargs) -> None:
    cache.store(
        track=track,
        url=track_url(track),
        html=html,
        etag=kwargs.get("etag", '"abc"'),
        last_modified=kwargs.get("last_modified", "Mon, 14 Sep 2026 08:01:00 GMT"),
    )


def test_starts_empty(cache: Cache) -> None:
    assert cache.is_empty()
    assert cache.pages() == {}
    assert cache.page_meta() == {}
    assert cache.oldest_fetch() is None


def test_store_roundtrips_the_html(cache: Cache) -> None:
    store(cache, html="<html>the page</html>")
    page = cache.pages()["tech"]
    assert page.html == "<html>the page</html>"
    assert page.url == "https://www.aigrunn.org/tech"
    assert page.etag == '"abc"'
    assert page.last_modified == "Mon, 14 Sep 2026 08:01:00 GMT"
    assert datetime.now(UTC) - page.fetched_at < timedelta(seconds=30)
    assert not cache.is_empty()


def test_store_replaces_the_previous_copy(cache: Cache) -> None:
    store(cache, html="<html>old</html>", etag='"v1"')
    store(cache, html="<html>new</html>", etag='"v2"')
    pages = cache.pages()
    assert len(pages) == 1
    assert pages["tech"].html == "<html>new</html>"
    assert pages["tech"].etag == '"v2"'


def test_page_meta_leaves_the_html_behind(cache: Cache) -> None:
    store(cache, html="<html>" + "x" * 100_000 + "</html>")
    meta = cache.page_meta()["tech"]
    assert meta.etag == '"abc"'
    assert not hasattr(meta, "html")


def test_touch_bumps_the_timestamp_and_keeps_everything_else(cache: Cache) -> None:
    store(cache, html="<html>original</html>")
    before = cache.pages()["tech"]
    cache.touch("tech")
    after = cache.pages()["tech"]
    assert after.html == "<html>original</html>"
    assert after.etag == before.etag
    assert after.last_modified == before.last_modified
    assert after.fetched_at >= before.fetched_at


def test_touching_an_unknown_page_is_a_no_op(cache: Cache) -> None:
    cache.touch("healthcare")
    assert cache.pages() == {}


def test_oldest_fetch_spans_every_page(cache: Cache) -> None:
    store(cache, "tech")
    store(cache, "healthcare")
    assert cache.oldest_fetch() is not None
    assert len(cache.page_meta()) == 2


def test_schema_change_rebuilds_the_database(tmp_path: Path) -> None:
    path = tmp_path / "apigrunn.db"
    first = Cache(path)
    store(first)
    first.close()

    stale = Cache(path)
    stale._conn.execute("UPDATE schema_version SET version = 0")
    stale._conn.commit()
    stale.close()

    rebuilt = Cache(path)
    assert rebuilt.is_empty()  # a copy of a website, so rebuilding is free
    rebuilt.close()


def test_a_v1_database_is_replaced_not_read(tmp_path: Path) -> None:
    """The old schema derived talks at write time; it must not linger."""
    path = tmp_path / "apigrunn.db"
    legacy = Cache(path)
    legacy._conn.executescript(
        "DROP TABLE pages;"
        "CREATE TABLE talks (video_id TEXT PRIMARY KEY);"
        "CREATE TABLE talk_tracks (video_id TEXT, track TEXT);"
        "UPDATE schema_version SET version = 1;"
    )
    legacy._conn.commit()
    legacy.close()

    upgraded = Cache(path)
    tables = {
        row["name"]
        for row in upgraded._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert tables == {"schema_version", "pages"}
    upgraded.close()


def test_default_db_path_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APIGRUNN_CACHE", str(tmp_path / "custom.db"))
    assert default_db_path() == tmp_path / "custom.db"

    monkeypatch.delenv("APIGRUNN_CACHE")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_db_path() == tmp_path / "apigrunn" / "apigrunn.db"
