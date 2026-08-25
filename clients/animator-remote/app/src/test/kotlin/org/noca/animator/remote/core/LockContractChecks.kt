//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.core

import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlinx.coroutines.runBlocking

// The safety half of the contract.
//
// What is defended here is not "the buttons work" but that an ambiguous command
// outcome can never advance a ceremony twice in front of an audience. Every check
// below asserts one of: a stated refusal does not lock, an ambiguous outcome does,
// a locked client sends nothing, and only an explicit operator action unlocks it.

// ---------------------------------------------------------------------------
// stated refusals
// ---------------------------------------------------------------------------

internal fun statedRefusalsDoNotLock() = runBlocking {
    for (status in listOf(400, 404, 409, 422)) {
        val transport = FakeTransport()
        transport.responder = { HttpResponse(status, """{"detail":"nope"}""") }
        val client = unlockedClient(transport)

        val refusal = assertIs<CommandOutcome.StatedRefusal>(
            client.step(),
            "status $status must be a stated refusal",
        )
        assertEquals(status, refusal.status)
        assertEquals("nope", refusal.detail)
        assertTrue(!client.isBlocked, "a stated refusal applied nothing, so it must not lock")

        // ...and the next command really is issued.
        transport.responder = { HttpResponse(200, projectionJson()) }
        assertIs<CommandOutcome.Confirmed>(client.step())
    }
}

internal fun mutationConflictLosesLeaseWithoutAmbiguity() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(409, """{"detail":"$CONTROLLER_LEASE_LOST_DETAIL"}""") }
    val client = unlockedClient(transport)

    assertIs<CommandOutcome.StatedRefusal>(client.step())
    assertTrue(!client.isBlocked, "409 is definitive, so it must not set the ambiguity lock")
    assertEquals(ControllerLeaseState.LOST, client.leaseState)
    val before = transport.sent.size
    assertIs<CommandOutcome.Suppressed>(client.step())
    assertEquals(before, transport.sent.size)
}

internal fun forbiddenForgetsCredential() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(403, """{"detail":"Invalid operator credential"}""") }
    val client = unlockedClient(transport)

    assertIs<CommandOutcome.AuthFailure>(client.step())
    assertTrue(!client.hasSecret, "an invalid credential must not sit in memory")
    assertNull(client.projection)
    assertTrue(!client.isBlocked)

    // With no credential, nothing is sent at all.
    val before = transport.sent.size
    assertIs<CommandOutcome.Suppressed>(client.step())
    assertEquals(before, transport.sent.size)
}

internal fun forbiddenOnStateLoadForgetsCredential() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(403, """{"detail":"Invalid operator credential"}""") }
    val client = unlockedClient(transport)
    assertIs<CommandOutcome.AuthFailure>(client.loadState())
    assertTrue(!client.hasSecret)
}

internal fun claimConflictIsReadOnly() = runBlocking {
    val transport = FakeTransport()
    val client = CommandClient(transport, ENDPOINTS, controllerId = "controller-00000001")
    client.setSecret("operator-token")
    transport.responder = { HttpResponse(409, """{"detail":"Another controller is active."}""") }

    assertIs<LeaseOutcome.ReadOnly>(client.claimLease())
    assertEquals(ControllerLeaseState.READ_ONLY, client.leaseState)
    val before = transport.sent.size
    assertIs<CommandOutcome.Suppressed>(client.step())
    assertEquals(before, transport.sent.size)
}

internal fun heartbeatConflictLosesLease() = runBlocking {
    val transport = FakeTransport()
    val client = unlockedClient(transport)
    transport.responder = { HttpResponse(409, """{"detail":"Controller lease lost."}""") }

    assertIs<LeaseOutcome.Lost>(client.heartbeatLease())
    assertEquals(ControllerLeaseState.LOST, client.leaseState)
    assertIs<CommandOutcome.Suppressed>(client.step())
}

internal fun leaseUnavailableFailsClosed() = runBlocking {
    val transport = FakeTransport()
    val client = unlockedClient(transport)
    transport.responder = { HttpResponse(503, """{"detail":"Lease store unavailable."}""") }

    assertIs<LeaseOutcome.Unavailable>(client.heartbeatLease())
    assertEquals(ControllerLeaseState.UNAVAILABLE, client.leaseState)
    assertIs<CommandOutcome.Suppressed>(client.step())
}

internal fun takeoverRestoresAuthority() = runBlocking {
    val transport = FakeTransport()
    val client = CommandClient(transport, ENDPOINTS, controllerId = "controller-00000001")
    client.setSecret("operator-token")
    transport.responder = { HttpResponse(409, """{"detail":"Another controller is active."}""") }
    client.claimLease()
    assertEquals(ControllerLeaseState.READ_ONLY, client.leaseState)

    transport.responder = { request ->
        if (request.url == ENDPOINTS.leaseTakeover) HttpResponse(200, leaseJson())
        else HttpResponse(200, projectionJson())
    }
    assertIs<LeaseOutcome.Active>(client.takeoverLease())
    assertEquals(ENDPOINTS.leaseTakeover, transport.last().url)
    assertEquals(ControllerLeaseState.ACTIVE, client.leaseState)
    assertIs<CommandOutcome.Confirmed>(client.step())
}

internal fun releaseSuppressesLaterMutations() = runBlocking {
    val transport = FakeTransport()
    val client = unlockedClient(transport)
    transport.responder = { HttpResponse(200, leaseJson()) }

    assertIs<LeaseOutcome.Released>(client.releaseLease())
    assertEquals(ControllerLeaseState.UNCLAIMED, client.leaseState)
    val before = transport.sent.size
    assertIs<CommandOutcome.Suppressed>(client.step())
    assertEquals(before, transport.sent.size)
}

internal fun renewalRetriesReachTheServerAfterABlip() = runBlocking {
    // The policy in `heartbeatStep` is only half the rule: the ticker must also
    // still be able to *send* a renewal after a blip already moved ownership to
    // UNAVAILABLE. `heartbeatLease` short-circuits there, so a loop built on it
    // spins forever without ever reaching the server again — the panel keeps
    // reading "in control" while every command is silently suppressed until the
    // lease expires. `renewLease` is the bypass; this pins that it works.
    val transport = FakeTransport()
    val client = unlockedClient(transport)

    transport.responder = { HttpResponse(503, """{"detail":"Lease store unavailable."}""") }
    assertIs<LeaseOutcome.Unavailable>(client.renewLease())
    assertEquals(ControllerLeaseState.UNAVAILABLE, client.leaseState)
    assertTrue(client.leaseRenewable, "a tolerated blip must leave the lease renewable")

    // The blip clears: the very next renewal must be a real round trip.
    transport.responder = { HttpResponse(200, leaseJson()) }
    val before = transport.sent.size
    assertIs<LeaseOutcome.Active>(client.renewLease())
    assertEquals(before + 1, transport.sent.size, "a retry must reach the server, not short-circuit")
    assertEquals(ENDPOINTS.leaseHeartbeat, transport.last().url)
    assertEquals(ControllerLeaseState.ACTIVE, client.leaseState)
    assertIs<CommandOutcome.Confirmed>(
        run {
            transport.responder = { HttpResponse(200, projectionJson()) }
            client.step()
        },
    )

    // A stated ownership answer ends renewal for good: the loop must stop
    // instead of ticking against a scope this panel no longer holds.
    transport.responder = { HttpResponse(409, """{"detail":"Controller lease lost."}""") }
    assertIs<LeaseOutcome.Lost>(client.renewLease())
    assertFalse(client.leaseRenewable, "a stated 409 must end renewal")
    val quiet = transport.sent.size
    assertIs<LeaseOutcome.Suppressed>(client.renewLease())
    assertEquals(quiet, transport.sent.size, "a lost lease must not keep polling")
}

internal fun heartbeatPolicyToleratesBlipsAndSuppression() {
    // A renewal suppressed by an in-flight command must never terminate the
    // ticker: with a 10 s cadence and a 45 s lease, colliding with a command
    // is the common case, not the exotic one.
    assertEquals(HeartbeatStep.RETRY, heartbeatStep(LeaseOutcome.Suppressed, 0))

    // Transport/store blips ride inside the TTL budget: tolerate up to
    // MAX_MISSED_HEARTBEATS misses, then surface the failure.
    assertEquals(HeartbeatStep.RETRY, heartbeatStep(LeaseOutcome.Unavailable("blip"), 0))
    assertEquals(HeartbeatStep.RETRY, heartbeatStep(LeaseOutcome.Unavailable("blip"), 1))
    assertEquals(HeartbeatStep.TERMINATE, heartbeatStep(LeaseOutcome.Unavailable("blip"), MAX_MISSED_HEARTBEATS))

    // A confirmed renewal resets nothing here — the caller owns the counter —
    // but it always continues.
    assertEquals(HeartbeatStep.CONFIRMED, heartbeatStep(LeaseOutcome.Active(ControllerLeaseResponse("renewed", 45, 10)), 3))

    // Stated ownership answers are terminal immediately; retrying cannot help.
    assertEquals(HeartbeatStep.TERMINATE, heartbeatStep(LeaseOutcome.Lost, 0))
    assertEquals(HeartbeatStep.TERMINATE, heartbeatStep(LeaseOutcome.ReadOnly, 0))
    assertEquals(HeartbeatStep.TERMINATE, heartbeatStep(LeaseOutcome.AuthFailure, 0))
}

// ---------------------------------------------------------------------------
// ambiguous outcomes — the core safety property
// ---------------------------------------------------------------------------

internal fun ambiguousOutcomesLock() = runBlocking {
    // The store's 503 is ambiguous even though it often means "nothing applied":
    // it can also arrive AFTER a fenced save committed.
    val cases = listOf(
        503 to """{"detail":"Another operator is mutating this reveal session; retry shortly."}""",
        500 to """{"detail":"boom"}""",
        502 to "",
    )
    for ((status, body) in cases) {
        val transport = FakeTransport()
        transport.responder = { HttpResponse(status, body) }
        val client = unlockedClient(transport)

        val ambiguous = assertIs<CommandOutcome.Ambiguous>(
            client.step(),
            "status $status must be ambiguous",
        )
        assertEquals(status, ambiguous.status)
        assertTrue(client.isBlocked, "status $status must lock the controls")

        // And while locked, NOTHING reaches the network.
        val before = transport.sent.size
        assertIs<CommandOutcome.Suppressed>(client.step())
        assertIs<CommandOutcome.Suppressed>(client.back())
        assertIs<CommandOutcome.Suppressed>(client.reset())
        assertIs<CommandOutcome.Suppressed>(client.jump("t1"))
        assertIs<CommandOutcome.Suppressed>(client.jumpPending())
        assertIs<CommandOutcome.Suppressed>(client.start(null, false))
        assertEquals(before, transport.sent.size, "a locked client must send nothing")
    }
}

internal fun transportFailureLocks() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { throw TransportFailure("connection reset") }
    val client = unlockedClient(transport)

    val ambiguous = assertIs<CommandOutcome.Ambiguous>(client.step())
    assertNull(ambiguous.status, "no response means no status")
    assertEquals("connection reset", ambiguous.cause)
    assertTrue(client.isBlocked)
}

internal fun unreadableSuccessLocks() = runBlocking {
    // A 200 whose body cannot be read means the command WAS applied but its
    // result is unknown — that is ambiguous, not a refusal.
    val transport = FakeTransport()
    transport.responder = { HttpResponse(200, "<html>proxy error</html>") }
    val client = unlockedClient(transport)

    assertIs<CommandOutcome.Ambiguous>(client.step())
    assertTrue(client.isBlocked)
}

internal fun explicitReloadClearsTheLock() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(503, """{"detail":"locked"}""") }
    val client = unlockedClient(transport)
    client.step()
    assertTrue(client.isBlocked)

    transport.responder = { HttpResponse(200, projectionJson()) }
    assertIs<CommandOutcome.Confirmed>(client.reloadState())
    assertTrue(!client.isBlocked, "an explicit reload is the operator's acknowledgement")

    // Commands flow again.
    assertIs<CommandOutcome.Confirmed>(client.step())
}

internal fun failedReloadKeepsTheLock() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(503, """{"detail":"locked"}""") }
    val client = unlockedClient(transport)
    client.step()
    assertTrue(client.isBlocked)

    transport.responder = { throw TransportFailure("still down") }
    assertIs<CommandOutcome.StateLoadFailed>(client.reloadState())
    assertTrue(client.isBlocked, "reconciliation failed, so the lock must hold")
}

internal fun nudgeRefreshNeverClearsTheLock() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(503, """{"detail":"locked"}""") }
    val client = unlockedClient(transport)
    client.step()
    assertTrue(client.isBlocked)

    // An SSE nudge says SOMEONE changed the ceremony. It does not say that this
    // operator's ambiguous command applied, so it must not unlock the panel.
    transport.responder = { HttpResponse(200, projectionJson()) }
    assertIs<CommandOutcome.Confirmed>(client.refreshFromNudge())
    assertTrue(client.isBlocked, "a background nudge must never release the lock")
    assertEquals(34, client.projection?.revealedCount, "but it may refresh the display")

    // Commands are still suppressed.
    val before = transport.sent.size
    assertIs<CommandOutcome.Suppressed>(client.step())
    assertEquals(before, transport.sent.size)
}

internal fun nudgeFailureLeavesStateAlone() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(200, projectionJson()) }
    val client = unlockedClient(transport)
    client.loadState()
    val before = client.projection

    transport.responder = { throw TransportFailure("flaky wifi") }
    client.refreshFromNudge()
    // A failed background poll must not hide the controls or blank the display.
    assertEquals(before, client.projection)
    assertTrue(
        !client.stateLoadFailed,
        "a background refresh failure is not an operator-visible state failure",
    )
    assertTrue(client.visibility.stepVisible)
}

// ---------------------------------------------------------------------------
// same-key retry
// ---------------------------------------------------------------------------

internal fun retryReusesTheOriginalKey() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(503, """{"detail":"locked"}""") }
    val client = unlockedClient(transport)
    client.step()
    val originalKey = assertNotNull(transport.last().headers["Idempotency-Key"])
    assertTrue(client.isBlocked)
    assertTrue(client.canRetryLastAttempt)

    // The server replays the most recent key: same projection, no save, no publish.
    transport.responder = { HttpResponse(200, projectionJson()) }
    assertIs<CommandOutcome.Confirmed>(client.retryLastAttempt())
    assertEquals(originalKey, transport.last().headers["Idempotency-Key"], "a retry must reuse the key")
    assertEquals(ENDPOINTS.step, transport.last().url)
    assertTrue(!client.isBlocked, "a confirmed replay proves the state we now hold")
}

internal fun retryRefusedKeepsTheLock() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(503, """{"detail":"locked"}""") }
    val client = unlockedClient(transport)
    client.step()

    // 409 superseded is authoritative about the ATTEMPT but says nothing about
    // current state, so the operator must still reload.
    transport.responder = {
        HttpResponse(
            409,
            """{"detail":"That command was already applied and the ceremony has moved past it; reload the state."}""",
        )
    }
    assertIs<CommandOutcome.StatedRefusal>(client.retryLastAttempt())
    assertTrue(client.isBlocked, "only a confirmed replay may release the lock")
}

internal fun retryWithoutAnAttemptDoesNothing() = runBlocking {
    val transport = FakeTransport()
    val client = unlockedClient(transport)
    assertIs<CommandOutcome.Suppressed>(client.retryLastAttempt())
    assertEquals(0, transport.sent.size)
}

// ---------------------------------------------------------------------------
// state reads
// ---------------------------------------------------------------------------

internal fun stateNotFoundMeansNoCeremony() = runBlocking {
    val transport = FakeTransport()
    transport.responder = {
        HttpResponse(404, """{"detail":"No reveal session has been started for this scope."}""")
    }
    val client = unlockedClient(transport)

    assertIs<CommandOutcome.NoCeremony>(client.loadState())
    assertNull(client.projection)
    assertTrue(!client.stateLoadFailed, "404 here is an honest answer, not a failure")
    // Only Start is offered.
    assertTrue(client.visibility.startVisible)
    assertTrue(!client.visibility.stepVisible)
}

internal fun unusableStoredStateIsRecoverable() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(500, """{"detail":"$UNUSABLE_STATE_DETAIL"}""") }
    val client = unlockedClient(transport)

    val outcome = assertIs<CommandOutcome.StateUnusable>(client.loadState())
    assertEquals(UNUSABLE_STATE_DETAIL, outcome.detail)
    assertTrue(client.stateUnusable)
    // Nothing is actionable until the state is rebuilt.
    assertEquals(ControlVisibility.NONE, client.visibility)
    assertTrue(!client.isBlocked, "a failed READ cannot have mutated anything")

    // Rebuild = start-reveal with restart=true, which replaces rather than loads.
    transport.responder = { HttpResponse(200, projectionJson()) }
    assertIs<CommandOutcome.Confirmed>(client.start(siteId = null, restart = true))
    assertEquals("""{"site_id":null,"restart":true}""", transport.last().body)
    assertTrue(!client.stateUnusable)
}

internal fun otherStateFailureHidesControlsWithoutLocking() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(502, "") }
    val client = unlockedClient(transport)

    assertIs<CommandOutcome.StateLoadFailed>(client.loadState())
    assertTrue(client.stateLoadFailed)
    assertEquals(ControlVisibility.NONE, client.visibility)
    assertTrue(!client.isBlocked, "a read is never ambiguous: it mutates nothing")
}

// ---------------------------------------------------------------------------
// sequences
// ---------------------------------------------------------------------------

internal fun sequenceStopsAtFirstFailure() = runBlocking {
    val transport = FakeTransport()
    var served = 0
    transport.responder = {
        served += 1
        if (served <= 3) HttpResponse(200, projectionJson()) else HttpResponse(409, """{"detail":"done"}""")
    }
    val client = unlockedClient(transport)

    val outcome = client.stepMany(10)
    assertEquals(10, outcome.requested)
    assertEquals(3, outcome.completed, "the sequence must stop at the first unconfirmed response")
    assertIs<CommandOutcome.StatedRefusal>(outcome.last)
    assertEquals(4, transport.sent.size, "no bulk route: one request per step, and no extra")

    // Each iteration carried its own key.
    val keys = transport.sent.mapNotNull { it.headers["Idempotency-Key"] }
    assertEquals(keys.toSet().size, keys.size)
}

internal fun sequenceStopsOnAmbiguityWithoutConsumingSteps() = runBlocking {
    val transport = FakeTransport()
    var served = 0
    transport.responder = {
        served += 1
        if (served <= 2) HttpResponse(200, projectionJson()) else HttpResponse(503, """{"detail":"locked"}""")
    }
    val client = unlockedClient(transport)

    val outcome = client.backMany(10)
    assertEquals(2, outcome.completed)
    assertIs<CommandOutcome.Ambiguous>(outcome.last)
    assertTrue(client.isBlocked)
    assertEquals(3, transport.sent.size, "an ambiguous result must not consume an extra step")
}

internal fun zeroLengthSequenceSendsNothing() = runBlocking {
    val transport = FakeTransport()
    val client = unlockedClient(transport)
    val outcome = client.stepMany(0)
    assertEquals(0, outcome.completed)
    assertEquals(0, transport.sent.size)
    assertEquals(0, client.stepMany(-5).requested)
}
