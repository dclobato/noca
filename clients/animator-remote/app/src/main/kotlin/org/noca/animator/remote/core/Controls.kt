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
    )
}
