"""HTML sanitization for user-supplied rich text (e.g. Markdown descriptions).

Policy: plain text + links only. Markdown syntax passes through untouched
(Redmine renders it as CommonMark); any embedded HTML except safe links is
stripped. `javascript:`/event-handler vectors are neutralized by bleach.
"""

import bleach

_ALLOWED_TAGS = ["a"]
_ALLOWED_ATTRIBUTES = {"a": ["href", "title"]}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def sanitize_rich_text(value: str | None) -> str | None:
    """Strip everything but text and safe links. None-safe, idempotent."""
    if not value:
        return value
    return bleach.clean(
        value,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
