"""SQLite store of the parsed aiGrunn talk archive.

The database holds normalized talks — one row per talk, one row per
(talk, track) pair — plus the HTTP validators needed to re-check each source
page cheaply. Pages are parsed once, at refresh time; queries are plain SQL.

Because the pages themselves are not kept, the cache cannot be migrated across
schema versions: an older file raises
:class:`~apigrunn.errors.CacheVersionError` rather than being silently rebuilt.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from .errors import CacheVersionError
from .models import Talk
from .parser import ParsedCard

SCHEMA_VERSION = 3

_TABLES = ("schema_version", "pages", "talks", "talk_tracks")

_SCHEMA = """
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE pages (
    track         TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    etag          TEXT,
    last_modified TEXT,
    fetched_at    TEXT NOT NULL
);

CREATE TABLE talks (
    video_id      TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    speaker       TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL,
    year          INTEGER,
    thumbnail_url TEXT
);

CREATE TABLE talk_tracks (
    video_id TEXT NOT NULL REFERENCES talks(video_id) ON DELETE CASCADE,
    track    TEXT NOT NULL,
    PRIMARY KEY (video_id, track)
);

CREATE INDEX idx_talks_year ON talks(year);
CREATE INDEX idx_talk_tracks_track ON talk_tracks(track);
"""


class PageMeta(NamedTuple):
    """A source page's validators, used to make refreshes conditional."""

    track: str
    url: str
    etag: str | None
    last_modified: str | None
    fetched_at: datetime


class ApplyResult(NamedTuple):
    """How a write changed the stored archive."""

    added: int
    removed: int
    total: int


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
    """Thread-safe store of parsed talks."""

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
            self._conn.execute("PRAGMA foreign_keys = ON")
            if str(self.path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._ensure_schema()

    # ---------------------------------------------------------------- schema

    def _ensure_schema(self) -> None:
        if self._is_blank():
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
            self._conn.commit()
            return

        version = self._stored_version()
        if version != SCHEMA_VERSION:
            # Talks are stored parsed, so there is nothing to migrate from.
            raise CacheVersionError(self.path, version, SCHEMA_VERSION)

    def _is_blank(self) -> bool:
        """True for a new or empty file — one holding none of our tables."""
        placeholders = ",".join("?" * len(_TABLES))
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' "
            f"AND name IN ({placeholders})",
            _TABLES,
        ).fetchone()
        return row["n"] == 0

    def _stored_version(self) -> int | None:
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

    # ------------------------------------------------------------ page state

    def page_meta(self) -> dict[str, PageMeta]:
        """Validators for the conditional requests a refresh sends."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM pages").fetchall()
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

    def record_page(
        self, *, track: str, url: str, etag: str | None, last_modified: str | None
    ) -> None:
        """Mark a page as checked now, keeping previous validators if absent.

        A ``304`` carries no validators of its own, so passing ``None`` for both
        bumps ``fetched_at`` and leaves the stored ones in place.
        """
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO pages (track, url, etag, last_modified, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(track) DO UPDATE SET
                    url           = excluded.url,
                    etag          = COALESCE(excluded.etag, pages.etag),
                    last_modified = COALESCE(excluded.last_modified, pages.last_modified),
                    fetched_at    = excluded.fetched_at
                """,
                (track, url, etag, last_modified, _now()),
            )

    def is_empty(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) AS n FROM talks").fetchone()["n"] == 0

    def oldest_fetch(self) -> datetime | None:
        """Oldest ``fetched_at`` across cached pages — drives the TTL check."""
        with self._lock:
            row = self._conn.execute("SELECT MIN(fetched_at) AS ts FROM pages").fetchone()
        return _parse_ts(row["ts"]) if row and row["ts"] else None

    # ---------------------------------------------------------------- writes

    def apply(
        self, cards: Sequence[ParsedCard], *, refreshed_tracks: Iterable[str]
    ) -> ApplyResult:
        """Write the cards from freshly parsed pages into the archive.

        Track tags are replaced only for pages that were actually re-parsed, so
        a page that answered ``304`` — or failed — keeps the tags it had. A talk
        left on no page at all is then pruned, which is what makes the cache a
        mirror of the site rather than an accumulating archive.
        """
        refreshed = list(refreshed_tracks)

        with self._lock, self._conn:  # one lock, one transaction
            before = {
                row["video_id"] for row in self._conn.execute("SELECT video_id FROM talks")
            }

            for card in _dedupe(cards):
                self._conn.execute(
                    """
                    INSERT INTO talks (video_id, title, description, speaker, url, year,
                                       thumbnail_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(video_id) DO UPDATE SET
                        title         = excluded.title,
                        description   = excluded.description,
                        speaker       = excluded.speaker,
                        url           = excluded.url,
                        year          = COALESCE(excluded.year, talks.year),
                        thumbnail_url = COALESCE(excluded.thumbnail_url, talks.thumbnail_url)
                    """,
                    (
                        card.video_id,
                        card.title,
                        card.description,
                        card.speaker,
                        card.url,
                        card.year,
                        card.thumbnail_url,
                    ),
                )

            for track in refreshed:
                self._conn.execute("DELETE FROM talk_tracks WHERE track = ?", (track,))
            self._conn.executemany(
                "INSERT OR IGNORE INTO talk_tracks (video_id, track) VALUES (?, ?)",
                [(card.video_id, card.track) for card in cards],
            )

            self._conn.execute(
                "DELETE FROM talks WHERE video_id NOT IN (SELECT video_id FROM talk_tracks)"
            )

            after = {
                row["video_id"] for row in self._conn.execute("SELECT video_id FROM talks")
            }

        return ApplyResult(
            added=len(after - before), removed=len(before - after), total=len(after)
        )

    # ----------------------------------------------------------------- reads

    def talks(
        self,
        *,
        year: int | None = None,
        track: str | None = None,
        search: str | None = None,
    ) -> list[Talk]:
        sql = ["SELECT * FROM talks WHERE 1=1"]
        params: list[object] = []
        if year is not None:
            sql.append("AND year = ?")
            params.append(year)
        if track is not None:
            sql.append("AND video_id IN (SELECT video_id FROM talk_tracks WHERE track = ?)")
            params.append(track)
        if search:
            sql.append(
                "AND (lower(title) LIKE ? OR lower(description) LIKE ? OR lower(speaker) LIKE ?)"
            )
            needle = f"%{search.lower()}%"
            params.extend([needle, needle, needle])
        sql.append("ORDER BY year DESC, title COLLATE NOCASE ASC")

        with self._lock:
            rows = self._conn.execute(" ".join(sql), params).fetchall()
        return self._build_talks(rows)

    def talk(self, video_id: str) -> Talk | None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM talks WHERE video_id = ?", (video_id,)
            ).fetchall()
        talks = self._build_talks(rows)
        return talks[0] if talks else None

    def years(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT year FROM talks WHERE year IS NOT NULL ORDER BY year DESC"
            ).fetchall()
        return [row["year"] for row in rows]

    def track_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT track, COUNT(*) AS n FROM talk_tracks GROUP BY track"
            ).fetchall()
        return {row["track"]: row["n"] for row in rows}

    def _build_talks(self, rows: Sequence[sqlite3.Row]) -> list[Talk]:
        if not rows:
            return []
        ids = [row["video_id"] for row in rows]
        tags: dict[str, list[str]] = {vid: [] for vid in ids}
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            # A second query rather than group_concat, whose ordering is not
            # guaranteed and would make Talk.tracks unstable.
            tag_rows = self._conn.execute(
                f"SELECT video_id, track FROM talk_tracks WHERE video_id IN ({placeholders}) "
                "ORDER BY track",
                ids,
            ).fetchall()
        for row in tag_rows:
            tags[row["video_id"]].append(row["track"])

        return [
            Talk(
                video_id=row["video_id"],
                title=row["title"],
                description=row["description"],
                speaker=row["speaker"],
                url=row["url"],
                year=row["year"],
                tracks=tags[row["video_id"]],
                thumbnail_url=row["thumbnail_url"],
            )
            for row in rows
        ]


def _dedupe(cards: Sequence[ParsedCard]) -> list[ParsedCard]:
    """Collapse the same talk seen on several track pages into one row."""
    best: dict[str, ParsedCard] = {}
    for card in cards:
        current = best.get(card.video_id)
        # Prefer a card that knows the year; only the lane headers carry it.
        if current is None or (current.year is None and card.year is not None):
            best[card.video_id] = card
    return list(best.values())
