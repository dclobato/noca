#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""No route may open a second database session beside the one its dependency holds.

Both engines run SQLAlchemy's stock pool: fifteen connections, and a thirty-second
wait for the sixteenth. A route whose dependency already holds a ``get_db``
session -- every authenticated route, since the auth query checks the
connection out -- and that *also* opens ``request.app.state.db_session()``
holds two connections for the whole request, and FastAPI keeps the first one
open until the response has been fully sent, streaming bodies included. #196
found this on the user-media routes; #198 found it on twenty-two more.

This test is the structural form of that fix. It walks every route module in
both HTTP applications and fails on any function that declares a
session-holding dependency yet opens a session of its own. The public routes
that open a manual session with *no* dependency session are left alone: they
hold one connection, which is the whole point.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTE_DIRS = (PROJECT_ROOT / "web" / "routes", PROJECT_ROOT / "arena" / "routes")

# Dependencies that hold a ``get_db`` session for the request, directly or
# through one they depend on, plus the ``Annotated`` aliases they are declared
# through. ``FlashDep`` and the like are deliberately absent: they hold no
# session, so a public route may combine them with a manual one.
SESSION_HOLDING_DEPENDENCIES = frozenset(
    {
        "UberAdminDep",
        "UserMediaContextDep",
        "get_db",
        "get_uberadmin",
        "get_contest_context",
        "get_contest_admin_context",
        "get_current_user",
        "get_user_media_context",
        "get_current_arena_user",
        "require_arena_user",
        "require_arena_admin",
        "require_arena_problem_editor",
        "require_arena_judge",
        "require_arena_teacher",
    }
)
MANUAL_SESSION_ATTRS = frozenset({"db_session", "arena_db_session"})


def _opens_manual_session(fn: ast.AST) -> int | None:
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in MANUAL_SESSION_ATTRS
        ):
            return node.lineno
    return None


def _dependency_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    params = fn.args.args + fn.args.kwonlyargs
    for default in fn.args.defaults + [d for d in fn.args.kw_defaults if d is not None]:
        if isinstance(default, ast.Call) and getattr(default.func, "id", None) == "Depends" and default.args:
            names.add(ast.unparse(default.args[0]))
    for param in params:
        ann = param.annotation
        if isinstance(ann, ast.Name) and ann.id.endswith("Dep"):
            names.add(ann.id)
        elif ann is not None:
            for node in ast.walk(ann):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Depends" and node.args:
                    names.add(ast.unparse(node.args[0]))
    return names


def _holds_a_session(names: set[str]) -> bool:
    return any(name in SESSION_HOLDING_DEPENDENCIES for name in names)


def _nested_sites() -> list[str]:
    found: list[str] = []
    for route_dir in ROUTE_DIRS:
        for path in sorted(route_dir.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                line = _opens_manual_session(fn)
                if line is None:
                    continue
                if _holds_a_session(_dependency_names(fn)):
                    found.append(f"{path.relative_to(PROJECT_ROOT)}:{line} {fn.name}")
    return found


def test_the_scan_still_sees_the_single_session_public_routes() -> None:
    """Guard against a vacuous pass: the public routes that legitimately open one session."""
    manual: list[str] = []
    for route_dir in ROUTE_DIRS:
        for path in sorted(route_dir.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and _opens_manual_session(fn) is not None:
                    manual.append(fn.name)
    # auth.py, profile.py, root.py, announcements.py and problem_set.py each hold
    # exactly one session because they carry no dependency session.
    assert len(manual) >= 10, manual


def test_no_route_opens_a_second_session_beside_its_dependency() -> None:
    nested = _nested_sites()
    assert nested == [], "routes holding two request sessions:\n  " + "\n  ".join(nested)
