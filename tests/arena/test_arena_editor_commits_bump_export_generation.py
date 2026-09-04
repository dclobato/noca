#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Every Arena editor action that commits without the swap bumps the export counter.

The public export and sample ZIP are served from an on-disk cache keyed on
``arena_problems.public_export_generation`` (#204). Saves that go through the
edit swap bump it inside ``open_save_swap``; the actions that skip the swap
because they touch no file -- and Arena's definition editor, which never uses
it -- must bump it themselves, or an edit leaves a stale package cached. This
is the structural form of that rule, so the next such action cannot forget.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EDITOR_ROUTE_MODULES = (
    "arena/routes/admin_problem_validator.py",
    "arena/routes/admin_problem_interaction.py",
    "arena/routes/admin_problem_judgment.py",
    "arena/routes/admin_problem_save.py",
)
# Any of these means the function's commit is the swap's, which already bumps.
SWAP_CALLS = frozenset({"stage_case_action", "commit_with_edit_swap", "apply_case_action", "open_save_swap"})
# A brand-new problem has nothing cached, so its create route need not bump.
EXEMPT = frozenset({"admin_problem_create"})


def _calls(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            target = node.func
            names.add(target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", ""))
    return names


def _commits_without_bump() -> list[str]:
    missing: list[str] = []
    for rel in EDITOR_ROUTE_MODULES:
        tree = ast.parse((PROJECT_ROOT / rel).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef) or fn.name in EXEMPT:
                continue
            calls = _calls(fn)
            if "commit" not in calls or calls & SWAP_CALLS:
                continue
            if "bump_public_export_generation" not in calls:
                missing.append(f"{rel}:{fn.lineno} {fn.name}")
    return missing


def test_the_scan_sees_the_editor_actions() -> None:
    """Guard against a vacuous pass if the modules are renamed or emptied."""
    total = 0
    for rel in EDITOR_ROUTE_MODULES:
        tree = ast.parse((PROJECT_ROOT / rel).read_text(encoding="utf-8"))
        total += sum(1 for fn in ast.walk(tree) if isinstance(fn, ast.AsyncFunctionDef) and "commit" in _calls(fn))
    assert total >= 8, total


def test_every_non_swap_editor_commit_bumps_the_export_generation() -> None:
    missing = _commits_without_bump()
    assert missing == [], "editor commits that do not invalidate the export cache:\n  " + "\n  ".join(missing)
