#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The add-user form renders the declared role matrices beside the role picker.

Rendered rather than asserted against template source, so a matrix that stops
reaching the page -- a renamed global, a dropped include, a partial that raises
on a new cell shape -- fails here instead of silently shipping a page that
offers a role choice with nothing to inform it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, Environment, FileSystemLoader
from markupsafe import escape

from shared.enumerations import ALL_CONTEST_ROLES, RoleEnum
from web.access_matrix import ACCESS_AREAS, CAPABILITY_GROUPS, MatrixActor, all_capabilities
from web.template_globals import ROLE_LABELS, template_globals

_ROOT = Path(__file__).resolve().parents[2]

_EMPTY_FORM = {
    "username": "",
    "fullname": "",
    "role": "",
    "password": "",
    "email": "",
    "site_id": "",
}


class _Url:
    path = "/"


class _Request:
    """Minimal stand-in for the Starlette request the templates reach for."""

    query_params: dict[str, str] = {}
    scope: dict[str, Any] = {}
    url = _Url()

    def url_for(self, name: str, **params: Any) -> str:
        path = params.get("path")
        return f"/{name}/{path}" if path else f"/{name}"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader([str(_ROOT / "web" / "template"), str(_ROOT / "shared" / "template")]),
        undefined=ChainableUndefined,
        autoescape=True,
    )
    env.globals.update(template_globals())
    env.globals["app_version"] = "test"
    env.globals["get_flashed_messages"] = lambda **kwargs: []
    return env


def _contest() -> Any:
    return type(
        "_Contest",
        (),
        {
            "id": "contest-1",
            "login_slug": "slug",
            "contest_name": "Contest",
            "chief_judge_id": None,
            "is_running": True,
            "is_past": False,
            "active": True,
            "upcoming": False,
            "remaining_time_seconds": 0,
        },
    )()


def _render(**overrides: Any) -> str:
    context: dict[str, Any] = {
        "request": _Request(),
        "contest": _contest(),
        "current_user": type("_User", (), {"id": "u-1", "role": RoleEnum.ADMIN.value})(),
        "is_locked": False,
        "success": False,
        "credentials": None,
        "errors": [],
        "form_data": dict(_EMPTY_FORM),
        "sites": [],
        "email_delivery_message": None,
    }
    context.update(overrides)
    return _env().get_template("admin/users/add.html").render(**context)


def test_the_role_select_offers_exactly_the_contest_roles() -> None:
    html = _render()
    for role in ALL_CONTEST_ROLES:
        assert f'value="{role.value}"' in html
        assert ROLE_LABELS[role.value] in html
    # UBERADMIN is not a contest role and must never be offered here.
    assert f'value="{RoleEnum.UBERADMIN.value}"' not in html


def test_the_reference_card_renders_both_matrices() -> None:
    html = _render()
    assert 'id="role-reference"' in html
    assert 'id="role-access-matrix"' in html
    assert 'id="role-capability-matrix"' in html
    for area in ACCESS_AREAS:
        assert str(escape(area.label)) in html
    for group in CAPABILITY_GROUPS:
        assert str(escape(group.label)) in html


def test_every_declared_capability_row_reaches_the_page() -> None:
    """A capability declared but not rendered is a silently incomplete answer.

    Labels are compared escaped, because that is how they reach the page: an
    apostrophe in "Force-release another's lock" renders as `&#39;`.
    """
    html = _render()
    for capability in all_capabilities():
        assert str(escape(capability.label)) in html, capability.key


def test_the_matrices_are_marked_up_for_the_highlight_script() -> None:
    """`role-reference.js` keys off `data-noca-actor`; without it nothing lights up."""
    html = _render()
    for actor in MatrixActor:
        assert f'data-noca-actor="{actor.value}"' in html


def test_the_notes_that_explain_the_surprising_cells_are_shown() -> None:
    html = _render()
    assert "Chief Judge is not a role" in html
    assert "Uber Admin cannot perform attributed work" in html


def test_the_credentials_card_replaces_the_reference_after_a_successful_create() -> None:
    """The two cards share the one right-hand column, so only one may render."""
    credentials = {
        "username": "team01",
        "fullname": "Team One",
        "role": "team",
        "password": "secret",
        "email": "",
        "site": "",
    }
    html = _render(success=True, credentials=credentials)
    assert "User created" in html
    assert 'id="role-reference"' not in html


def test_the_reference_still_renders_for_a_finished_contest() -> None:
    """The form is disabled once the contest ends; the reference stays readable."""
    html = _render(is_locked=True)
    assert "new users cannot be added" in html
    assert 'id="role-reference"' in html


def _render_batch_import(**overrides: Any) -> str:
    context: dict[str, Any] = {
        "request": _Request(),
        "contest": _contest(),
        "current_user": type("_User", (), {"id": "u-1", "role": RoleEnum.ADMIN.value})(),
        "is_locked": False,
        "error": None,
        "result": None,
        "download_json_str": "",
        "email_delivery_summary": None,
    }
    context.update(overrides)
    return _env().get_template("admin/users/batch_import.html").render(**context)


def test_batch_import_documents_exactly_the_contest_roles() -> None:
    """The accepted-values row is generated, so a new role cannot go undocumented."""
    html = _render_batch_import()
    for role in ALL_CONTEST_ROLES:
        assert f"<code>{role.value.lower()}</code>" in html
    assert f"<code>{RoleEnum.UBERADMIN.value.lower()}</code>" not in html


def test_batch_import_role_list_reads_as_a_comma_separated_list() -> None:
    """A generated list still has to punctuate like the hand-written one did.

    The template is reformatted by djlint, which is free to move the separator
    onto its own line; that is exactly how a loop starts rendering "admin ,
    judge" with a space before each comma.
    """
    html = _render_batch_import()
    cell = re.search(r"<code>role</code>\s*</td>.*?<td>(.*?)</td>", html, re.S)
    assert cell is not None
    # Drop the tags without substituting anything, so the only whitespace left
    # is whitespace the browser would actually render, then collapse it the way
    # the browser does.
    text = " ".join(re.sub(r"<[^>]+>", "", cell.group(1)).split())
    assert text == ", ".join(role.value.lower() for role in ALL_CONTEST_ROLES)


def test_batch_import_renders_the_role_reference_with_its_own_intro() -> None:
    """Every row of an import file carries a role choice this reference informs.

    The default lead-in ("Pick a role above") assumes a role picker; this page
    has none, so it must override it rather than instruct the reader to use a
    control that is not there.
    """
    html = _render_batch_import()
    assert 'id="role-reference"' in html
    assert "What each value of the file&#39;s role column" in html
    assert "Pick a role above" not in html
    for capability in all_capabilities():
        assert str(escape(capability.label)) in html, capability.key
