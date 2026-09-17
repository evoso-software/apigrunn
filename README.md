# apigrunn

A small Python API over the [aiGrunn](https://www.aigrunn.org/) conference website,
backed by a local SQLite cache.

aigrunn.org has no public API and no per-talk pages — the archive of past talks is
embedded in the HTML of the eight track pages. This library crawls those pages, stores
their **raw HTML** in SQLite, and parses talks out of it when you query. Refreshes use
conditional HTTP requests, so an unchanged page costs a single `304`.

Keeping the database to just the crawled HTML means the schema is one table, a parser
fix takes effect on the next call with no re-crawl, and anything the site publishes that
isn't modelled yet is already sitting in the cache waiting to be parsed.

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
24-hour TTL expires. Parsing all eight pages takes ~300 ms, so each client memoises the
result against a digest of the stored HTML — repeated queries re-parse nothing, and the
memo drops itself the moment a page actually changes.

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
client.refresh(force=True)               # ignore ETags, re-parse everything
```

The default location is `$XDG_CACHE_HOME/apigrunn/apigrunn.db` (`~/.cache/apigrunn/apigrunn.db`),
overridable with `$APIGRUNN_CACHE`. The database is a copy of someone else's website — it
can be deleted at any time, and a schema change simply rebuilds it.

`refresh()` returns a `RefreshResult` reporting what happened per page:

```python
>>> print(client.refresh())
RefreshResult(fetched=8, not_modified=0, errors=0, talks=66)
```

### What's actually stored

One table, one row per track page:

```sql
CREATE TABLE pages (
    track         TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    html          TEXT NOT NULL,
    etag          TEXT,
    last_modified TEXT,
    fetched_at    TEXT NOT NULL
);
```

`client.pages()` hands you that HTML if you want to parse something the API does not
expose yet. The building blocks are public too, so you can go from HTML to talks without
a client at all:

```python
from apigrunn import parse_track_page, talks_from_cards

cards = parse_track_page(html, "tech")       # HTML -> one page's cards
talks = talks_from_cards(cards)              # cards -> deduplicated talks
```

Because talks are derived on read, a talk that disappears from aigrunn.org disappears
from the API on the next refresh. The cache mirrors the site; it is not an archive of
things the site used to say.

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
