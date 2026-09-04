//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.core

/**
 * Which controls the current projection makes meaningful.
 *
 * A direct port of `controlsForState` in `animator/static/js/control.js`, kept
 * name-for-name so the two clients can be diffed. It is the single source of
 * truth for the panel: the button pad and any keyboard/hardware shortcut both
 * consume it, so a control can never be reachable by one route and hidden on
 * another.
 */
data class ControlVisibility(
    val startVisible: Boolean,
    val startOverVisible: Boolean,
    val resetVisible: Boolean,
    val stepVisible: Boolean,
    val backVisible: Boolean,
    val jumpVisible: Boolean,
    val jumpPendingVisible: Boolean,
    val mediaVisible: Boolean,
) {
    companion object {
        /** Nothing is actionable — an unreadable or unloadable ceremony state. */
        val NONE = ControlVisibility(
            startVisible = false,
            startOverVisible = false,
            resetVisible = false,
            stepVisible = false,
            backVisible = false,
            jumpVisible = false,
            jumpPendingVisible = false,
            mediaVisible = false,
        )
    }
}

/**
 * Maps a projection to the controls that make sense for it.
 *
 * `Back` is intentionally **not** gated on `revealed_count`: a step can be a
 * pure cursor move with no reveal, so "0 revealed" does not mean "nothing to
 * undo", and the projection deliberately exposes no step-log length to compute
 * it from. `back` on an empty trail is a harmless no-op, so it stays available
 * whenever a session is past `idle`.
 *
 * @param projection The last projection the server confirmed, or `null` when no
 *   ceremony exists yet for this credential.
 * @param stateUnusable The stored state could not be read (a `500` naming an
 *   unusable session); only "Rebuild state" can help, and it is offered
 *   separately.
 * @param stateLoadFailed The state request failed for any other reason.
 */
fun controlsForState(
    projection: RevealProjection?,
    stateUnusable: Boolean = false,
    stateLoadFailed: Boolean = false,
): ControlVisibility {
    if (stateUnusable || stateLoadFailed) {
        return ControlVisibility.NONE
    }
    if (projection == null || projection.phase == RevealPhase.IDLE) {
        return ControlVisibility.NONE.copy(startVisible = true)
    }
    val revealing = projection.phase == RevealPhase.REVEALING
    return ControlVisibility(
        startVisible = false,
        startOverVisible = true,
        resetVisible = true,
        stepVisible = revealing,
        backVisible = true,
        jumpVisible = revealing,
        jumpPendingVisible = revealing && projection.nextCell == null,
        // Gated on a focused team, not on the phase: the cue names no team of
        // its own, so the server has nothing to read without a cursor. It
        // survives into `done` on purpose — the champion's photo is the moment
        // the control exists for, and by then the ceremony has stopped stepping.
        mediaVisible = projection.focusedTeamId != null,
    )
}

/**
 * The three fields that make a projection a *different* moment in the ceremony.
 *
 * A direct port of `ceremonySignature` in
 * `animator/static/js/ceremony-media-cue.js`, kept name-for-name for the same
 * reason `controlsForState` is: the projector closes its media overlay when this
 * value changes, and both operator panels reset their Show/Hide label on it. A
 * second definition of "the ceremony moved" would drift, and the symptom is a
 * button describing the opposite of what is on the projector.
 *
 * Counts and focus cover every command — a step moves one or both, a back moves
 * them the other way, and reset/start move the phase. Comparing this rather than
 * the whole projection is what keeps an unchanged reload, or a nudge-driven
 * refresh that returns identical state, from reading as a change and clearing a
 * label while the overlay is still up.
 */
fun ceremonySignature(projection: RevealProjection?): String {
    if (projection == null) {
        return "none"
    }
    return "${projection.phase}|${projection.revealedCount}|${projection.focusedTeamId ?: ""}"
}

/**
 * How the projector readout is worded.
 *
 * A port of `projectorCopy` in `animator/static/js/control-ownership.js`, so
 * the two controllers name the same fact the same way. `null` is the server
 * saying it could not tell, which is deliberately not worded as zero: an
 * outage and an empty hall are different facts, and the operator on stage has
 * no other way to tell them apart.
 */
fun projectorLabel(count: Int?): String = when (count) {
    null -> "Projector count unavailable"
    0 -> "No projectors connected"
    1 -> "1 projector connected"
    else -> "$count projectors connected"
}
