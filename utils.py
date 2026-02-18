"""Shared utility functions for the Sorare MLS Auction Tracker."""

import re


def name_from_slug(slug: str) -> str:
    """Derive a display name from a slug like 'roman-celentano' -> 'Roman Celentano'.

    Strips trailing date suffixes used for disambiguation (e.g. '-1998-09-01').
    """
    cleaned = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", slug)
    return " ".join(part.capitalize() for part in cleaned.split("-"))


def ordinal(n: int) -> str:
    """Return ordinal string for a 1-based index: 1 -> '1st', 2 -> '2nd', ..."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
