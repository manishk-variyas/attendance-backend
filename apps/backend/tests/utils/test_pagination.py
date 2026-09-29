"""Isolated tests for app.utils.pagination.

Runs entirely on an in-memory SQLite database with a local dummy model —
no app database, no fixtures, no network.
"""
import pytest
from sqlalchemy import Column, Integer, String, create_engine, func, select
from sqlalchemy.orm import declarative_base, Session

from app.utils.pagination import paginate_query, paginate_text

Base = declarative_base()


class Widget(Base):
    __tablename__ = "widgets"
    id = Column(Integer, primary_key=True)
    name = Column(String(50))
    qty = Column(Integer, default=0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"]
    for i, name in enumerate(names, start=1):
        session.add(Widget(name=name, qty=i * 10))
    session.commit()
    yield session
    session.close()


ALLOW = {"name": Widget.name, "qty": Widget.qty}


def names(page):
    return [w.name for w in page.items]


def test_first_and_second_page(db):
    p1 = paginate_query(db, db.query(Widget), page=1, page_size=3,
                        sort_by="name", sort_dir="asc", allowed_sorts=ALLOW)
    assert names(p1) == ["alpha", "bravo", "charlie"]
    assert (p1.total, p1.page, p1.page_size, p1.total_pages) == (7, 1, 3, 3)
    p3 = paginate_query(db, db.query(Widget), page=3, page_size=3,
                        sort_by="name", sort_dir="asc", allowed_sorts=ALLOW)
    assert names(p3) == ["golf"]


def test_total_pages_math(db):
    p = paginate_query(db, db.query(Widget), page=1, page_size=7, allowed_sorts=ALLOW)
    assert (p.total, p.total_pages) == (7, 1)
    p = paginate_query(db, db.query(Widget).filter(Widget.id > 100), page=1, page_size=5)
    assert p.total == 0 and p.total_pages == 0 and p.items == []


def test_page_beyond_end_is_empty_but_total_intact(db):
    p = paginate_query(db, db.query(Widget), page=99, page_size=5, allowed_sorts=ALLOW)
    assert p.items == [] and p.total == 7 and p.total_pages == 2


@pytest.mark.parametrize("page", [0, -1, "x", None])
def test_invalid_page_rejected(db, page):
    with pytest.raises(ValueError, match="page must be"):
        paginate_query(db, db.query(Widget), page=page, allowed_sorts=ALLOW)


@pytest.mark.parametrize("size", [0, -5, "x"])
def test_invalid_page_size_rejected(db, size):
    with pytest.raises(ValueError, match="page_size must be"):
        paginate_query(db, db.query(Widget), page_size=size)


def test_page_size_capped(db):
    with pytest.raises(ValueError, match="between 1 and 200"):
        paginate_query(db, db.query(Widget), page_size=201)
    p = paginate_query(db, db.query(Widget), page_size=50, max_page_size=50)
    assert p.page_size == 50 and p.total == 7


def test_sort_desc_and_unknown_sort(db):
    p = paginate_query(db, db.query(Widget), page=1, page_size=3,
                       sort_by="qty", sort_dir="desc", allowed_sorts=ALLOW)
    assert names(p) == ["golf", "foxtrot", "echo"]
    with pytest.raises(ValueError, match="Invalid sort_by"):
        paginate_query(db, db.query(Widget), sort_by="DROP TABLE widgets", allowed_sorts=ALLOW)
    with pytest.raises(ValueError, match="sort_dir"):
        paginate_query(db, db.query(Widget), sort_by="name", sort_dir="sideways", allowed_sorts=ALLOW)


def test_default_order_applies_without_sort(db):
    p = paginate_query(db, db.query(Widget), page=1, page_size=7,
                       default_order_by=[Widget.qty.desc()])
    assert names(p)[0] == "golf"


def test_select_style_and_tuple_rows(db):
    stmt = select(Widget).where(Widget.qty >= 30)
    p = paginate_query(db, stmt, page=1, page_size=2, sort_by="qty",
                       sort_dir="asc", allowed_sorts=ALLOW)
    assert names(p) == ["charlie", "delta"] and p.total == 5
    cols = paginate_query(db, select(Widget.name, Widget.qty), page=1, page_size=2,
                          sort_by="qty", allowed_sorts=ALLOW, scalars=False)
    assert list(cols.items[0]) == ["alpha", 10]


def test_grouped_query_counts_groups(db):
    stmt = select(Widget.qty).group_by(Widget.qty)
    p = paginate_query(db, stmt, page=1, page_size=100, scalars=False)
    assert p.total == 7  # one group per distinct qty


def test_total_override_skips_count(db):
    p = paginate_query(db, db.query(Widget), page=1, page_size=3, total=999)
    assert (p.total, p.total_pages) == (999, 333) and len(p.items) == 3


def test_paginate_text(db):
    sel = "SELECT id, name, qty FROM widgets WHERE qty >= :minq"
    cnt = "SELECT COUNT(*) FROM widgets WHERE qty >= :minq"
    p = paginate_text(db, select_sql=sel, count_sql=cnt,
                      params={"minq": 30}, page=2, page_size=2, order_by="qty ASC")
    assert [r[1] for r in p.items] == ["echo", "foxtrot"]
    assert (p.total, p.page, p.total_pages) == (5, 2, 3)


def test_paginate_text_empty_and_validation(db):
    sel = "SELECT id FROM widgets WHERE 1 = 0"
    p = paginate_text(db, select_sql=sel, count_sql="SELECT COUNT(*) FROM widgets WHERE 1 = 0")
    assert p.total == 0 and p.items == [] and p.total_pages == 0
    with pytest.raises(ValueError, match="page must be"):
        paginate_text(db, select_sql=sel, count_sql=sel, page=0)
