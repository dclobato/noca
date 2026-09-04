//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Wire models mirroring the animator's response schemas.
 *
 * The animator serves no OpenAPI document (`openapi_url=None`), so these are
 * hand-written against `animator/models/responses.py` and
 * `animator/models/reveal_session.py` and pinned by
 * `tests/animator/test_remote_client_contract.py`, which fails the Python suite
 * if the server models and this file drift apart.
 *
 * Every model is decoded with [AnimatorJson], which ignores unknown keys: a
 * field added to a server response must never crash an operator's remote in the
 * middle of a ceremony.
 */

/** The decoder for every animator payload. */
val AnimatorJson: Json = Json {
    ignoreUnknownKeys = true
    explicitNulls = true
}

/**
 * Ceremony phase.
 *
 * Kept as a [String] rather than an enum on purpose: an unrecognized *value*
 * would abort decoding of an otherwise valid projection, and the remote should
 * degrade to "no controls" instead of failing to parse the ceremony it is
 * supposed to be driving.
 */
object RevealPhase {
    const val IDLE = "idle"
    const val REVEALING = "revealing"
    const val DONE = "done"
}

/** Maximum ranking positions awarded each medal, ascending. */
@Serializable
data class MedalCutoffs(
    val gold: Int,
    val silver: Int,
    val bronze: Int,
)

/** The single cell the next `step` will change — position only, never a verdict. */
@Serializable
data class NextRevealCell(
    @SerialName("team_id") val teamId: String,
    @SerialName("problem_id") val problemId: String,
    val label: String,
)

/** One team's view of one problem. */
@Serializable
data class ProblemRevealView(
    val label: String,
    @SerialName("problem_id") val problemId: String,
    val solved: Boolean = false,
    val attempts: Int = 0,
    @SerialName("solved_at_minutes") val solvedAtMinutes: Int? = null,
    val penalty: Int = 0,
    @SerialName("pending_frozen_count") val pendingFrozenCount: Int = 0,
    @SerialName("is_first_solver") val isFirstSolver: Boolean = false,
    @SerialName("pending_frozen") val pendingFrozen: Boolean = false,
)

/**
 * One team's row.
 *
 * @property penalty Total ICPC time — solve minutes plus attempt penalties.
 */
@Serializable
data class TeamRevealView(
    @SerialName("team_id") val teamId: String,
    @SerialName("team_name") val teamName: String,
    @SerialName("team_fullname") val teamFullname: String,
    @SerialName("site_name") val siteName: String? = null,
    @SerialName("current_rank") val currentRank: Int,
    val solved: Int = 0,
    val penalty: Int = 0,
    val medal: String? = null,
    val problems: Map<String, ProblemRevealView> = emptyMap(),
)

/**
 * The safe projection of one ceremony, returned by every control command.
 *
 * Note what is absent: `frozen_submission_ids`, `step_log`, and
 * `command_receipts` never leave the server, so a client cannot infer the
 * unrevealed remainder. Only counts, phase, focus, and derived team views.
 */
@Serializable
data class RevealProjection(
    @SerialName("contest_id") val contestId: String,
    val scope: String,
    @SerialName("site_id") val siteId: String? = null,
    @SerialName("site_name") val siteName: String? = null,
    val phase: String,
    @SerialName("focused_team_id") val focusedTeamId: String? = null,
    @SerialName("revealed_count") val revealedCount: Int = 0,
    @SerialName("frozen_count") val frozenCount: Int = 0,
    @SerialName("medal_cutoffs") val medalCutoffs: MedalCutoffs? = null,
    val teams: List<TeamRevealView> = emptyList(),
    @SerialName("next_cell") val nextCell: NextRevealCell? = null,
)

/** One problem column in the public `/meta` feed. */
@Serializable
data class ProblemMeta(
    @SerialName("problem_id") val problemId: String,
    val ordinal: Int,
    val label: String,
    @SerialName("balloon_color") val balloonColor: String = "",
)

/**
 * One contest site in the public `/meta` feed.
 *
 * [siteId] is exactly what a `start-reveal` body's `site_id` may carry and what
 * a `?scope=` query value accepts.
 */
@Serializable
data class SiteMeta(
    @SerialName("site_id") val siteId: String,
    val name: String,
    @SerialName("gold_cutoff") val goldCutoff: Int = 0,
    @SerialName("silver_cutoff") val silverCutoff: Int = 0,
    @SerialName("bronze_cutoff") val bronzeCutoff: Int = 0,
    @SerialName("team_count") val teamCount: Int = 0,
)

/** The public contest feed the scope picker is built from. */
@Serializable
data class ContestMeta(
    @SerialName("contest_id") val contestId: String,
    val slug: String,
    val name: String,
    @SerialName("start_time") val startTime: String = "",
    @SerialName("end_time") val endTime: String = "",
    @SerialName("freeze_at") val freezeAt: String = "",
    @SerialName("is_frozen") val isFrozen: Boolean = false,
    // Whether the contest has begun. Until it has, the feed publishes an empty
    // `problems` list on purpose -- the problem count and balloon colors are
    // contest secrets before the start -- so a remote client must not read
    // "no problems" as "this contest has none".
    //
    // The default deliberately diverges from the server, which always states the
    // field and treats absence as nothing at all. Here absence means the payload
    // came from a server predating the field, and such a server had no pre-start
    // gate to report: it published problems at all times. Decoding as `true` is
    // therefore what that payload actually meant. The consequence to know is that
    // a pre-start payload from an old server decodes as started -- which is
    // exactly the leak this field exists to close, and it is closed by upgrading
    // the server, not the client. A client-side default of `false` would not fix
    // it and would blank every live board served by an older deployment.
    @SerialName("has_started") val hasStarted: Boolean = true,
    val problems: List<ProblemMeta> = emptyList(),
    val sites: List<SiteMeta> = emptyList(),
)

/** Response shared by the four controller-lease operations. */
@Serializable
data class ControllerLeaseResponse(
    val status: String,
    @SerialName("lease_ttl_seconds") val leaseTtlSeconds: Int,
    @SerialName("heartbeat_interval_seconds") val heartbeatIntervalSeconds: Int,
    /** Open projector streams in this scope; `null` when the server could not tell. */
    @SerialName("projector_count") val projectorCount: Int? = null,
    @SerialName("server_time") val serverTime: String? = null,
)

/**
 * How a team is named on screen.
 *
 * Mirrors `teamLabel` in `animator/static/js/cell-format.js`: the feeds carry
 * both `team_fullname` (the real name) and `team_name` (the login). An audience
 * reads the name — a login on a projector means nothing to them — so the full
 * name wins whenever there is one and the login is only a fallback. The operator
 * and the projector must never name the same team differently.
 */
fun teamLabel(team: TeamRevealView): String {
    val full = team.teamFullname.trim()
    return if (full.isNotEmpty()) full else team.teamName.trim()
}

/**
 * Extracts a human-readable message from a FastAPI error body.
 *
 * FastAPI's `detail` is a **string** for a raised `HTTPException` but a **list**
 * of validation errors for a 422. Both shapes reach this client, so neither may
 * be assumed; anything unparseable yields `null` and the caller falls back to a
 * generic message rather than showing the operator raw JSON.
 */
fun parseErrorDetail(body: String): String? = runCatching {
    if (body.isBlank()) {
        return@runCatching null
    }
    val detail = (AnimatorJson.parseToJsonElement(body) as? JsonObject)?.get("detail")
    when (detail) {
        // A raised HTTPException: `detail` is a plain string. `JsonNull` is also a
        // JsonPrimitive whose `content` is the literal "null", which must not be
        // shown to an operator as if it were a message.
        is JsonPrimitive -> if (detail is JsonNull) null else detail.content.ifBlank { null }

        // A 422: `detail` is a list of validation errors. Every access here is
        // defensive because this is an *error* path — the body may be truncated by
        // a proxy or shaped by a future FastAPI — and a crash while rendering a
        // failure message would be worse than the failure it describes.
        is JsonArray -> detail
            .mapNotNull { entry -> (entry as? JsonObject)?.get("msg") as? JsonPrimitive }
            .filter { it !is JsonNull }
            .joinToString("; ") { it.content }
            .ifBlank { null }

        else -> null
    }
}.getOrNull()

/** Decodes a projection, or `null` when the payload is not one. */
fun parseProjection(body: String): RevealProjection? =
    runCatching { AnimatorJson.decodeFromString<RevealProjection>(body) }.getOrNull()

/** Decodes the contest meta feed, or `null` when the payload is not one. */
fun parseContestMeta(body: String): ContestMeta? =
    runCatching { AnimatorJson.decodeFromString<ContestMeta>(body) }.getOrNull()

/** Decodes a controller-lease response, or `null` when the payload is not one. */
fun parseControllerLeaseResponse(body: String): ControllerLeaseResponse? =
    runCatching { AnimatorJson.decodeFromString<ControllerLeaseResponse>(body) }.getOrNull()
