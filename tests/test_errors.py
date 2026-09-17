"""Error codes are part of the public contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from apigrunn.errors import ApiGrunnError, CacheVersionError, FetchError, ParseError

ALL_ERRORS = [ApiGrunnError, FetchError, ParseError, CacheVersionError]


def test_codes_are_unique() -> None:
    codes = [e.code for e in ALL_ERRORS]
    assert len(set(codes)) == len(codes)
    assert all(c.startswith("APIGRUNN-E") for c in codes)


@pytest.mark.parametrize("error", [ApiGrunnError, FetchError, ParseError])
def test_the_message_is_prefixed_with_the_code(error: type[ApiGrunnError]) -> None:
    exc = error("something went wrong")
    assert str(exc) == f"[{error.code}] something went wrong"
    assert exc.message == "something went wrong"


def test_every_error_is_catchable_as_the_base() -> None:
    with pytest.raises(ApiGrunnError):
        raise FetchError("boom")


def test_cache_version_error_reports_both_versions() -> None:
    exc = CacheVersionError(Path("/tmp/x.db"), found=2, expected=3)
    assert exc.code == "APIGRUNN-E300"
    assert "/tmp/x.db" in str(exc)
    assert "schema version 2" in str(exc)
    assert "requires 3" in str(exc)


def test_cache_version_error_without_a_known_version() -> None:
    exc = CacheVersionError(Path("/tmp/x.db"), found=None, expected=3)
    assert "unknown apigrunn schema version" in str(exc)
    assert exc.found is None
