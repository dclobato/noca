#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena pagination helpers."""

from arena.services.pagination_service import Pagination, build_pagination_params, clamp_page, parse_page


def test_parse_page_clamps_invalid_values() -> None:
    """Invalid, missing, and low page values should resolve to page one."""
    assert parse_page(None) == 1
    assert parse_page("not-a-number") == 1
    assert parse_page("-4") == 1
    assert parse_page("3") == 3


def test_pagination_zero_total_has_empty_bounds() -> None:
    """Empty pagination should expose zero pages and zero item bounds."""
    pagination: Pagination[int] = Pagination(items=[], page=1, per_page=50, total=0)

    assert pagination.pages == 0
    assert pagination.first == 0
    assert pagination.last == 0
    assert not pagination.has_prev
    assert not pagination.has_next


def test_pagination_page_count_and_prev_next() -> None:
    """Pagination should report page count plus previous and next page numbers."""
    pagination = Pagination(items=[51], page=2, per_page=50, total=101)

    assert pagination.pages == 3
    assert pagination.first == 51
    assert pagination.last == 100
    assert pagination.has_prev
    assert pagination.has_next
    assert pagination.prev_num == 1
    assert pagination.next_num == 3


def test_iter_pages_inserts_gaps() -> None:
    """Large page ranges should include ellipsis markers."""
    pagination: Pagination[int] = Pagination(items=[], page=10, per_page=10, total=200)

    assert list(pagination.iter_pages()) == [1, 2, None, 8, 9, 10, 11, 12, None, 19, 20]


def test_build_params_and_clamp_page() -> None:
    """Pagination params should clamp page and size, then clamp to a known total."""
    params = build_pagination_params("-1", per_page=0)

    assert params.page == 1
    assert params.per_page == 1
    assert params.offset == 0
    assert clamp_page(10, total=51, per_page=50) == 2
    assert clamp_page(10, total=0, per_page=50) == 1
