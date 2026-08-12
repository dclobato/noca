#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Guard: every integer route parameter compared with a column must be bounded.

Python integers are unbounded while PostgreSQL ``integer`` is 32-bit, so a bare
``int`` route parameter reaches a query with a value no column can hold. asyncpg
raises ``DataError: value out of int32 range``, which surfaces as a misleading
503 and logs a full SQL statement.

This reads FastAPI's own resolved parameter fields rather than the source text or
``inspect.signature``: every route module uses ``from __future__ import
annotations``, so signature annotations are plain strings and a naive scan
silently inspects nothing at all.  ``route.dependant`` carries the real resolved
annotation and its constraint metadata, which is exactly what FastAPI validates.
"""

from __future__ import annotations

from typing import get_args

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

import animator.main
import arena.main
import healthmonitor.main
import web.main

# Parameters intentionally left unbounded because a downstream helper clamps them
# instead of the request being refused.  Each entry must stay justified:
#
#   arena_problem_list.page -- this public list has always been forgiving about
#   the page number (?page=0 and negatives render page one).  parse_page caps it
#   at MAX_PAGE and clamp_page then bounds it by the real row count, so it cannot
#   reach an OFFSET unbounded.
_CLAMPED_ELSEWHERE = {
    ("arena_problem_list", "page"),
}


def _apps() -> list[tuple[str, FastAPI]]:
    return [
        ("web", web.main.app),
        ("arena", arena.main.app),
        ("animator", animator.main.app),
        ("healthmonitor", healthmonitor.main.app),
    ]


def _mentions_int(annotation: object) -> bool:
    """Return whether an annotation is ``int`` or a union containing ``int``.

    ``int | None`` is just as capable of reaching a query with an oversized value
    as a bare ``int``, so an optional parameter must not slip past this guard.
    """
    if annotation is int:
        return True
    return any(arg is int for arg in get_args(annotation))


def _int_fields(dependant: Dependant) -> list[tuple[str, bool]]:
    """Return (name, has_upper_bound) for every int parameter, sub-deps included."""
    found: list[tuple[str, bool]] = []
    fields = (
        list(dependant.path_params)
        + list(dependant.query_params)
        + list(dependant.header_params)
        + list(dependant.cookie_params)
        + list(dependant.body_params)
    )
    for field in fields:
        info = field.field_info
        if not _mentions_int(getattr(info, "annotation", None)):
            continue
        bounded = any(getattr(m, "le", None) is not None for m in getattr(info, "metadata", []))
        found.append((field.name, bounded))
    for sub in dependant.dependencies:
        found.extend(_int_fields(sub))
    return found


@pytest.mark.parametrize("module_name,app", _apps(), ids=[n for n, _ in _apps()])
def test_every_int_route_parameter_declares_an_upper_bound(module_name: str, app: FastAPI) -> None:
    unbounded: list[str] = []
    inspected = 0
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for name, bounded in _int_fields(route.dependant):
            if (route.endpoint.__name__, name) in _CLAMPED_ELSEWHERE:
                continue
            inspected += 1
            if not bounded:
                unbounded.append(f"{route.path} -> {route.endpoint.__name__}({name})")

    assert unbounded == [], (
        f"{module_name}: int route parameters without an upper bound "
        f"(use shared.http_params.DbId / PageNumber): {unbounded}"
    )
    # Guard the guard: if resolution ever breaks, this test must fail loudly
    # rather than pass by inspecting nothing.
    if module_name in {"web", "arena"}:
        assert inspected > 0, f"{module_name}: resolved no int parameters -- the scan is not working"


@pytest.mark.parametrize("module_name,app", _apps(), ids=[n for n, _ in _apps()])
def test_no_route_uses_the_unbounded_int_path_convertor(module_name: str, app: FastAPI) -> None:
    """Starlette's built-in ``:int`` convertor can raise while matching a route.

    It matches ``[0-9]+`` and then calls ``int(value)``, which CPython refuses
    for a decimal string longer than ``sys.get_int_max_str_digits()`` (4300).
    The resulting ``ValueError`` is raised inside ``Route.matches()``, before any
    handler or dependency, so it escapes the exception handlers entirely and
    becomes an unauthenticated 500 with a traceback.  Use the bounded ``:dbid``
    convertor from ``shared.http_params`` instead.
    """
    offenders = [route.path for route in app.routes if isinstance(route, APIRoute) and ":int}" in route.path]
    assert offenders == [], (
        f"{module_name}: routes using the unbounded ':int' path convertor (use ':dbid' instead): {offenders}"
    )


def test_the_reported_parameter_is_actually_bounded() -> None:
    """Pin the exact parameter from the production incident."""
    for route in arena.main.app.routes:
        if isinstance(route, APIRoute) and route.endpoint.__name__ == "arena_problem_detail":
            fields = dict(_int_fields(route.dependant))
            assert fields.get("arena_number") is True
            return
    pytest.fail("arena_problem_detail route not found")
