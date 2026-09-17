"""The eight aiGrunn tracks and where to find them.

The slugs are the URL paths on aigrunn.org; the display names are the ones
used in the site navigation. ``tech`` is listed first deliberately: its page
carries every archived talk, so it is the page that seeds the cache.
"""

from __future__ import annotations

BASE_URL = "https://www.aigrunn.org"

TRACKS: dict[str, str] = {
    "tech": "Tech",
    "business": "Business",
    "government": "Government",
    "education": "Education",
    "sensible-ai": "Sensible AI",
    "science": "Science & Research",
    "healthcare": "Healthcare & Life Sciences",
    "hardware": "Hardware",
}

#: The track whose page lists every talk, regardless of track.
SUPERSET_TRACK = "tech"


def track_url(slug: str) -> str:
    """Return the absolute URL of a track page."""
    return f"{BASE_URL}/{slug}"


def track_name(slug: str) -> str:
    """Return the display name for a track slug, or the slug itself if unknown."""
    return TRACKS.get(slug, slug)
