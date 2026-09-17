from __future__ import annotations

import pytest

from apigrunn.models import PageResult, RefreshResult, Talk


def talk(speaker: str) -> Talk:
    return Talk(video_id="x", title="T", url="https://youtu.be/x", speaker=speaker)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("   ", []),
        ("Berco Beute", ["Berco Beute"]),
        ("Wieneke Keller & Navaneeth Krishnan", ["Wieneke Keller", "Navaneeth Krishnan"]),
        (
            "Niels Taatgen, Willem Jonker, Onno Zoeter, Yonatan Alexander",
            ["Niels Taatgen", "Willem Jonker", "Onno Zoeter", "Yonatan Alexander"],
        ),
        ("Ada and Grace", ["Ada", "Grace"]),
    ],
)
def test_speakers_splits_the_raw_string(raw: str, expected: list[str]) -> None:
    assert talk(raw).speakers == expected


def test_raw_speaker_is_preserved() -> None:
    raw = "Wieneke Keller & Navaneeth Krishnan"
    assert talk(raw).speaker == raw


def test_refresh_result_counters() -> None:
    result = RefreshResult(
        pages=[
            PageResult(track="tech", url="u", status="fetched", cards=66),
            PageResult(track="business", url="u", status="not_modified"),
            PageResult(track="science", url="u", status="error", error="HTTP 503"),
        ],
        talks_added=66,
    )
    assert (result.fetched, result.not_modified) == (1, 1)
    assert [p.track for p in result.errors] == ["science"]
