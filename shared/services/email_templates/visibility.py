#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read-only, effective-template views for module administration pages.

Two rules shape this module, and both come from what the pages are *for*. They
exist to explain a template that is behaving unexpectedly, so a single
unexplainable template must never take the page down with it: every key is
rendered on its own and a failure becomes that key's row. And they report state
rather than deriving it, so a backend that cannot describe what it is serving is
reported as unknown rather than guessed to be the packaged default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from shared.services.email_templates.grammar import EmailTemplateError
from shared.services.email_templates.overrides import OverrideState
from shared.services.email_templates.registry import EmailTemplateRegistry

BaselineStatus = Literal["not_applicable", "not_recorded", "matches", "stale"]
TemplateSource = Literal["default", "override", "unknown"]


@runtime_checkable
class DescribesOverrides(Protocol):
    """An override backend that can report the state it is currently serving.

    `EmailTemplateOverrideLookup` requires only `get()`, because rendering needs
    nothing more. Reporting needs more, and asking for it structurally keeps this
    module working for any future backend that offers it without tying it to the
    filesystem one.
    """

    def describe(self) -> tuple[OverrideState, ...]:
        """Return the per-key state this backend is serving."""
        ...


@dataclass(frozen=True)
class EmailTemplateVisibility:
    """One safe, rendered template preview and its current override state.

    Attributes:
        key: Stable template catalogue key.
        subject: Sample-rendered, effective email subject; empty when the
            template could not be rendered.
        body: Sample-rendered, effective plain-text email body; empty when the
            template could not be rendered.
        source: Whether the preview used a packaged default or an override, or
            ``unknown`` when the backend cannot report what it is serving.
        baseline_status: Relationship of the active override's ``based_on``
            metadata to the current packaged default.
        error: A runtime override error retained by this process, if any.
        preview_error: Why this template could not be rendered, if it could not.
            A template in this state would also fail to send.
    """

    key: str
    subject: str
    body: str
    source: TemplateSource
    baseline_status: BaselineStatus
    error: str | None
    preview_error: str | None = None


def inspect_email_templates(
    registry: EmailTemplateRegistry,
    *,
    brand_name: str,
) -> tuple[EmailTemplateVisibility, ...]:
    """Render safe samples and expose the source state already held by a registry.

    Rendering first gives a filesystem source one normal runtime read per key.
    The following ``describe()`` reports that same process's retained state; this
    function deliberately does not read, parse, or validate override files.

    Args:
        registry: Module registry serving outbound mail in this process.
        brand_name: Deployment brand used for the globally declared placeholder.

    Returns:
        One effective-template view per catalogue key, sorted by key. A key that
        cannot be rendered is reported through its own ``preview_error`` rather
        than raising, so one broken template never hides the other rows.
    """
    previews = _render_each(registry, brand_name=brand_name)
    lookup = registry.override_lookup
    states: dict[str, OverrideState] = {}
    describable = isinstance(lookup, DescribesOverrides)
    if isinstance(lookup, DescribesOverrides):
        states = {state.key: state for state in lookup.describe()}
    views: list[EmailTemplateVisibility] = []
    for key in sorted(registry.definitions):
        state = states.get(key)
        subject, body, preview_error = previews[key]
        views.append(
            EmailTemplateVisibility(
                key=key,
                subject=subject,
                body=body,
                source=_source(lookup, describable=describable, state=state),
                baseline_status=_baseline_status(state),
                error=None if state is None else state.error,
                preview_error=preview_error,
            )
        )
    return tuple(views)


def _render_each(
    registry: EmailTemplateRegistry,
    *,
    brand_name: str,
) -> dict[str, tuple[str, str, str | None]]:
    """Render every key independently, keeping each failure to its own key.

    `render_samples()` is all-or-nothing, which is right for a contract test and
    wrong here: a template can pass startup validation and still fail to render
    when substitution pushes the subject or body past its limit, and that is
    precisely the template an administrator opened this page to find.
    """
    rendered: dict[str, tuple[str, str, str | None]] = {}
    for key, definition in registry.definitions.items():
        try:
            preview = registry.render(key, brand_name=brand_name, context=definition.sample_values)
        except EmailTemplateError as exc:
            rendered[key] = ("", "", str(exc))
        else:
            rendered[key] = (preview.subject, preview.body, None)
    return rendered


def _source(lookup: object, *, describable: bool, state: OverrideState | None) -> TemplateSource:
    """Say what a key is rendering from, without guessing.

    A registry with no override backend is definitively serving packaged
    defaults. One whose backend cannot describe itself is not: claiming
    ``default`` there would state, on the page built to be believed, the one
    thing this module cannot know.
    """
    if lookup is None:
        return "default"
    if not describable:
        return "unknown"
    return "override" if state is not None and state.active else "default"


def _baseline_status(state: OverrideState | None) -> BaselineStatus:
    """Classify an active override's baseline metadata against the shipped default."""
    if state is None or not state.active:
        return "not_applicable"
    if state.stale:
        return "stale"
    return "not_recorded" if state.based_on is None else "matches"
