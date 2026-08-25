//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.core

/**
 * The result of one command attempt.
 *
 * The distinction that matters is [StatedRefusal] versus [Ambiguous]. A refusal
 * the server *described* — `400`, `403`, `404`, `409`, `422` — changed nothing:
 * no fenced save ran, no event was published, and the operator may correct and
 * resend at once. Anything else — no HTTP status at all, or any `5xx` including
 * the reveal store's `503` — may have arrived *after* a fenced save committed,
 * so the command's effect is genuinely unknown and the controls must lock until
 * authoritative state is reloaded. Collapsing these two into one "error" is
 * exactly how an operator double-reveals a team in front of an audience.
 */
sealed interface CommandOutcome {

    /** The server applied (or replayed) the command and returned a projection. */
    data class Confirmed(val projection: RevealProjection) : CommandOutcome

    /**
     * The credential has no ceremony yet — `GET /control/state` answered `404`.
     *
     * Not an error: it is the honest answer before `start-reveal`.
     */
    data object NoCeremony : CommandOutcome

    /** The server refused and said why; nothing was applied. */
    data class StatedRefusal(val status: Int, val detail: String?) : CommandOutcome

    /**
     * The command's effect is unknown.
     *
     * @property status The `5xx` status, or `null` when no response arrived.
     * @property cause Diagnostic text for the operator-facing banner.
     */
    data class Ambiguous(val status: Int?, val cause: String) : CommandOutcome

    /** The credential was rejected; it has been forgotten and must be re-entered. */
    data object AuthFailure : CommandOutcome

    /**
     * Nothing was sent.
     *
     * Either no credential is held, another command is in flight, or the client
     * is locked after an ambiguous outcome. A suppressed command reached no
     * network at all, which is what makes the lock a real safety property rather
     * than a UI hint.
     */
    data object Suppressed : CommandOutcome

    /**
     * The stored ceremony state cannot be read (`500`, unusable session).
     *
     * Recoverable only by `start-reveal` with `restart=true`, which replaces the
     * state instead of loading it.
     */
    data class StateUnusable(val detail: String) : CommandOutcome

    /**
     * A state *read* failed.
     *
     * Kept apart from [Ambiguous] because a read cannot have mutated anything:
     * it leaves the controls hidden until a successful reload, but it never sets
     * the ambiguous-outcome lock.
     */
    data class StateLoadFailed(val status: Int?, val cause: String) : CommandOutcome
}

/**
 * The result of a multi-step sequence such as "Step 10".
 *
 * @property requested How many single commands were asked for.
 * @property completed How many were confirmed before the sequence stopped.
 * @property last The outcome that ended the sequence.
 */
data class SequenceOutcome(
    val requested: Int,
    val completed: Int,
    val last: CommandOutcome,
)

/** Whether this process may issue ceremony mutations. */
enum class ControllerLeaseState {
    UNCLAIMED,
    ACTIVE,
    READ_ONLY,
    LOST,
    UNAVAILABLE,
}

/** Result of a controller-lease operation. */
sealed interface LeaseOutcome {
    data class Active(val lease: ControllerLeaseResponse) : LeaseOutcome
    data object ReadOnly : LeaseOutcome
    data object Lost : LeaseOutcome
    data class Unavailable(val cause: String) : LeaseOutcome
    data object AuthFailure : LeaseOutcome
    data object Released : LeaseOutcome
    data object Suppressed : LeaseOutcome
}

/**
 * Consecutive heartbeat blips (transport failure or `503`) tolerated inside the
 * lease TTL before the ticker gives up and surfaces an ownership problem.
 *
 * The server's validator requires `TTL >= 3x heartbeat`, so two missed ticks
 * (~20 s at the default 10 s cadence) still leave comfortable margin before a
 * 45 s lease expires — a one-second network blip must not cost command
 * authority plus a manual recovery click.
 */
const val MAX_MISSED_HEARTBEATS: Int = 2

/** What the heartbeat ticker should do after one renewal attempt. */
enum class HeartbeatStep {
    /** Renewal confirmed; reset the miss counter and keep the cadence. */
    CONFIRMED,

    /** No authoritative answer (transport busy with a command, or a blip still inside the miss budget); retry next tick. */
    RETRY,

    /** Ownership ended or is unverifiable within the budget; surface the outcome and stop the loop. */
    TERMINATE,
}

/**
 * Pure decision for the heartbeat loop, so the "a suppressed renewal must not
 * kill the ticker" rule is testable on a plain JVM without Android types.
 */
fun heartbeatStep(outcome: LeaseOutcome, missedHeartbeats: Int): HeartbeatStep = when {
    outcome is LeaseOutcome.Active -> HeartbeatStep.CONFIRMED
    outcome is LeaseOutcome.Suppressed -> HeartbeatStep.RETRY
    outcome is LeaseOutcome.Unavailable && missedHeartbeats < MAX_MISSED_HEARTBEATS -> HeartbeatStep.RETRY
    else -> HeartbeatStep.TERMINATE
}
