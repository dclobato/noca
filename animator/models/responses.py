#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Typed public response models for the animator feed.

These models define the exact, presentation-safe surface of the animator feed.
Only the fields declared here are serialized: no operator secrets, secret
digests, credentials, emails, or unrelated contest configuration are ever
exposed. Response builders live in :mod:`animator.services.contest_feed_service`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from animator.models.reveal_session import (
    MedalCutoffs,
    NextRevealCell,
    RevealSessionState,
    TeamRevealView,
)
from shared.reveal_schema import GLOBAL_SCOPE, RevealPhase
from shared.services.balloon_assets import MedalBand


class ProblemMeta(BaseModel):
    """Presentation metadata for one problem."""

    problem_id: str
    ordinal: int
    label: str
    balloon_color: str


class SiteMeta(BaseModel):
    """Presentation summary for one contest site (venue)."""

    site_id: str
    name: str
    gold_cutoff: int
    silver_cutoff: int
    bronze_cutoff: int
    team_count: int


class ContestMetaResponse(BaseModel):
    """Contest identity, problem labels/colors, timing, freeze state, sites.

    ``problems`` is **empty** until the contest starts. How many problems a
    contest has and which balloon colors they carry are secrets the Web layer
    already withholds before the start instant, and this anonymous feed must not
    be the way around that gate. ``has_started`` is what tells a client the
    difference between "no problems yet published" and "this contest has none",
    so it can show the pre-start banner instead of an empty board.
    """

    contest_id: str
    slug: str
    name: str
    start_time: str
    end_time: str
    freeze_at: str
    is_frozen: bool
    has_started: bool
    problems: list[ProblemMeta]
    sites: list[SiteMeta]


class ProblemCellResponse(BaseModel):
    """Scoreboard cell for one team and problem."""

    label: str
    problem_id: str
    solved: bool
    attempts: int
    solved_at_minutes: int | None
    penalty: int
    is_pending: bool
    is_first_balloon: bool


class TeamStandingResponse(BaseModel):
    """Scoreboard row for one team.

    ``medal`` is the band this row's rank falls into under the cutoffs in force
    for the requested scope, or ``None`` when the row wins no medal or the scope
    has no cutoffs configured.

    ``absent`` marks a team that has not signed in since the contest
    started, so the board can show the venue staff an empty seat. It lives on
    this module-owned response rather than on the shared ``TeamStanding``
    because that projection is cached in snapshots that are written once and
    never invalidated -- a presence flag stored inside one would freeze with the
    standings. It is always ``False`` outside a running contest: before the
    start nobody is late, and afterwards an absence is history.
    """

    rank: int
    team_id: str
    team_name: str
    team_fullname: str
    site_name: str | None
    problems_solved: int
    total_time: int
    problems: dict[str, ProblemCellResponse]
    medal: MedalBand | None = None
    absent: bool = False


class PendingSubmissionResponse(BaseModel):
    """One team's unresolved, freeze-safe submission awaiting a verdict."""

    submission_id: str
    team_id: str
    problem_id: str
    team_name: str
    problem_label: str
    created_at: str


# The four sentences the activity rail can compose. Named so the builder can
# annotate its choice and mypy checks it against this model's field.
RecentEventKind = Literal["submitted", "verdict", "balloon", "first"]


class RecentEventResponse(BaseModel):
    """One past contest event, for seeding a freshly loaded activity rail.

    Carries the *parts* of the sentence rather than the sentence: the client
    composes the wording so a seeded backlog and the live SSE stream cannot drift
    into two vocabularies. ``key`` is the same identity the live handlers use
    (``submission:<id>`` / ``verdict:<judgment_id>``), so a page that seeds and
    then receives the same event renders it once.

    Attributes:
        key: Stable de-duplication identity shared with the live stream.
        minute: Contest minute the submission was made at.
        kind: ``submitted`` (still unjudged), ``first`` (first solve of the
            problem), ``balloon`` (this team's solve), or ``verdict`` (any other
            judged result).
        team_name: Short team name (the team's login).
        team_fullname: Full team name, which is what the rail displays.
        problem_label: Spreadsheet-style problem label.
        verdict: Verdict code, present on every judged kind and ``None`` for
            ``submitted``.
    """

    key: str
    minute: int
    kind: RecentEventKind
    team_name: str
    team_fullname: str
    problem_label: str
    verdict: str | None = None


class ScoreboardSnapshotResponse(BaseModel):
    """Full scoreboard snapshot plus a refresh version token.

    Before the contest starts every payload field is empty -- no problem labels,
    no balloon colors, no standings, no pending submissions, no recent events --
    because the problem set is a contest secret until then. ``has_started`` carries that
    state explicitly so a client renders the pre-start banner rather than
    mistaking the gate for a contest with no teams.
    """

    contest_id: str
    generated_at: str
    version: str
    is_frozen: bool
    has_started: bool
    problems: list[str]
    balloon_colors: list[str]
    standings: list[TeamStandingResponse]
    pending_submissions: list[PendingSubmissionResponse] = []
    recent_events: list[RecentEventResponse] = []


# ----------------------------------------------------------------------------
# Live SSE event payloads (see animator/services/event_stream_service.py)
# ----------------------------------------------------------------------------


class VerdictPayload(BaseModel):
    """Public ``verdict`` SSE payload: identifies a finalized judgment, no logs."""

    submission_id: str
    judgment_id: str
    problem_id: str | None
    team_id: str | None
    verdict: str
    update_kind: str | None


class RedactedVerdictPayload(BaseModel):
    """Frozen-contest ``verdict`` payload: no team/problem/verdict is revealed."""

    redacted: bool = True


class SubmissionPayload(BaseModel):
    """Public ``submission`` SSE payload: a low-latency new-submission nudge.

    No redacted variant exists: frozen contests emit no ``submission`` event at
    all, so nothing about post-freeze activity leaks.
    """

    submission_id: str
    team_id: str
    problem_id: str


class ScoreboardRefreshPayload(BaseModel):
    """Bare ``scoreboard_refresh`` signal to refetch the authoritative snapshot."""


class TimerTickPayload(BaseModel):
    """``timer_tick`` payload carrying UTC server time only."""

    server_time: str


# ----------------------------------------------------------------------------
# Reveal ceremony projection (shared by the control API and the spectator feed)
# ----------------------------------------------------------------------------


class RevealProjectionResponse(BaseModel):
    """The safe, public projection of one reveal ceremony.

    This is deliberately **not** :class:`RevealSessionState`. That model is the
    persisted machine state and carries ``frozen_submission_ids`` and
    ``reveal_log`` — the ordered identities of post-freeze submissions, including
    the ones *not yet revealed*. Serializing it would hand a spectator (or an
    operator's browser console) the shape of the unrevealed remainder and, with
    a second request, let them infer results the ceremony has not reached yet.
    Only counts, phase, focus, and the already-derived team views leave the
    server.

    Both the operator control API and the public spectator feed return this one
    model, so the two views of a ceremony cannot drift.

    Attributes:
        contest_id: Contest the ceremony belongs to.
        scope: Canonical scope — the site id, or ``"global"``. Derived here, so
            no caller-supplied scope is ever echoed back as authoritative.
        site_id: Site being revealed; ``None`` for a global ceremony.
        site_name: Display name of ``site_id``; ``None`` iff global.
        phase: Ceremony phase.
        focused_team_id: The team currently in focus, if any.
        revealed_count: How many frozen submissions have been revealed.
        frozen_count: Size of the immutable frozen universe.
        medal_cutoffs: The cutoffs in force for this ceremony -- the site's for a
            site ceremony, the contest's global ones for a global ceremony, and
            ``None`` when the global cutoffs are unconfigured.
        teams: Derived team views in current ranking order.
        next_cell: The cell the next ``step`` will change — **position only**, no
            verdict — so a projector can draw the audience's attention to it.
            ``None`` while idle or done.
    """

    contest_id: str
    scope: str
    site_id: str | None
    site_name: str | None
    phase: RevealPhase
    focused_team_id: str | None
    revealed_count: int
    frozen_count: int
    medal_cutoffs: MedalCutoffs | None
    teams: list[TeamRevealView]
    next_cell: NextRevealCell | None = None

    @classmethod
    def from_projection(
        cls,
        state: RevealSessionState,
        teams: Sequence[TeamRevealView],
        next_cell: NextRevealCell | None = None,
    ) -> RevealProjectionResponse:
        """Build the safe projection from a state and its derived views.

        Args:
            state: The current (already persisted) session state.
            teams: Team views derived for exactly that state.
            next_cell: The cell the next step will change, when known.

        Returns:
            The response model, carrying counts instead of submission ids.
        """
        return cls(
            contest_id=state.contest_id,
            scope=state.site_id if state.site_id is not None else GLOBAL_SCOPE,
            site_id=state.site_id,
            site_name=state.site_name,
            phase=state.phase,
            focused_team_id=state.focused_team_id,
            revealed_count=len(state.reveal_log),
            frozen_count=len(state.frozen_submission_ids),
            medal_cutoffs=state.medal_cutoffs,
            teams=list(teams),
            next_cell=next_cell,
        )


class RevealPublicStateResponse(BaseModel):
    """The spectator view of one ceremony scope, including "nothing yet".

    A spectator can ask about a scope no operator has opened, so the response is
    an envelope rather than a bare projection: identity is always present and
    ``projection`` is ``null`` until a session exists.

    ``has_session`` is deliberately **not** named ``started``. It reports one
    thing only — whether a durable session is stored for this scope — and says
    nothing about how far the ceremony has progressed. After ``reset`` a session
    still exists with ``phase="idle"``, which a field called ``started`` would
    describe ambiguously. Clients read progress from ``projection.phase``
    (``idle`` / ``revealing`` / ``done``), never from this flag.

    Attributes:
        has_session: Whether durable state exists for this contest and scope.
        contest_id: Contest the scope belongs to.
        scope: Canonical scope — the site id, or ``"global"``.
        site_id: Site being watched; ``None`` for the global ceremony.
        site_name: Display name of ``site_id``; ``None`` iff global.
        projection: The safe projection, or ``None`` when ``has_session`` is false.
    """

    has_session: bool
    contest_id: str
    scope: str
    site_id: str | None
    site_name: str | None
    projection: RevealProjectionResponse | None = None


class RevealReadyPayload(BaseModel):
    """``reveal_ready`` payload: the ceremony subscription is live.

    Emitted once per SSE connection, **after** the Valkey subscription is
    established, so a client that reconciles here cannot race the subscription
    the way one reconciling on ``EventSource.onopen`` can (the response headers —
    and therefore ``open`` — precede the subscribe). It carries no state: like
    every other reveal event it means "refetch the authoritative projection".
    """

    ready: bool = True
