"""Opt-in checks against the real aigrunn.org.

Run with ``pytest -m network``. These exist to catch upstream markup drift:
if the site restructures its "Blast from the past" section, this is the test
that fails first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apigrunn import ApiGrunn
from apigrunn.tracks import TRACKS

pytestmark = pytest.mark.network


def test_live_site_still_parses(tmp_path: Path) -> None:
    with ApiGrunn(db_path=tmp_path / "live.db") as client:
        result = client.refresh()
        assert result.errors == [], result.errors
        assert result.fetched == len(TRACKS)
        assert result.talks > 50

        talks = client.talks()
        assert {2023, 2024, 2025} <= set(client.years())
        assert all(t.title and t.url and t.year for t in talks)
        assert all(t.tracks for t in talks)
        # /tech is expected to remain the superset that seeds the cache.
        assert len(client.talks(track="tech")) == len(talks)


def test_live_refresh_is_conditional(tmp_path: Path) -> None:
    with ApiGrunn(db_path=tmp_path / "live.db") as client:
        first = client.refresh()
        second = client.refresh()
        assert second.not_modified == len(TRACKS)
        # Nothing was re-downloaded, so the stored HTML must still parse the same.
        assert second.talks == first.talks
