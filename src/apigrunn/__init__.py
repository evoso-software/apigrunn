"""A Python API over the aiGrunn conference website, cached in SQLite.

    >>> from apigrunn import ApiGrunn
    >>> with ApiGrunn() as client:
    ...     for talk in client.talks(year=2024, track="healthcare"):
    ...         print(talk.title, talk.url)

The cache holds the parsed, normalized talks: pages are scraped and parsed at
refresh time, and queries are plain SQL against the stored rows.
"""

from ._version import __version__
from .cache import Cache, PageMeta, default_db_path
from .client import DEFAULT_TTL, ApiGrunn, AsyncApiGrunn
from .errors import ApiGrunnError, CacheVersionError, FetchError, ParseError
from .models import PageResult, RefreshResult, Talk, Track
from .parser import ParsedCard, parse_track_page, talks_from_cards
from .tracks import BASE_URL, TRACKS, track_name, track_url

__all__ = [
    "BASE_URL",
    "DEFAULT_TTL",
    "TRACKS",
    "ApiGrunn",
    "ApiGrunnError",
    "AsyncApiGrunn",
    "Cache",
    "CacheVersionError",
    "FetchError",
    "PageMeta",
    "PageResult",
    "ParseError",
    "ParsedCard",
    "RefreshResult",
    "Talk",
    "Track",
    "__version__",
    "default_db_path",
    "parse_track_page",
    "talks_from_cards",
    "track_name",
    "track_url",
]
