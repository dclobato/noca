#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Regenerate ``docs/BACKLOG.md`` from the Gitea issues labelled ``backlog``.

The Gitea issues are the authoritative text of every backlog contract. This
script renders them into an in-tree index so that a checkout still answers
"what is outstanding, and where is it tracked" without network access, which
is how most work in this repository actually starts.

The index is derived data. Editing ``docs/BACKLOG.md`` by hand is pointless --
the next run overwrites it -- so amend the issue instead. Grouping comes from
the module labels (``autojudge``, ``shared``, ``web``, ``arena``,
``docs-rendering``), status comes from the issue state plus the ``idea`` label,
and the summary is the issue body's first paragraph. Nothing is parsed out of
the previous file, so a hand edit cannot survive and cannot corrupt a run.

Credentials come from the environment, because the repository is private:

- ``GITEA_TOKEN`` -- a personal access token with read access to issues.
- ``GITEA_URL`` -- instance base URL, when it is not the default below.

Run with:

    GITEA_TOKEN=... uv run python scripts/generate_backlog_index.py
    GITEA_TOKEN=... uv run python scripts/generate_backlog_index.py --check
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx

_DEFAULT_URL: Final[str] = "https://git.lobato.org:10880"
_DEFAULT_OWNER: Final[str] = "dclobato"
_DEFAULT_REPO: Final[str] = "noca"
_DEFAULT_OUTPUT: Final[Path] = Path("docs/BACKLOG.md")
_BACKLOG_LABEL: Final[str] = "backlog"
_IDEA_LABEL: Final[str] = "idea"
_PAGE_SIZE: Final[int] = 50
_WRAP_WIDTH: Final[int] = 79
_SUMMARY_CHARS: Final[int] = 400

# Group key -> (heading, ordering). A group key is the frozenset of module
# labels an issue carries, so an item labelled both `web` and `arena` lands in
# "Web and Arena" rather than being listed twice or arbitrarily in one of them.
_GROUPS: Final[tuple[tuple[frozenset[str], str], ...]] = (
    (frozenset({"autojudge"}), "Autojudge"),
    (frozenset({"shared"}), "Shared problem data and packages"),
    (frozenset({"web", "arena"}), "Web and Arena"),
    (frozenset({"web"}), "Web"),
    (frozenset({"arena"}), "Arena"),
    (frozenset({"docs-rendering"}), "Document rendering"),
)
_MODULE_LABELS: Final[frozenset[str]] = frozenset().union(*(k for k, _ in _GROUPS))
_OTHER_HEADING: Final[str] = "Other"


@dataclass(frozen=True)
class BacklogIssue:
    """One backlog issue as the index needs it.

    Attributes:
        number: Issue number.
        title: Issue title, with any ``[module]`` prefix already stripped.
        url: Browser URL of the issue.
        closed: Whether the issue is closed, i.e. the contract has landed.
        labels: Label names carried by the issue.
        summary: First body paragraph, collapsed to a single line.
    """

    number: int
    title: str
    url: str
    closed: bool
    labels: frozenset[str]
    summary: str

    @property
    def status(self) -> str:
        """Return the human-readable status shown in the index."""
        if self.closed:
            return "Implemented"
        if _IDEA_LABEL in self.labels:
            return "Idea -- not yet an accepted contract"
        return "Pending"

    @property
    def group_heading(self) -> str:
        """Return the section heading this issue belongs under."""
        modules = self.labels & _MODULE_LABELS
        for key, heading in _GROUPS:
            if modules == key:
                return heading
        for key, heading in _GROUPS:
            if modules & key:
                return heading
        return _OTHER_HEADING


def _wrap(text: str) -> list[str]:
    """Wrap prose without splitting long tokens.

    Markdown link targets are single unbreakable tokens: breaking one across
    lines silently turns a working link into literal text, so an over-long URL
    is allowed to overflow the wrap width instead.

    Args:
        text: Prose to wrap.

    Returns:
        The wrapped lines.
    """
    return textwrap.wrap(text, width=_WRAP_WIDTH, break_long_words=False, break_on_hyphens=False)


def _relink(text: str, output: Path) -> str:
    """Rebase repository-root-relative Markdown links onto the output file.

    An issue body is rendered by Gitea from the repository root, so its links
    read ``docs/CONFIG.md``. The generated index lives inside ``docs/``, where
    that same target resolves to ``docs/docs/CONFIG.md`` and 404s. Absolute
    URLs and anchors are left alone.

    Args:
        text: Markdown text taken from an issue body.
        output: Path the index is being written to.

    Returns:
        The text with root-relative link targets rebased.
    """
    parent = output.parent.name
    if not parent:
        return text
    return text.replace(f"]({parent}/", "](")


def _strip_module_prefix(title: str) -> str:
    """Drop a leading ``[module]`` tag from an issue title."""
    if title.startswith("[") and "]" in title:
        return title.split("]", 1)[1].strip()
    return title


def _summarize(body: str) -> str:
    """Extract the first real paragraph of an issue body as one line.

    Skips the ``**Status:**`` and ``**Tracking:**`` metadata lines and stops
    before the trailing ``---`` source footer. Markdown headings are never a
    summary: a leading one is skipped so a body that opens with ``## Context``
    still yields its first real paragraph, and a later one bounds the paragraph
    already collected exactly as a blank line does.

    Args:
        body: Raw Markdown issue body.

    Returns:
        A single-line summary, truncated on a word boundary.
    """
    paragraph: list[str] = []
    for raw in body.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("---"):
            break
        if line.startswith("#"):
            if paragraph:
                break
            continue
        if line.startswith(("**Status:**", "**Tracking:**")):
            continue
        if not line:
            if paragraph:
                break
            continue
        paragraph.append(line)
    summary = " ".join(paragraph)
    if summary.endswith(":"):
        # The paragraph introduces the bullet list the index does not carry.
        head, separator, _ = summary.rpartition(". ")
        summary = f"{head}." if separator else f"{summary.rstrip(':').rstrip()}."
    if len(summary) <= _SUMMARY_CHARS:
        return summary
    cut = summary[:_SUMMARY_CHARS].rsplit(" ", 1)[0]
    return f"{cut.rstrip('.,;:')}..."


def _to_issue(payload: dict[str, Any]) -> BacklogIssue:
    """Build a `BacklogIssue` from one Gitea API issue object."""
    labels = frozenset(label["name"] for label in payload.get("labels") or ())
    return BacklogIssue(
        number=int(payload["number"]),
        title=_strip_module_prefix(str(payload["title"])),
        url=str(payload["html_url"]),
        closed=str(payload.get("state")) == "closed",
        labels=labels,
        summary=_summarize(str(payload.get("body") or "")),
    )


def fetch_issues(base_url: str, owner: str, repo: str, token: str) -> list[BacklogIssue]:
    """Fetch every non-pull-request issue labelled ``backlog``.

    Args:
        base_url: Gitea instance base URL.
        owner: Repository owner.
        repo: Repository name.
        token: Personal access token with issue read access.

    Returns:
        The matching issues, ordered by issue number.

    Raises:
        httpx.HTTPStatusError: If the API rejects a request.
    """
    endpoint = f"{base_url.rstrip('/')}/api/v1/repos/{owner}/{repo}/issues"
    headers = {"Authorization": f"token {token}", "Accept": "application/json"}
    collected: list[BacklogIssue] = []
    with httpx.Client(timeout=30.0, headers=headers) as client:
        for page in range(1, 100):
            response = client.get(
                endpoint,
                params={
                    "state": "all",
                    "type": "issues",
                    "labels": _BACKLOG_LABEL,
                    "page": page,
                    "limit": _PAGE_SIZE,
                },
            )
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            collected.extend(_to_issue(item) for item in batch)
            if len(batch) < _PAGE_SIZE:
                break
    return sorted(collected, key=lambda issue: issue.number)


def render_index(issues: list[BacklogIssue], output: Path = _DEFAULT_OUTPUT) -> str:
    """Render the complete ``docs/BACKLOG.md`` text.

    Args:
        issues: Issues to index, in any order.
        output: Path the index will be written to, used to rebase links.

    Returns:
        The full Markdown document, ending in a newline.
    """
    open_issues = [issue for issue in issues if not issue.closed]
    closed_issues = [issue for issue in issues if issue.closed]
    headings = [heading for _, heading in _GROUPS] + [_OTHER_HEADING]

    lines: list[str] = [
        "# Backlog",
        "",
        "<!-- Generated by scripts/generate_backlog_index.py -- do not edit. -->",
        "",
    ]
    lines += _wrap(
        "This is a generated index of the NOCA backlog. The authoritative text "
        "of every contract lives in its Gitea issue, linked from each entry "
        "below; amend the issue, then regenerate this file with "
        "`uv run python scripts/generate_backlog_index.py`."
    )
    lines += ["", f"{len(open_issues)} open, {len(closed_issues)} implemented.", ""]

    for heading in headings:
        section = [issue for issue in open_issues if issue.group_heading == heading]
        if not section:
            continue
        lines += [f"## {heading}", ""]
        for issue in sorted(section, key=lambda issue: issue.number):
            lines += _render_entry(issue, output)

    if closed_issues:
        lines += ["## Implemented", ""]
        lines += _wrap(
            "Contracts that have landed. They are kept here, rather than "
            "dropped, as a pointer to the issue recording what was built."
        )
        lines += [""]
        for issue in sorted(closed_issues, key=lambda issue: issue.number):
            lines += _render_entry(issue, output)

    return "\n".join(lines).rstrip("\n") + "\n"


def _render_entry(issue: BacklogIssue, output: Path) -> list[str]:
    """Render one issue as an index entry."""
    entry = [
        f"### [{issue.title}]({issue.url})",
        "",
        f"**#{issue.number}** -- {issue.status}",
        "",
    ]
    if issue.summary:
        entry += _wrap(_relink(issue.summary, output))
        entry += [""]
    return entry


def main() -> int:
    """Run the generator.

    Returns:
        Process exit status: ``0`` on success, ``1`` when ``--check`` finds the
        committed index stale or when credentials are missing.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--owner", default=_DEFAULT_OWNER)
    parser.add_argument("--repo", default=_DEFAULT_REPO)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when the committed index differs from the issues",
    )
    args = parser.parse_args()

    token = os.environ.get("GITEA_TOKEN", "").strip()
    if not token:
        print("GITEA_TOKEN is not set; cannot read a private repository.", file=sys.stderr)
        return 1
    issues = fetch_issues(os.environ.get("GITEA_URL", _DEFAULT_URL), args.owner, args.repo, token)

    rendered = render_index(issues, args.output)
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != rendered:
            print(f"{args.output} is stale; regenerate it.", file=sys.stderr)
            return 1
        print(f"{args.output} is up to date ({len(issues)} issues).")
        return 0

    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.output} from {len(issues)} issues.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
