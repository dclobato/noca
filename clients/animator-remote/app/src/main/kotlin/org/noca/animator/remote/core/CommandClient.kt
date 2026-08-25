//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.core

import java.util.UUID
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString

// The reveal operator command client.
//
// This is a port of `animator/static/js/control.js`, kept name-for-name so the
// two clients can be diffed against each other. Three properties dominate it:
//
//   1. **The credential lives in one private field and nowhere else.** It is set
//      from the unlock screen and from then on exists only as an Authorization
//      header. It is never written to a URL, a log, a settings store, or a
//      persisted projection. Persisting it is the platform layer's business
//      (`store/TokenVault.kt`), and even there it never leaves the Keystore
//      unencrypted.
//
//   2. **An unknown outcome must never let the ceremony advance twice.** A
//      refusal the server *stated* (400/403/404/409/422) changed nothing, so the
//      controls re-enable at once. But a transport failure or any 5xx —
//      including the 503 the reveal store raises, which may arrive *after* a
//      fenced save already committed — is ambiguous. There the client locks and
//      refuses to send anything until the operator either reloads authoritative
//      state or retries the very same attempt. Re-enabling optimistically would
//      let an operator press "step" again and silently reveal two teams in front
//      of an audience.
//
//   3. **A locked client sends nothing.** `Suppressed` means no request reached
//      the network, which is what makes the lock a safety property rather than a
//      UI hint.
//
// Threading: like its JavaScript original this class is **not** thread-safe and
// must be called from a single dispatcher (on Android, the main one). JS gets
// that for free from its event loop; here it is a precondition. The `busy` flag
// is single-flight protection against overlapping operator taps, not a lock.

/** Status codes that mean "the server refused and applied nothing". */
val DEFINITIVE_STATUSES: Set<Int> = setOf(400, 403, 404, 409, 422)

/** The server's own `Idempotency-Key` contract; a v4 UUID satisfies it. */
val IDEMPOTENCY_KEY_PATTERN: Regex = Regex("^[A-Za-z0-9_-]{8,128}$")

/** Header carrying this in-memory controller process's identity. */
const val CONTROLLER_ID_HEADER: String = "X-Animator-Controller-Id"

/** The server's controller-id contract; a UUID satisfies it. */
val CONTROLLER_ID_PATTERN: Regex = Regex("^[A-Za-z0-9_-]{8,128}$")

/** The one recoverable corrupt-state detail, answered with `Rebuild state`. */
const val UNUSABLE_STATE_DETAIL: String = "The stored reveal session is unusable."

/** The authored `409` detail that specifically means controller ownership ended. */
const val CONTROLLER_LEASE_LOST_DETAIL: String = "This controller no longer owns the ceremony."

/** Operator-facing copy, mirroring the web panel's wording. */
const val UNKNOWN_OUTCOME: String =
    "Command outcome unknown — controls are locked until the ceremony state is reloaded."

/** Operator-facing copy for the recovery the lock demands. */
const val RELOAD_REQUIRED: String =
    "Command delivery was not confirmed. It may or may not have been applied. " +
        "Restore the connection, then reload state or retry the same command."

/** Operator-facing copy when reconciliation itself fails. */
const val RECONCILE_FAILED: String =
    "The ceremony state could not be reloaded. Reload it before issuing another command."

/** Whether a failure the server described is definitive: nothing was applied. */
fun isDefinitive(status: Int?): Boolean = status != null && status in DEFINITIVE_STATUSES

/** `POST /control/start-reveal` body. */
@Serializable
internal data class StartRevealBody(
    @SerialName("site_id") val siteId: String?,
    val restart: Boolean,
)

/** `POST /control/jump-team` body. */
@Serializable
internal data class JumpTeamBody(
    @SerialName("team_id") val teamId: String,
)

/**
 * One command attempt: the exact bytes sent, plus the key that names it.
 *
 * Retained after dispatch so that an ambiguous outcome can be resolved by
 * re-sending *the same attempt* — the one recovery the server can answer
 * safely, by replaying the original projection instead of applying twice.
 */
internal data class Attempt(
    val method: HttpMethod,
    val url: String,
    val body: String?,
    val key: String?,
)

/**
 * Drives one reveal ceremony through the animator control API.
 *
 * @param transport The HTTP port; see [HttpTransport].
 * @param endpoints The seven control endpoints, from [controlEndpoints].
 * @param newKey Generates one `Idempotency-Key` per attempt. Injectable so a
 *   test can assert key behavior deterministically.
 */
class CommandClient(
    private val transport: HttpTransport,
    private val endpoints: ControlEndpoints,
    controllerId: String = UUID.randomUUID().toString(),
    private val newKey: () -> String = { UUID.randomUUID().toString() },
) {

    private var secret: String? = null
    private var lastAttempt: Attempt? = null
    private val controllerId: String = controllerId.also {
        require(CONTROLLER_ID_PATTERN.matches(it)) { "controller id does not satisfy the server's pattern" }
    }

    /** Current controller ownership. The id itself is intentionally never exposed. */
    var leaseState: ControllerLeaseState = ControllerLeaseState.UNCLAIMED
        private set

    /** Heartbeat cadence stated by the server after a successful lease operation. */
    var heartbeatIntervalSeconds: Int = 10
        private set

    /** Whether a request is in flight; single-flight protection for the UI. */
    var isBusy: Boolean = false
        private set

    /**
     * Whether an ambiguous outcome has locked the client.
     *
     * Cleared only by a confirmed command or an explicit [reloadState]. A
     * background refresh driven by an SSE nudge must never clear it: the nudge
     * says the ceremony changed, not that *this operator's* command applied.
     */
    var isBlocked: Boolean = false
        private set

    /** The last projection the server confirmed, or `null` when there is none. */
    var projection: RevealProjection? = null
        private set

    /** The stored state could not be read; only `Rebuild state` can help. */
    var stateUnusable: Boolean = false
        private set

    /** A state read failed for any other reason. */
    var stateLoadFailed: Boolean = false
        private set

    /** Whether a credential is held. */
    val hasSecret: Boolean get() = secret != null

    /** Whether an ambiguous attempt is available to retry under its own key. */
    val canRetryLastAttempt: Boolean get() = lastAttempt != null && secret != null && !isBusy

    /** Which controls the current state makes meaningful. */
    val visibility: ControlVisibility
        get() = controlsForState(projection, stateUnusable, stateLoadFailed)

    /** Accepts a credential. Does not validate it; the first request does that. */
    fun setSecret(token: String) {
        secret = token
        isBlocked = false
        lastAttempt = null
        leaseState = ControllerLeaseState.UNCLAIMED
    }

    /**
     * Forgets the credential and every piece of ceremony state derived with it.
     *
     * Called on any `403` — from unlock or from a command alike — so that an
     * invalid credential can never sit in memory behind a visible command pad.
     */
    fun forgetSecret() {
        secret = null
        isBlocked = false
        projection = null
        stateUnusable = false
        stateLoadFailed = false
        lastAttempt = null
        leaseState = ControllerLeaseState.UNCLAIMED
    }

    /** Claims an empty lease after the credential has been validated. */
    suspend fun claimLease(): LeaseOutcome = leaseOperation(endpoints.leaseClaim, LeaseAction.CLAIM)

    /** Renews this process's active lease. */
    suspend fun heartbeatLease(): LeaseOutcome =
        if (leaseState == ControllerLeaseState.ACTIVE) {
            leaseOperation(endpoints.leaseHeartbeat, LeaseAction.HEARTBEAT)
        } else {
            LeaseOutcome.Suppressed
        }

    /**
     * The ticker's renewal round trip, callable while a tolerated blip has
     * already moved ownership to [ControllerLeaseState.UNAVAILABLE].
     *
     * [heartbeatLease] deliberately short-circuits when ownership is not
     * `ACTIVE`, which is right for a one-shot caller but fatal for the retry
     * loop: the first blip sets `UNAVAILABLE`, and every later tick would then
     * answer `Suppressed` without ever reaching the server again — spinning
     * forever while the panel still reads "in control" and every command is
     * silently suppressed. The browser panel bypasses its own `active` guard
     * for exactly this reason (`sendHeartbeat` in `control-lease.js`); this is
     * that bypass. Ownership answers the server actually stated
     * (`LOST`/`READ_ONLY`) stay terminal.
     */
    suspend fun renewLease(): LeaseOutcome =
        if (leaseRenewable) {
            leaseOperation(endpoints.leaseHeartbeat, LeaseAction.HEARTBEAT)
        } else {
            LeaseOutcome.Suppressed
        }

    /**
     * Whether the ticker still has something to renew: ownership is held, or
     * held-but-unverified after a blip. A stated `LOST`/`READ_ONLY`, or a
     * released `UNCLAIMED`, means renewal is over — the loop must stop rather
     * than tick against a scope it no longer holds.
     */
    val leaseRenewable: Boolean
        get() = leaseState == ControllerLeaseState.ACTIVE || leaseState == ControllerLeaseState.UNAVAILABLE

    /** Best-effort owner release; TTL expiry remains authoritative. */
    suspend fun releaseLease(): LeaseOutcome =
        if (leaseState == ControllerLeaseState.ACTIVE) {
            leaseOperation(endpoints.leaseRelease, LeaseAction.RELEASE)
        } else {
            LeaseOutcome.Suppressed
        }

    /** Explicitly fences the former controller and acquires command authority. */
    suspend fun takeoverLease(): LeaseOutcome =
        leaseOperation(endpoints.leaseTakeover, LeaseAction.TAKEOVER)

    /** Loads authoritative state; a `404` means "no ceremony yet", not an error. */
    suspend fun loadState(): CommandOutcome = guarded {
        readState(clearBlockedOnSuccess = false)
    }

    /**
     * Reloads authoritative state and, on success, releases the lock.
     *
     * This is the operator's explicit acknowledgement of an unknown outcome, so
     * a failure here keeps the lock rather than risking a double step.
     */
    suspend fun reloadState(): CommandOutcome = guarded {
        val outcome = readState(clearBlockedOnSuccess = true)
        val authoritative = outcome is CommandOutcome.Confirmed ||
            outcome is CommandOutcome.NoCeremony ||
            outcome is CommandOutcome.AuthFailure
        if (!authoritative) {
            isBlocked = true
        }
        outcome
    }

    /**
     * Refreshes the displayed projection after an SSE nudge.
     *
     * Display only: it may not clear [isBlocked], and a failure leaves every
     * flag as it was. A nudge is a hint that *someone* changed the ceremony —
     * treating it as proof that this operator's ambiguous command applied would
     * defeat the lock entirely, and letting a failed background poll hide the
     * controls would make the remote flicker on a weak connection.
     */
    suspend fun refreshFromNudge(): CommandOutcome = guarded {
        val blockedBefore = isBlocked
        val projectionBefore = projection
        val unusableBefore = stateUnusable
        val failedBefore = stateLoadFailed

        val outcome = readState(clearBlockedOnSuccess = false)
        isBlocked = blockedBefore
        if (outcome !is CommandOutcome.Confirmed && outcome !is CommandOutcome.NoCeremony) {
            projection = projectionBefore
            stateUnusable = unusableBefore
            stateLoadFailed = failedBefore
        }
        outcome
    }

    /** `POST /control/start-reveal`. [siteId] must equal the token's own scope. */
    suspend fun start(siteId: String?, restart: Boolean): CommandOutcome {
        val body = AnimatorJson.encodeToString(
            StartRevealBody(siteId = siteId?.trim()?.ifEmpty { null }, restart = restart),
        )
        return runCommand(Attempt(HttpMethod.POST, endpoints.start, body, nextKey()))
    }

    /** `POST /control/step`; sends no body, as the server's model forbids extras. */
    suspend fun step(): CommandOutcome =
        runCommand(Attempt(HttpMethod.POST, endpoints.step, null, nextKey()))

    /** `POST /control/back`; sends no body. */
    suspend fun back(): CommandOutcome =
        runCommand(Attempt(HttpMethod.POST, endpoints.back, null, nextKey()))

    /** `POST /control/reset`; sends no body. */
    suspend fun reset(): CommandOutcome =
        runCommand(Attempt(HttpMethod.POST, endpoints.reset, null, nextKey()))

    /** `POST /control/jump-team`. */
    suspend fun jump(teamId: String): CommandOutcome {
        val body = AnimatorJson.encodeToString(JumpTeamBody(teamId = teamId))
        return runCommand(Attempt(HttpMethod.POST, endpoints.jump, body, nextKey()))
    }

    /** `POST /control/jump-pending`; sends no body. */
    suspend fun jumpPending(): CommandOutcome =
        runCommand(Attempt(HttpMethod.POST, endpoints.jumpPending, null, nextKey()))

    /**
     * Sends [count] separate `step` commands, stopping at the first that is not
     * confirmed.
     *
     * There is no bulk route: each iteration is its own command with its own
     * key, so an ambiguous result mid-sequence can never consume an extra step.
     */
    suspend fun stepMany(count: Int): SequenceOutcome = repeatCommand(count) { step() }

    /** Sends [count] separate `back` commands, stopping at the first failure. */
    suspend fun backMany(count: Int): SequenceOutcome = repeatCommand(count) { back() }

    /**
     * Re-sends the last attempt under its original `Idempotency-Key`.
     *
     * This is the safe half of ambiguity recovery and the reason the key is kept:
     * the server resolves the key inside the scope lock, and the *most recent*
     * key is **replayed** — the stored state is re-projected and returned with a
     * plain `200`, with no save and no publish, because that state already is
     * the command's result.
     *
     * Deliberately permitted while locked, and deliberately conservative
     * afterwards: only a confirmed replay proves the projection we now hold, so
     * any other outcome keeps the lock. In particular a `409` means the ceremony
     * has moved past this attempt, which is authoritative about the *attempt* but
     * says nothing about current state.
     */
    suspend fun retryLastAttempt(): CommandOutcome {
        val attempt = lastAttempt ?: return CommandOutcome.Suppressed
        if (isBusy || secret == null) {
            return CommandOutcome.Suppressed
        }
        isBusy = true
        try {
            val outcome = dispatch(attempt)
            if (outcome !is CommandOutcome.Confirmed && outcome !is CommandOutcome.AuthFailure) {
                isBlocked = true
            }
            return outcome
        } finally {
            isBusy = false
        }
    }

    // ------------------------------------------------------------------
    // internals
    // ------------------------------------------------------------------

    /** Runs [block] under single-flight protection, requiring a credential. */
    private suspend fun guarded(block: suspend () -> CommandOutcome): CommandOutcome {
        if (isBusy || secret == null) {
            return CommandOutcome.Suppressed
        }
        isBusy = true
        try {
            return block()
        } finally {
            isBusy = false
        }
    }

    /** Refuses to send while locked, then dispatches one mutating command. */
    private suspend fun runCommand(attempt: Attempt): CommandOutcome {
        if (isBusy || isBlocked || secret == null || leaseState != ControllerLeaseState.ACTIVE) {
            return CommandOutcome.Suppressed
        }
        isBusy = true
        try {
            return dispatch(attempt)
        } finally {
            isBusy = false
        }
    }

    /** Sends one attempt and classifies its outcome. */
    private suspend fun dispatch(attempt: Attempt): CommandOutcome {
        val token = secret ?: return CommandOutcome.Suppressed
        lastAttempt = attempt

        val response = try {
            transport.send(attempt.toRequest(token))
        } catch (failure: TransportFailure) {
            isBlocked = true
            return CommandOutcome.Ambiguous(null, failure.message ?: RELOAD_REQUIRED)
        }

        if (response.status in 200..299) {
            val parsed = parseProjection(response.body)
            if (parsed == null) {
                // The command was applied — the server said 200 — but its result
                // is unreadable, so the ceremony state we hold is unknown.
                isBlocked = true
                return CommandOutcome.Ambiguous(response.status, UNKNOWN_OUTCOME)
            }
            projection = parsed
            isBlocked = false
            stateUnusable = false
            stateLoadFailed = false
            return CommandOutcome.Confirmed(parsed)
        }

        if (response.status == 403) {
            forgetSecret()
            return CommandOutcome.AuthFailure
        }

        val detail = parseErrorDetail(response.body)
        if (isDefinitive(response.status)) {
            isBlocked = false
            if (response.status == 409 && detail == CONTROLLER_LEASE_LOST_DETAIL) {
                leaseState = ControllerLeaseState.LOST
                lastAttempt = null
            }
            return CommandOutcome.StatedRefusal(response.status, detail)
        }

        isBlocked = true
        return CommandOutcome.Ambiguous(response.status, detail ?: UNKNOWN_OUTCOME)
    }

    /** Reads `GET /control/state` and maps it onto the client's flags. */
    private suspend fun readState(clearBlockedOnSuccess: Boolean): CommandOutcome {
        val token = secret ?: return CommandOutcome.Suppressed
        val request = HttpRequest(
            method = HttpMethod.GET,
            url = endpoints.state,
            headers = buildHeaders(token, withBody = false, idempotencyKey = null),
        )

        val response = try {
            transport.send(request)
        } catch (failure: TransportFailure) {
            stateUnusable = false
            stateLoadFailed = true
            return CommandOutcome.StateLoadFailed(null, failure.message ?: RECONCILE_FAILED)
        }

        if (response.status in 200..299) {
            val parsed = parseProjection(response.body)
                ?: run {
                    stateUnusable = false
                    stateLoadFailed = true
                    return CommandOutcome.StateLoadFailed(response.status, "the state response could not be read")
                }
            projection = parsed
            stateUnusable = false
            stateLoadFailed = false
            if (clearBlockedOnSuccess) {
                isBlocked = false
                lastAttempt = null
            }
            return CommandOutcome.Confirmed(parsed)
        }

        if (response.status == 403) {
            forgetSecret()
            return CommandOutcome.AuthFailure
        }

        val detail = parseErrorDetail(response.body)

        if (response.status == 404) {
            // Honest answer, not a failure: this credential has no ceremony yet.
            projection = null
            stateUnusable = false
            stateLoadFailed = false
            if (clearBlockedOnSuccess) {
                isBlocked = false
                lastAttempt = null
            }
            return CommandOutcome.NoCeremony
        }

        if (response.status == 500 && detail == UNUSABLE_STATE_DETAIL) {
            projection = null
            stateUnusable = true
            stateLoadFailed = false
            return CommandOutcome.StateUnusable(detail)
        }

        stateUnusable = false
        stateLoadFailed = true
        return CommandOutcome.StateLoadFailed(response.status, detail ?: RECONCILE_FAILED)
    }

    /** Runs one command repeatedly, stopping at the first unconfirmed outcome. */
    private suspend fun repeatCommand(
        count: Int,
        command: suspend () -> CommandOutcome,
    ): SequenceOutcome {
        val requested = count.coerceAtLeast(0)
        var completed = 0
        var last: CommandOutcome = CommandOutcome.Suppressed
        while (completed < requested) {
            val outcome = command()
            last = outcome
            if (outcome !is CommandOutcome.Confirmed) {
                break
            }
            completed += 1
        }
        return SequenceOutcome(requested = requested, completed = completed, last = last)
    }

    /**
     * Produces one key per attempt.
     *
     * A fresh key each time, because two deliberate presses of "step" are two
     * commands and must both apply; only a retry of one attempt reuses a key.
     * The generated value is checked against the server's own pattern so a bad
     * injected generator fails here rather than as a puzzling 422 mid-ceremony.
     */
    private fun nextKey(): String {
        val key = newKey()
        require(IDEMPOTENCY_KEY_PATTERN.matches(key)) {
            "generated idempotency key does not satisfy the server's pattern: $key"
        }
        return key
    }

    private fun Attempt.toRequest(token: String): HttpRequest = HttpRequest(
        method = method,
        url = url,
        headers = buildHeaders(
            token,
            withBody = body != null,
            idempotencyKey = key,
            withControllerId = true,
        ),
        body = body,
    )

    private fun buildHeaders(
        token: String,
        withBody: Boolean,
        idempotencyKey: String?,
        withControllerId: Boolean = false,
    ): Map<String, String> = buildMap {
        put("Accept", "application/json")
        put("Authorization", "Bearer $token")
        if (withBody) {
            put("Content-Type", "application/json")
        }
        if (idempotencyKey != null) {
            put("Idempotency-Key", idempotencyKey)
        }
        if (withControllerId) {
            put(CONTROLLER_ID_HEADER, controllerId)
        }
    }

    private enum class LeaseAction { CLAIM, HEARTBEAT, RELEASE, TAKEOVER }

    /** Sends one authenticated lease request without exposing the controller id. */
    private suspend fun leaseOperation(url: String, action: LeaseAction): LeaseOutcome {
        if (isBusy || secret == null) {
            return LeaseOutcome.Suppressed
        }
        isBusy = true
        try {
            val token = secret ?: return LeaseOutcome.Suppressed
            val response = try {
                transport.send(
                    HttpRequest(
                        method = HttpMethod.POST,
                        url = url,
                        headers = buildHeaders(
                            token,
                            withBody = false,
                            idempotencyKey = null,
                            withControllerId = true,
                        ),
                    ),
                )
            } catch (failure: TransportFailure) {
                leaseState = ControllerLeaseState.UNAVAILABLE
                return LeaseOutcome.Unavailable(failure.message ?: "Controller lease is unavailable.")
            }

            if (response.status in 200..299) {
                if (action == LeaseAction.RELEASE) {
                    leaseState = ControllerLeaseState.UNCLAIMED
                    return LeaseOutcome.Released
                }
                val lease = parseControllerLeaseResponse(response.body)
                if (lease == null) {
                    leaseState = ControllerLeaseState.UNAVAILABLE
                    return LeaseOutcome.Unavailable("The controller lease response could not be read.")
                }
                heartbeatIntervalSeconds = lease.heartbeatIntervalSeconds
                leaseState = ControllerLeaseState.ACTIVE
                return LeaseOutcome.Active(lease)
            }
            if (response.status == 403) {
                forgetSecret()
                return LeaseOutcome.AuthFailure
            }
            if (response.status == 409) {
                leaseState = if (action == LeaseAction.CLAIM) {
                    ControllerLeaseState.READ_ONLY
                } else {
                    ControllerLeaseState.LOST
                }
                return if (action == LeaseAction.CLAIM) LeaseOutcome.ReadOnly else LeaseOutcome.Lost
            }
            leaseState = ControllerLeaseState.UNAVAILABLE
            return LeaseOutcome.Unavailable(
                parseErrorDetail(response.body) ?: "Controller lease is unavailable.",
            )
        } finally {
            isBusy = false
        }
    }
}
