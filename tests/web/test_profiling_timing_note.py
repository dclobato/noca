#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The two ceilings the Limits tab states before an author spends a profiling run.

A profiling run costs a container and several minutes and then fails with
nothing but a verdict, so both numbers that can fail it are on the page: the
ceiling for one run of one test case, and the ceiling for all of that case's
repetitions together. The second moves with the selected language, so it is
rendered server-side for the initial selection and kept in step by
``problem-profiling-limits.js`` -- the page is correct with scripting off.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from web.template_globals import template_globals

_ROOT = Path(__file__).resolve().parents[2]


class _Url:
    path = "/"


class _Request:
    query_params: dict[str, str] = {}
    scope: dict[str, Any] = {}
    url = _Url()

    def url_for(self, name: str, **params: Any) -> str:
        path = params.get("path")
        return f"/{name}/{path}" if path else f"/{name}"


def _language(language_id: str, name: str, repetitions: int) -> Any:
    return type(
        "_Language",
        (),
        {"id": language_id, "name": name, "profiling_repetitions_default": repetitions, "icon": language_id},
    )()


def _render(*, cap_seconds: float, repetitions: int | None, languages: list[Any] | None = None) -> str:
    env = Environment(
        loader=FileSystemLoader([str(_ROOT / "web" / "template"), str(_ROOT / "shared" / "template")]),
        undefined=ChainableUndefined,
        autoescape=True,
    )
    env.globals.update(template_globals())
    env.globals["app_version"] = "test"
    if languages is None:
        languages = [_language("python3", "Python 3", 10), _language("cpp20", "C++20", 3)]
    return env.get_template("admin/problems/profiling_limits.html").render(
        request=_Request(),
        contest=type("_Contest", (), {"login_slug": "slug"})(),
        problem=type("_Problem", (), {"id": "problem-1"})(),
        languages=languages,
        limits_map={},
        form_data={},
        is_edit_allowed=True,
        is_limits_edit_allowed=True,
        active_profiling_run=None,
        latest_profiling_run=None,
        profiling_selected_language_id="python3",
        profiling_computed_limits_map={},
        profiling_cap_seconds=cap_seconds,
        profiling_selected_repetitions=repetitions,
    )


def test_both_ceilings_are_stated() -> None:
    """10 s for one run, and 100 s for the ten runs of a python3 test case."""
    markup = _render(cap_seconds=10.0, repetitions=10)

    assert "data-profiling-single-run" in markup
    assert ">10 s<" in markup
    assert "100 s" in markup


def test_the_cap_is_rendered_without_a_trailing_zero() -> None:
    """`10.0 s` reads as false precision on a whole-second ceiling."""
    markup = _render(cap_seconds=10.0, repetitions=3)

    assert "10.0 s" not in markup
    assert "30 s" in markup


def test_a_fractional_cap_survives_formatting() -> None:
    """The %g format must not round a deliberately sub-second cap away."""
    markup = _render(cap_seconds=2.5, repetitions=4)

    assert "2.5 s" in markup
    assert "10 s" in markup


def test_every_language_option_carries_its_repetition_count() -> None:
    """The script reads the count off the option rather than refetching."""
    markup = _render(cap_seconds=10.0, repetitions=10)

    assert 'data-repetitions="10"' in markup
    assert 'data-repetitions="3"' in markup


def test_the_note_is_omitted_when_no_language_can_be_selected() -> None:
    """A contest with no languages has nothing to state, and must not divide by nothing."""
    markup = _render(cap_seconds=10.0, repetitions=None, languages=[])

    assert "profiling-timing-note" not in markup
