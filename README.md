# apigrunn

A small Python API over the [aiGrunn](https://www.aigrunn.org/) conference website,
backed by a local SQLite cache.

aigrunn.org has no public API and no per-talk pages — the archive of past talks is
embedded in the HTML of the eight track pages. This library crawls and parses those pages,
stores the **normalized talks** in SQLite, and answers queries with plain SQL. Refreshes
use conditional HTTP requests, so an unchanged page costs a single `304`.

## Install

```bash
pip install apigrunn        # or: uv add apigrunn
```

Requires Python 3.11+.

## Usage

```python
from apigrunn import ApiGrunn

with ApiGrunn() as client:
    for talk in client.talks(year=2024, track="healthcare"):
        print(talk.title, "—", talk.speaker)
        print(talk.url)
```

The first call crawls aigrunn.org into the cache; later calls read from SQLite until the
24-hour TTL expires. Pages are parsed once, during a refresh — queries never touch HTML.

### Queries

```python
client.talks()                          # every archived talk, newest year first
client.talks(year=2025)                 # one edition
client.talks(track="sensible-ai")       # one track
client.talks(search="RAG")              # substring over title, description, speaker
client.talk("ys__Y1mICwo")              # one talk by YouTube video id
client.years()                          # [2025, 2024, 2023]
client.tracks()                         # the 8 tracks with talk counts
```

### A talk

```python
Talk(
    video_id="tP_kg20VAho",
    title="We Put YOLO on a Tractor — What could go wrong?",
    description="Aurea Imaging retrofits tractors with vision-powered edge devices …",
    speaker="Wieneke Keller & Navaneeth Krishnan",
    url="https://www.youtube.com/watch?v=tP_kg20VAho",
    year=2025,
    tracks=["science", "tech"],
    thumbnail_url="https://img.youtube.com/vi/tP_kg20VAho/mqdefault.jpg",
)
```

A talk can belong to several tracks, so `tracks` is a list. `speaker` is the raw string
as published; `talk.speakers` splits it into names on a best-effort basis.

### Async

`AsyncApiGrunn` mirrors every method:

```python
import asyncio
from apigrunn import AsyncApiGrunn

async def main():
    async with AsyncApiGrunn() as client:
        print(len(await client.talks()))

asyncio.run(main())
```

### Cache control

```python
from datetime import timedelta

ApiGrunn(db_path="/tmp/apigrunn.db")     # where to cache
ApiGrunn(ttl=timedelta(days=7))          # how long before an auto-refresh
ApiGrunn(auto_refresh=False)             # never hit the network implicitly
client.refresh()                         # refresh now (conditional requests)
client.refresh(force=True)               # ignore ETags, re-download and re-parse
```

The default location is `$XDG_CACHE_HOME/apigrunn/apigrunn.db` (`~/.cache/apigrunn/apigrunn.db`),
overridable with `$APIGRUNN_CACHE`. The database is a copy of someone else's website and
can be deleted at any time; see [Errors](#errors) for what happens to a cache written by
an older version.

`refresh()` returns a `RefreshResult` reporting what happened per page:

```python
>>> print(client.refresh())
RefreshResult(fetched=8, not_modified=0, errors=0, talks=66, added=66, removed=0)
```

### What's actually stored

Normalized talks, plus the HTTP validators that make refreshes conditional:

```sql
CREATE TABLE talks (
    video_id      TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    speaker       TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL,
    year          INTEGER,
    thumbnail_url TEXT
);

CREATE TABLE talk_tracks (            -- a talk belongs to 1..n tracks
    video_id TEXT NOT NULL REFERENCES talks(video_id) ON DELETE CASCADE,
    track    TEXT NOT NULL,
    PRIMARY KEY (video_id, track)
);

CREATE TABLE pages (                  -- one row per source page
    track TEXT PRIMARY KEY, url TEXT NOT NULL,
    etag TEXT, last_modified TEXT, fetched_at TEXT NOT NULL
);
```

Two consequences worth knowing:

- **A talk removed from aigrunn.org is pruned on the next refresh.** `talk_tracks` is the
  per-page record of what each page listed, so a talk left on no page is deleted. The
  cache mirrors the site; it is not an archive of what the site used to say.
- **The pages themselves are not kept**, so a parser fix does not reach already-cached
  talks. Run `client.refresh(force=True)` to re-download and re-parse.

The parsing pipeline is public if you want to run it yourself:

```python
from apigrunn import parse_track_page, talks_from_cards

cards = parse_track_page(html, "tech")       # HTML -> one page's cards
talks = talks_from_cards(cards)              # cards -> deduplicated talks
```

### Errors

Every exception carries a stable code, prefixed onto its message, so failures are
greppable without matching on prose:

| Code | Exception | Raised when |
| --- | --- | --- |
| `APIGRUNN-E100` | `FetchError` | every track page failed to download |
| `APIGRUNN-E200` | `ParseError` | a page has talk cards but none could be extracted |
| `APIGRUNN-E300` | `CacheVersionError` | the cache file is from another schema version |

All inherit from `ApiGrunnError`. Because talks are stored parsed, a cache written by a
different version of this library cannot be migrated — opening one raises rather than
silently rebuilding:

```
[APIGRUNN-E300] ~/.cache/apigrunn/apigrunn.db was written by apigrunn schema
version 2, but this version requires 3. The cache cannot be migrated: delete
the file to re-crawl, or pass a different db_path.
```

`CacheVersionError` carries `.path`, `.found` and `.expected` so a caller can act on it
without parsing the message.

## Scope

Currently exposed: the talk archive for aiGrunn 2023, 2024 and 2025 (track, title,
description, speaker, URL, year, thumbnail). The 2026 schedule is not yet published on
aigrunn.org. Sponsors, podcast episodes and aiLand/Café events are not covered yet.

## Development

```bash
uv sync
uv run pytest                  # offline: runs against saved HTML fixtures
uv run pytest -m network       # also checks the live site for markup drift
```

No system Python? The flake provides a pinned interpreter, uv and ruff:

```bash
nix develop                          # drop into the dev shell
nix develop -c uv run pytest         # or run a single command in it
```

The shell sets `UV_PYTHON` to the Nix interpreter and disables uv's own Python
downloads, so `uv sync` always builds the venv against the pinned 3.13.

The parser is pure (HTML string in, records out) and is tested against snapshots of all
eight track pages in `tests/fixtures/`. If aigrunn.org changes its markup, the networked
test is what will tell you.

## Courtesy

`robots.txt` on aigrunn.org is `Allow: /`. The library still identifies itself with a
descriptive `User-Agent`, caps concurrency at 4, and uses conditional requests so a
refresh transfers no data when nothing changed.

## Licence

MIT
