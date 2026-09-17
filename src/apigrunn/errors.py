"""Exceptions raised by :mod:`apigrunn`.

Every error carries a stable ``code`` that is prefixed onto its message, so a
failure can be recognised in a log or matched by a caller without depending on
the wording. Codes are part of the public contract: give a new condition a new
code rather than reusing or renumbering an existing one.

===================  ===========================================
``APIGRUNN-E000``    :class:`ApiGrunnError` (base, not raised)
``APIGRUNN-E100``    :class:`FetchError`
``APIGRUNN-E200``    :class:`ParseError`
``APIGRUNN-E300``    :class:`CacheVersionError`
===================  ===========================================
"""

from __future__ import annotations

from pathlib import Path


class ApiGrunnError(Exception):
    """Base class for every error raised by this library."""

    code = "APIGRUNN-E000"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"[{self.code}] {message}")


class FetchError(ApiGrunnError):
    """A track page could not be retrieved from aigrunn.org."""

    code = "APIGRUNN-E100"


class ParseError(ApiGrunnError):
    """A track page was retrieved but its markup could not be understood.

    Raised only when a page clearly contains talk cards yet none could be
    extracted, which means the site's markup changed and the parser needs
    updating.
    """

    code = "APIGRUNN-E200"


class CacheVersionError(ApiGrunnError):
    """The cache file was written by a different version of this library.

    The cache holds parsed talks rather than the pages they came from, so it
    cannot be migrated forward.
    """

    code = "APIGRUNN-E300"

    def __init__(self, path: Path, found: int | None, expected: int) -> None:
        self.path = path
        self.found = found
        self.expected = expected
        written_by = (
            f"apigrunn schema version {found}"
            if found is not None
            else "an unknown apigrunn schema version"
        )
        super().__init__(
            f"{path} was written by {written_by}, but this version requires "
            f"{expected}. The cache cannot be migrated."
        )
