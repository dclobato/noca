#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Template reachability shared by the globally-loaded-listener contracts.

Both ``confirm-submit.js`` (``data-confirm``) and ``submit-once.js``
(``data-submit-once``) are single delegated listeners loaded once from each
module's ``_base.html``. Their contracts are the same three questions -- does
every base load it, does anything load a second copy, is every surface
declaring the attribute reachable from a base -- so the walk that answers them
lives here rather than once per script.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_TEMPLATE_ROOTS = {
    "web": REPO_ROOT / "web" / "template",
    "arena": REPO_ROOT / "arena" / "template",
}
SHARED_TEMPLATE_ROOT = REPO_ROOT / "shared" / "template"
JS_ROOTS = (
    REPO_ROOT / "web" / "static" / "js",
    REPO_ROOT / "arena" / "static" / "js",
    REPO_ROOT / "shared" / "static" / "js",
)

SUBMIT_LISTENER_RE = re.compile(r"addEventListener\(\s*[\"']submit[\"']")
SHARED_SCRIPTS_BLOCK_RE = re.compile(
    r"\{%\s*block\s+shared_content_scripts\s*%\}.*?\{%\s*endblock\s*%\}",
    re.DOTALL,
)

_EXTENDS_RE = re.compile(r"\{%\s*extends\s+[\"']([^\"']+)[\"']")
_REFERENCE_RE = re.compile(r"\{%\s*(?:include|from|import)\s+[\"']([^\"']+)[\"']")
# judgment_shell.html picks its body through `{% include page_partials[...] %}`,
# where the quoted template paths live in a {% set %} mapping rather than in the
# include tag itself -- so any quoted `.html` literal counts as a reference edge.
# Over-approximation is deliberate: it only widens coverage, never narrows it.
_QUOTED_TEMPLATE_RE = re.compile(r"[\"']([^\"']+\.html)[\"']")


def include_pattern(script: str) -> re.Pattern[str]:
    """Match a script include, which always goes through ``url_for``'s ``path`` kwarg.

    Matching that form rather than the bare filename keeps Jinja comments and
    prose from counting as an include.
    """
    return re.compile(rf"path=[\"']{re.escape(script)}[\"']")


def read(path: Path) -> str:
    """Return the template source."""
    return path.read_text(encoding="utf-8")


def _resolve(module: str, name: str) -> Path | None:
    """Resolve a referenced template the way the module's ChoiceLoader does.

    The module's own template root wins; ``shared/template`` is the fallback.
    """
    module_path = MODULE_TEMPLATE_ROOTS[module] / name
    if module_path.is_file():
        return module_path
    shared_path = SHARED_TEMPLATE_ROOT / name
    if shared_path.is_file():
        return shared_path
    return None


def covered_templates(module: str) -> set[Path]:
    """Return every template reachable from the module's ``_base.html``.

    A page extending the base loads the listener; everything it (transitively)
    includes or imports macros from is rendered inside that page and shares it.
    The base itself is a root so chrome partials (navbar, footer) count too.
    """
    root = MODULE_TEMPLATE_ROOTS[module]
    stack: list[Path] = [root / "_base.html"]
    for path in root.rglob("*.html"):
        match = _EXTENDS_RE.search(read(path))
        if match and match.group(1) == "_base.html":
            stack.append(path)
    covered: set[Path] = set()
    while stack:
        current = stack.pop()
        if current in covered:
            continue
        covered.add(current)
        text = read(current)
        for reference in _REFERENCE_RE.findall(text) + _QUOTED_TEMPLATE_RE.findall(text):
            resolved = _resolve(module, reference)
            if resolved is not None:
                stack.append(resolved)
    return covered


def templates_declaring(attribute_re: re.Pattern[str], include_re: re.Pattern[str]) -> list[Path]:
    """Repo-relative templates using ``attribute_re`` that no listener reaches.

    Module templates must be reachable from their own module's ``_base.html``;
    shared partials must be reachable from at least one module. A genuinely
    standalone page may include the script itself instead.
    """
    covered = {module: covered_templates(module) for module in MODULE_TEMPLATE_ROOTS}
    offenders: list[Path] = []
    for module, root in MODULE_TEMPLATE_ROOTS.items():
        for path in root.rglob("*.html"):
            text = read(path)
            if not attribute_re.search(text):
                continue
            if path in covered[module] or include_re.search(text):
                continue
            offenders.append(path.relative_to(REPO_ROOT))
    for path in SHARED_TEMPLATE_ROOT.rglob("*.html"):
        text = read(path)
        if not attribute_re.search(text):
            continue
        if any(path in covered[module] for module in covered) or include_re.search(text):
            continue
        offenders.append(path.relative_to(REPO_ROOT))
    return offenders


def templates_double_loading(include_re: re.Pattern[str]) -> list[Path]:
    """Repo-relative covered templates that include the script a second time.

    Only the two ``_base.html`` files and genuinely standalone pages may include
    it; anything reachable from a base already has it.
    """
    offenders: list[Path] = []
    for module in MODULE_TEMPLATE_ROOTS:
        for path in covered_templates(module):
            if path.name == "_base.html":
                continue
            if include_re.search(read(path)):
                offenders.append(path.relative_to(REPO_ROOT))
    return offenders


def competing_listeners(handler_re: re.Pattern[str], owner: Path) -> list[Path]:
    """Repo-relative scripts other than ``owner`` registering the same handler."""
    offenders: list[Path] = []
    for root in JS_ROOTS:
        for path in root.rglob("*.js"):
            if path == owner:
                continue
            text = read(path)
            if SUBMIT_LISTENER_RE.search(text) and handler_re.search(text):
                offenders.append(path.relative_to(REPO_ROOT))
    return offenders
