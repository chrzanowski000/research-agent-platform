"""Query normalization and slug generation utilities."""
from __future__ import annotations

import re
import unicodedata

from slugify import slugify


def normalize_query(raw: str) -> str:
    """Lowercase, strip, collapse whitespace, remove non-word characters."""
    s = raw.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s)


def make_slug(normalized: str) -> str:
    """Generate a filesystem-safe slug from a normalized query."""
    return slugify(normalized, max_length=80)
