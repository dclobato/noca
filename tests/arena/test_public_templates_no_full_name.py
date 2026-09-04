#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Structural guards that keep a legal name off Arena's public surfaces.

This is deliberately **not** an allowlist of public templates. An allowlist only
holds if the contributor adding a public template also remembers to edit it --
which is exactly the failure it would exist to prevent. Instead every template
is scanned, the ones with a stated non-public basis are subtracted, and anything
left over fails. A new template is unclassified by default and breaks the build
until someone says which side of the line it is on.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ARENA = Path(__file__).resolve().parents[2] / "arena"
_TEMPLATE_DIR = _ARENA / "template"
_SERVICES = _ARENA / "services"

# Any ``<something>.nome`` in a template, capturing the variable it hangs off.
_NOME_REFERENCE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.nome\b")

# Variables that name the **viewer themself**. Showing you your own legal name
# is not a disclosure -- it is the whole point of your own profile page.
_SELF_VARIABLES = frozenset({"current_user"})

# Templates with a stated non-public basis for rendering someone's legal name.
# Each entry must carry a reason; an unexplained entry is not a classification.
_PRIVATE_TEMPLATES: dict[str, str] = {
    # Admin moderation surfaces: an admin acts on named accounts by definition,
    # and the age shield is a publication rule, not a moderation barrier.
    "admin/user_profile.html": "admin moderation of a named account",
    "admin/_user_parental_consent_modal.html": "admin confirms consent action on a named account",
    "admin/_user_google_unlink_modal.html": "admin confirms unlinking Google from a named account",
    "admin/_user_unlock_modal.html": "admin confirms lifting the sign-in lockout of a named account",
    "admin/user_list.html": "admin moderation of named accounts",
    "admin/problem_list.html": "admin sees the problem owner",
    "admin/dashboard_login_history.html": "admin audit trail",
    "admin/dashboard_ai_usage.html": "admin cost attribution",
    # Author credit is out of scope per #121: the name is shown only where its
    # owner opted into authorship credit, guarded by ``author_is_owner``.
    "_partials/problem_tab_metadata.html": "problem author credit, opt-in by the owner",
    # The AI-credit ledger names the *staff member* who granted the credit, so
    # the user can see who acted on their account. Not a participant identity.
    "users/_credits_list.html": "names the granting admin, not a participant",
}


def _template_files() -> list[Path]:
    """Return every Arena HTML template, sorted for a stable failure message."""
    return sorted(_TEMPLATE_DIR.rglob("*.html"))


def test_no_unclassified_template_renders_a_users_legal_name() -> None:
    """Every ``.nome`` in a template is either self-display or classified private.

    A public template that reaches for ``nome`` bypasses the shield entirely:
    the resolved name lives on ``public_display_name`` and on the ranking
    dataclasses, and nothing else may be rendered.
    """
    offenders: list[str] = []
    for path in _template_files():
        relative = path.relative_to(_TEMPLATE_DIR).as_posix()
        if relative in _PRIVATE_TEMPLATES:
            continue
        for variable in _NOME_REFERENCE.findall(path.read_text(encoding="utf-8")):
            if variable in _SELF_VARIABLES:
                continue
            offenders.append(f"{relative}: {variable}.nome")

    assert not offenders, (
        "These templates render a user's legal name without a stated basis:\n  "
        + "\n  ".join(offenders)
        + "\n\nRender `public_display_name` (or a resolved dataclass field) instead. "
        "If the surface genuinely is private, add it to _PRIVATE_TEMPLATES with a reason."
    )


def test_private_template_classifications_still_point_at_real_files() -> None:
    """A stale exemption is a hole: it would silently excuse a renamed template."""
    missing = [name for name in _PRIVATE_TEMPLATES if not (_TEMPLATE_DIR / name).is_file()]

    assert not missing, f"_PRIVATE_TEMPLATES names templates that no longer exist: {missing}"


def test_private_template_classifications_all_carry_a_reason() -> None:
    """An exemption without a reason is not a classification."""
    unexplained = [name for name, reason in _PRIVATE_TEMPLATES.items() if not reason.strip()]

    assert not unexplained, f"These exemptions state no reason: {unexplained}"


def test_live_feed_selects_no_user_name() -> None:
    """The public live feed shows affiliation and country, never a name.

    It is correct today; this pins it, because the feed is anonymous and adding
    a name here would reopen the leak the shield exists to close.
    """
    source = (_SERVICES / "live_feed_service.py").read_text(encoding="utf-8")

    assert "arena_users.c.nome" not in source
    assert "ArenaUser.nome" not in source


def _calls_to(tree: ast.AST, function_name: str) -> bool:
    """Return whether ``tree`` contains a call to ``function_name``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == function_name:
            return True
    return False


def test_relevance_ordering_has_no_public_caller() -> None:
    """``user_relevance_ordering`` orders on ``nome`` and is deliberately unshielded.

    That is safe only while its callers are teacher-scoped, where looking a
    student up by the name on the roll has a legitimate basis. This asserts the
    caller set with an AST scan, so a public surface adopting it fails here
    rather than silently ordering the public ranking by real-name similarity.
    """
    permitted = {"arena_class_detail_service.py", "identity_search_service.py"}
    callers = sorted(
        path.name
        for path in _ARENA.rglob("*.py")
        if path.name not in permitted
        and _calls_to(ast.parse(path.read_text(encoding="utf-8")), "user_relevance_ordering")
    )

    assert not callers, (
        f"user_relevance_ordering() gained callers outside the teacher-scoped path: {callers}. "
        "A public surface needs an age-shielded ordering sibling, not this one."
    )
