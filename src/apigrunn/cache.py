"""SQLite store of the crawled aiGrunn pages.

The database holds exactly one thing: the HTML of each track page, with the
HTTP validators needed to re-check it cheaply. Nothing is derived at write
time — talks are parsed out of the stored HTML when a query asks for them.

That keeps the schema trivial and means a parser fix takes effect immediately,
without a re-crawl or a cache migration.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE pages (
    track         TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    html          TEXT NOT NULL,
    etag          TEXT,
    last_modified TEXT,
    fetched_at    TEXT NOT NULL
);
"""


class Page(NamedTuple):
    """One crawled track page."""

    track: str
    url: str
    html: str
    etag: str | None
    last_modified: str | None
    fetched_at: datetime


class PageMeta(NamedTuple):
    """A page's validators, without dragging its HTML along."""

    track: str
    url: str
    etag: str | None
    last_modified: str | None
    fetched_at: datetime


def default_db_path() -> Path:
    """Return the default cache location, honouring ``$APIGRUNN_CACHE`` and XDG."""
    override = os.environ.get("APIGRUNN_CACHE")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "apigrunn" / "apigrunn.db"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Cache:
    """Thread-safe store of crawled pages."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False so the async client can hand queries to a
        # worker thread; the lock below keeps those handoffs serialised.
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

        with self._lock:
            if str(self.path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._ensure_schema()

    # ---------------------------------------------------------------- schema

    def _ensure_schema(self) -> None:
        if self._current_version() == SCHEMA_VERSION:
            return
        # The cache is a copy of someone else's website: rebuilding is free.
        for table in ("pages", "talks", "talk_tracks", "schema_version"):
            self._conn.execute(f"DROP TABLE IF EXISTS {table}")
        self._conn.executescript(_SCHEMA)
        self._conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        self._conn.commit()

    def _current_version(self) -> int | None:
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if exists is None:
            return None
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        return row["version"] if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----------------------------------------------------------------- reads

    def pages(self) -> dict[str, Page]:
        """Every crawled page, keyed by track slug."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM pages").fetchall()
        return {
            row["track"]: Page(
                track=row["track"],
                url=row["url"],
                html=row["html"],
                etag=row["etag"],
                last_modified=row["last_modified"],
                fetched_at=_parse_ts(row["fetched_at"]),
            )
            for row in rows
        }

    def page_meta(self) -> dict[str, PageMeta]:
        """Validators for the conditional requests a refresh sends."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT track, url, etag, last_modified, fetched_at FROM pages"
            ).fetchall()
        return {
            row["track"]: PageMeta(
                track=row["track"],
                url=row["url"],
                etag=row["etag"],
                last_modified=row["last_modified"],
                fetched_at=_parse_ts(row["fetched_at"]),
            )
            for row in rows
        }

    def is_empty(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) AS n FROM pages").fetchone()["n"] == 0

    def oldest_fetch(self) -> datetime | None:
        """Oldest ``fetched_at`` across cached pages — drives the TTL check."""
        with self._lock:
            row = self._conn.execute("SELECT MIN(fetched_at) AS ts FROM pages").fetchone()
        return _parse_ts(row["ts"]) if row and row["ts"] else None

    # ---------------------------------------------------------------- writes

    def store(
        self,
        *,
        track: str,
        url: str,
        html: str,
        etag: str | None,
        last_modified: str | None,
    ) -> None:
        """Save a freshly fetched page, replacing any previous copy."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO pages (track, url, html, etag, last_modified, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(track) DO UPDATE SET
                    url           = excluded.url,
                    html          = excluded.html,
                    etag          = excluded.etag,
                    last_modified = excluded.last_modified,
                    fetched_at    = excluded.fetched_at
                """,
                (track, url, html, etag, last_modified, _now()),
            )

    def touch(self, track: str) -> None:
        """Record that a page was re-checked and had not changed (``304``)."""
        with self._lock, self._conn:
            self._conn.execute("UPDATE pages SET fetched_at = ? WHERE track = ?", (_now(), track))
