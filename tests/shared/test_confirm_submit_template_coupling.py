#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Static contract pinning the shared ``confirm-submit.js`` / ``data-confirm`` wiring.

``data-confirm`` on a form is honoured by exactly one delegated listener, living in
``shared/static/js/confirm-submit.js`` and loaded unconditionally by both modules'
``_base.html``. Both failure modes this guards against are silent: a surface without
the listener submits with no confirmation at all (issue #32), and a surface with two
listeners asks the same question twice.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_TEMPLATE_ROOTS = {
    "web": _REPO_ROOT / "web" / "template",
    "arena": _REPO_ROOT / "arena" / "template",
}
_SHARED_TEMPLATE_ROOT = _REPO_ROOT / "shared" / "template"
_JS_ROOTS = (
    _REPO_ROOT / "web" / "static" / "js",
    _REPO_ROOT / "arena" / "static" / "js",
    _REPO_ROOT / "shared" / "static" / "js",
)
_SHARED_CONFIRM_SCRIPT = _REPO_ROOT / "shared" / "static" / "js" / "confirm-submit.js"

# A script include always goes through url_for's `path` kwarg in these templates;
# matching that form (never the bare word) keeps Jinja comments from counting.
_INCLUDE_RE = re.compile(r"path=[\"']confirm-submit\.js[\"']")
_EXTENDS_RE = re.compile(r"\{%\s*extends\s+[\"']([^\"']+)[\"']")
_REFERENCE_RE = re.compile(r"\{%\s*(?:include|from|import)\s+[\"']([^\"']+)[\"']")
# judgment_shell.html picks its body through `{% include page_partials[...] %}`,
# where the quoted template paths live in a {% set %} mapping rather than in the
# include tag itself -- so any quoted `.html` literal counts as a reference edge.
# Over-approximation is deliberate: it only widens coverage, never narrows it.
_QUOTED_TEMPLATE_RE = re.compile(r"[\"']([^\"']+\.html)[\"']")
_DATA_CONFIRM_RE = re.compile(r"data-confirm=")
_SUBMIT_LISTENER_RE = re.compile(r"addEventListener\(\s*[\"']submit[\"']")
_CONFIRM_HANDLER_RE = re.compile(r"form\[data-confirm\]|dataset\.confirm")
_SHARED_SCRIPTS_BLOCK_RE = re.compile(
    r"\{%\s*block\s+shared_content_scripts\s*%\}.*?\{%\s*endblock\s*%\}",
    re.DOTALL,
)


def _read(path: Path) -> str:
    """Return the template source."""
    return path.read_text(encoding="utf-8")


def _resolve(module: str, name: str) -> Path | None:
    """Resolve a referenced template the way the module's ChoiceLoader does.

    The module's own template root wins; ``shared/template`` is the fallback.
    """
    module_path = _MODULE_TEMPLATE_ROOTS[module] / name
    if module_path.is_file():
        return module_path
    shared_path = _SHARED_TEMPLATE_ROOT / name
    if shared_path.is_file():
        return shared_path
    return None


def _covered_templates(module: str) -> set[Path]:
    """Return every template reachable from the module's ``_base.html``.

    A page extending the base loads the listener; everything it (transitively)
    includes or imports macros from is rendered inside that page and shares it.
    The base itself is a root so chrome partials (navbar, footer) count too.
    """
    root = _MODULE_TEMPLATE_ROOTS[module]
    stack: list[Path] = [root / "_base.html"]
    for path in root.rglob("*.html"):
        match = _EXTENDS_RE.search(_read(path))
        if match and match.group(1) == "_base.html":
            stack.append(path)
    covered: set[Path] = set()
    while stack:
        current = stack.pop()
        if current in covered:
            continue
        covered.add(current)
        text = _read(current)
        references = _REFERENCE_RE.findall(text) + _QUOTED_TEMPLATE_RE.findall(text)
        for reference in references:
            resolved = _resolve(module, reference)
            if resolved is not None:
                stack.append(resolved)
    return covered


def test_both_base_templates_load_the_listener() -> None:
    """Each module's ``_base.html`` loads the shared script."""
    for module, root in _MODULE_TEMPLATE_ROOTS.items():
        base = root / "_base.html"
        assert _INCLUDE_RE.search(_read(base)), (
            f"{module}/template/_base.html must load confirm-submit.js via static_shared_js"
        )


def test_web_base_loads_the_listener_outside_the_overridable_block() -> None:
    """The login pages replace ``shared_content_scripts`` without ``super()``.

    An include inside that block would vanish on those pages, so the reference
    must survive stripping the whole block region out of the base template.
    """
    text = _read(_MODULE_TEMPLATE_ROOTS["web"] / "_base.html")
    stripped, count = _SHARED_SCRIPTS_BLOCK_RE.subn("", text)
    assert count == 1, "web/_base.html must define the shared_content_scripts block"
    assert _INCLUDE_RE.search(stripped), (
        "confirm-submit.js must be included outside the overridable "
        "shared_content_scripts block, or the login pages lose it"
    )


def test_no_covered_template_loads_a_second_copy() -> None:
    """Two live listeners ask the same question twice.

    Only the two ``_base.html`` files and genuinely standalone pages may include
    the script; anything reachable from a base already has it.
    """
    offenders = []
    for module in _MODULE_TEMPLATE_ROOTS:
        for path in _covered_templates(module):
            if path.name == "_base.html":
                continue
            if _INCLUDE_RE.search(_read(path)):
                offenders.append(path.relative_to(_REPO_ROOT))
    assert not offenders, f"templates double-loading confirm-submit.js: {offenders}"


def test_every_data_confirm_surface_is_covered() -> None:
    """A form declaring ``data-confirm`` must render where the listener exists.

    Module templates must be reachable from their own module's ``_base.html``;
    shared partials must be reachable from at least one module. A genuinely
    standalone page may include the script itself instead. This is also what
    guards the arena ``_auth_base.html`` / ``errors/_error_base.html``
    hierarchies, which the global listener does not reach.
    """
    covered = {module: _covered_templates(module) for module in _MODULE_TEMPLATE_ROOTS}
    offenders = []
    for module, root in _MODULE_TEMPLATE_ROOTS.items():
        for path in root.rglob("*.html"):
            text = _read(path)
            if not _DATA_CONFIRM_RE.search(text):
                continue
            if path in covered[module] or _INCLUDE_RE.search(text):
                continue
            offenders.append(path.relative_to(_REPO_ROOT))
    for path in _SHARED_TEMPLATE_ROOT.rglob("*.html"):
        text = _read(path)
        if not _DATA_CONFIRM_RE.search(text):
            continue
        if any(path in covered[module] for module in covered) or _INCLUDE_RE.search(text):
            continue
        offenders.append(path.relative_to(_REPO_ROOT))
    assert not offenders, f"templates declaring data-confirm= with no listener reaching them: {offenders}"


def test_no_other_script_registers_a_competing_listener() -> None:
    """The delegated ``data-confirm`` submit handler exists exactly once."""
    offenders = []
    for root in _JS_ROOTS:
        for path in root.rglob("*.js"):
            if path == _SHARED_CONFIRM_SCRIPT:
                continue
            text = _read(path)
            if _SUBMIT_LISTENER_RE.search(text) and _CONFIRM_HANDLER_RE.search(text):
                offenders.append(path.relative_to(_REPO_ROOT))
    assert not offenders, f"competing data-confirm submit listeners: {offenders}"
