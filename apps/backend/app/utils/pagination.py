"""Reusable database-level pagination for list endpoints.

Two entry points, one contract:

- :func:`paginate_query` — SQLAlchemy ORM / legacy queries
  (``db.query(Model)...`` or ``select(...)``).
- :func:`paginate_text` — raw SQL text queries (schema-qualified tables,
  ``GROUP BY`` aggregates, etc.) where the caller supplies the SELECT and
  a matching COUNT query.

Both validate input, enforce an allowlisted sort, run a **separate COUNT
query** (the page is never counted), and return a :class:`PageResult` with
items + total/page/page_size/total_pages.

Framework-free on purpose: invalid input raises :class:`ValueError` with a
clear message so routes can map it to 400 however they like. Importing this
module has no side effects (no FastAPI, no app config).

Rules callers must follow:
- ``allowed_sorts`` maps the public ``sort_by`` name to a column/expression.
  Unknown names are rejected — never interpolate raw user input into ORDER BY.
- Paging is only deterministic with an explicit order. If no ``sort_by`` is
  given, ``default_order_by`` is used; pass one whenever rows can tie.
- For ``paginate_text``, ``select_sql`` must NOT contain its own ORDER BY /
  LIMIT / OFFSET; pass ordering via ``order_by`` (a fixed, caller-side
  fragment, e.g. ``"name ASC"``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Mapping, Optional, Sequence, TypeVar

from sqlalchemy import func, select, text
from sqlalchemy.orm import Query
from sqlalchemy.sql import Select

T = TypeVar("T")

DEFAULT_MAX_PAGE_SIZE = 200
_SORT_DIRS = ("asc", "desc")


@dataclass(frozen=True)
class PageResult(Generic[T]):
    """One page of rows plus the totals needed to render pagination UI."""

    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


def _normalize_paging(page: Any, page_size: Any, max_page_size: int) -> tuple[int, int]:
    try:
        page = int(page)
    except (TypeError, ValueError):
        raise ValueError("page must be an integer")
    try:
        page_size = int(page_size)
    except (TypeError, ValueError):
        raise ValueError("page_size must be an integer")
    if page < 1:
        raise ValueError("page must be >= 1")
    if page_size < 1 or page_size > max_page_size:
        raise ValueError(f"page_size must be between 1 and {max_page_size}")
    return page, page_size


def _normalize_sort(
    sort_by: Optional[str],
    sort_dir: Optional[str],
    allowed_sorts: Optional[Mapping[str, Any]],
) -> tuple[Any, str]:
    direction = (sort_dir or "asc").lower()
    if direction not in _SORT_DIRS:
        raise ValueError("sort_dir must be 'asc' or 'desc'")
    if not sort_by:
        return None, direction
    if not allowed_sorts or sort_by not in allowed_sorts:
        valid = ", ".join(sorted(allowed_sorts or {})) or "none"
        raise ValueError(f"Invalid sort_by. Use one of: {valid}")
    return allowed_sorts[sort_by], direction


def _apply_direction(column: Any, direction: str) -> Any:
    return column.desc() if direction == "desc" else column.asc()


def paginate_query(
    db,
    query: Select | Query,
    *,
    page: Any = 1,
    page_size: Any = 50,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = "asc",
    allowed_sorts: Optional[Mapping[str, Any]] = None,
    default_order_by: Optional[Sequence[Any]] = None,
    max_page_size: int = DEFAULT_MAX_PAGE_SIZE,
    total: Optional[int] = None,
    scalars: bool = True,
) -> PageResult:
    """Paginate an ORM/legacy SQLAlchemy query at the database level.

    Accepts both 2.0-style ``select()`` and legacy ``db.query(...)`` objects
    (the latter is what most of this codebase uses). ``items`` are ORM
    entities when the query selects them; pass ``scalars=False`` for
    ``select()`` column projections to get raw rows instead.

    Pass ``total`` only when the caller already ran its own COUNT (e.g. a
    separate aggregate over a wider scope than the page); otherwise a COUNT
    query is executed automatically. For GROUP BY queries the automatic
    count equals the number of groups.
    """
    page, page_size = _normalize_paging(page, page_size, max_page_size)
    sort_col, direction = _normalize_sort(sort_by, sort_dir, allowed_sorts)

    is_legacy = isinstance(query, Query)
    if sort_col is not None:
        query = query.order_by(_apply_direction(sort_col, direction))
    elif default_order_by:
        query = query.order_by(*default_order_by)

    if total is None:
        if is_legacy:
            total = query.order_by(None).count()
        else:
            total = db.execute(
                select(func.count()).select_from(query.order_by(None).subquery())
            ).scalar() or 0
    offset = (page - 1) * page_size
    if is_legacy:
        items = list(query.limit(page_size).offset(offset).all())
    else:
        result = db.execute(query.limit(page_size).offset(offset))
        items = list(result.scalars().all()) if scalars else list(result.all())
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
    return PageResult(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


def paginate_text(
    db,
    *,
    select_sql: str,
    count_sql: str,
    params: Optional[Mapping[str, Any]] = None,
    page: Any = 1,
    page_size: Any = 50,
    order_by: Optional[str] = None,
    max_page_size: int = DEFAULT_MAX_PAGE_SIZE,
) -> PageResult:
    """Paginate a raw SQL text query at the database level.

    ``select_sql`` must be a plain SELECT without its own ORDER BY / LIMIT /
    OFFSET. ``order_by`` is appended verbatim, so it must be a fixed
    caller-side fragment (e.g. ``"name ASC"``) — never user input.
    ``count_sql`` must return a single scalar total for the same filters.
    """
    page, page_size = _normalize_paging(page, page_size, max_page_size)
    base_params = dict(params or {})
    total = db.execute(text(count_sql), base_params).scalar() or 0

    paged_sql = select_sql.rstrip().rstrip(";")
    if order_by:
        paged_sql += f" ORDER BY {order_by}"
    paged_sql += " LIMIT :_limit OFFSET :_offset"
    rows = db.execute(
        text(paged_sql), {**base_params, "_limit": page_size, "_offset": (page - 1) * page_size}
    ).fetchall()
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
    return PageResult(items=list(rows), total=total, page=page, page_size=page_size, total_pages=total_pages)
