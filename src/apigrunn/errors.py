"""Exceptions raised by :mod:`apigrunn`."""

from __future__ import annotations


class ApiGrunnError(Exception):
    """Base class for every error raised by this library."""


class FetchError(ApiGrunnError):
    """A track page could not be retrieved from aigrunn.org."""


class ParseError(ApiGrunnError):
    """A track page was retrieved but its markup could not be understood.

    Raised only when a page clearly contains talk cards yet none could be
    extracted, which means the site's markup changed and the parser needs
    updating.
    """
