"""A Python API over the aiGrunn conference website, cached in SQLite.

    >>> from apigrunn import ApiGrunn
    >>> with ApiGrunn() as client:
    ...     for talk in client.talks(year=2024, track="healthcare"):
    ...         print(talk.title, talk.url)

The cache stores the crawled HTML of the eight track pages and nothing else;
talks are parsed out of that HTML when a query asks for them.
"""

from ._version import __version__
from .cache import Cache, Page, default_db_path
from .client import DEFAULT_TTL, ApiGrunn, AsyncApiGrunn
from .errors import ApiGrunnError, FetchError, ParseError
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
    "FetchError",
    "Page",
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
