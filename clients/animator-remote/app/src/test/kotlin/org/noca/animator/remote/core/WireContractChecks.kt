//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.core

import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject

// What the client puts on the wire and reads back off it: models, URLs, request
// shapes, and control visibility. The fixtures are generated from the animator's
// own Pydantic models and pinned by tests/animator/test_remote_client_contract.py.

// ---------------------------------------------------------------------------
// wire models
// ---------------------------------------------------------------------------

internal fun projectionFixtureParses() {
    val projection = assertNotNull(parseProjection(projectionJson()), "fixture must parse")
    assertEquals("revealing", projection.phase)
    assertEquals(34, projection.revealedCount)
    assertEquals(51, projection.frozenCount)
    assertEquals("Site 1", projection.siteName)
    assertEquals(2, projection.teams.size)
    assertEquals(4, projection.medalCutoffs?.gold)
    assertEquals("B", projection.nextCell?.label)

    val first = projection.teams.first()
    assertEquals(1, first.currentRank)
    assertEquals("gold", first.medal)
    // pending_frozen is a Pydantic computed field and IS serialized; the cell
    // holding an unrevealed run must arrive as pending on the remote too.
    assertEquals(true, first.problems["B"]?.pendingFrozen)
    assertEquals(1, first.problems["B"]?.pendingFrozenCount)
    assertEquals(true, first.problems["A"]?.isFirstSolver)
    assertNull(first.problems["B"]?.solvedAtMinutes)
}

internal fun metaFixtureParses() {
    val meta = assertNotNull(parseContestMeta(fixture("meta.json")), "meta fixture must parse")
    assertEquals("maratona-2026", meta.slug)
    assertEquals(2, meta.problems.size)
    assertEquals(1, meta.sites.size)
    assertEquals("Site 1", meta.sites.first().name)
    // The balloon colour arrives WITHOUT a leading '#'.
    assertEquals("ff0000", meta.problems.first().balloonColor)
    assertEquals(true, meta.hasStarted)
}

internal fun controllerLeaseFixtureParses() {
    val lease = assertNotNull(parseControllerLeaseResponse(leaseJson()), "lease fixture must parse")
    assertEquals("claimed", lease.status)
    assertEquals(45, lease.leaseTtlSeconds)
    assertEquals(10, lease.heartbeatIntervalSeconds)
    assertEquals(3, lease.projectorCount)
    assertNull(lease.serverTime)
    // A lease answered by a server predating the readout still parses, and an
    // explicit null (Valkey could not tell) stays distinct from zero.
    assertNull(
        assertNotNull(
            parseControllerLeaseResponse(
                """{"status":"renewed","lease_ttl_seconds":45,"heartbeat_interval_seconds":10}""",
            ),
        ).projectorCount,
    )
    assertNull(
        assertNotNull(
            parseControllerLeaseResponse(
                """{"status":"renewed","lease_ttl_seconds":45,"heartbeat_interval_seconds":10,"projector_count":null}""",
            ),
        ).projectorCount,
    )
}

internal fun metaWithoutHasStartedDecodesAsStarted() {
    // A server predating `has_started` had no pre-start gate: it published the
    // problem set at all times, so a payload of its is a started contest as far
    // as this client can tell. Flipping this default to false would blank every
    // live board served by an older deployment, which is why the contract is
    // pinned here and not left to the model's comment alone.
    //
    // The consequence to know is the other direction: a *pre-start* payload from
    // such a server also decodes as started. That is the leak `has_started`
    // exists to close, and it is closed by upgrading the server -- no client
    // default can close it, because the payload carries nothing to close it with.
    val parsed = AnimatorJson.parseToJsonElement(fixture("meta.json")) as JsonObject
    val withoutField = JsonObject(parsed - "has_started").toString()
    val meta = assertNotNull(parseContestMeta(withoutField), "a payload without has_started must still parse")
    assertEquals(true, meta.hasStarted)
    assertEquals(2, meta.problems.size)
}

internal fun unknownFieldsAreIgnored() {
    // A field added to a future server response must not crash a remote in the
    // middle of a ceremony.
    val payload = """
        {"contest_id":"c1","scope":"global","site_id":null,"site_name":null,
         "phase":"revealing","focused_team_id":null,"revealed_count":1,"frozen_count":2,
         "medal_cutoffs":null,"teams":[],"a_field_from_the_future":{"nested":true}}
    """.trimIndent()
    assertEquals(1, assertNotNull(parseProjection(payload)).revealedCount)
}

internal fun teamLabelPrefersFullname() {
    val named = TeamRevealView(
        teamId = "t1", teamName = "team01", teamFullname = "Os Bugados", currentRank = 1,
    )
    val anonymous = TeamRevealView(
        teamId = "t2", teamName = "team02", teamFullname = "   ", currentRank = 2,
    )
    // The audience reads the real name; the login is only a fallback.
    assertEquals("Os Bugados", teamLabel(named))
    assertEquals("team02", teamLabel(anonymous))
}

internal fun errorDetailParsesBothShapes() {
    assertEquals(
        "A reveal session is already active; reset it or restart explicitly.",
        parseErrorDetail("""{"detail":"A reveal session is already active; reset it or restart explicitly."}"""),
    )
    // FastAPI's 422 detail is a LIST, not a string.
    assertEquals(
        "Field required; Input should be a valid string",
        parseErrorDetail(
            """{"detail":[{"loc":["body","team_id"],"msg":"Field required"},
               {"loc":["body","team_id"],"msg":"Input should be a valid string"}]}""",
        ),
    )
    assertNull(parseErrorDetail(""))
    assertNull(parseErrorDetail("not json at all"))
}

internal fun errorDetailSurvivesHostileBodies() {
    // This runs on an *error* path: the body may be truncated by a proxy or
    // reshaped by a future FastAPI. Crashing while rendering a failure message
    // would be worse than the failure it describes, so every shape must yield a
    // string or null — never an exception.
    val hostile = listOf(
        """{"detail":null}""",
        """{"detail":{}}""",
        """{"detail":[]}""",
        """{"detail":[{"msg":null}]}""",
        """{"detail":[{"msg":{"nested":"object"}}]}""",
        """{"detail":[{"loc":["body"]}]}""",
        """{"detail":["a bare string in the list"]}""",
        """{"detail":42}""",
        """{"nothing":"here"}""",
        "[]",
        "null",
        """{"detail":"""",
        "{",
    )
    for (body in hostile) {
        // The assertion is simply that this returns rather than throws.
        parseErrorDetail(body)
    }

    // A well-formed list still reads, and a bare-string list yields nothing
    // rather than a misleading fragment.
    assertEquals("only this", parseErrorDetail("""{"detail":[{"msg":"only this"}]}"""))
    assertNull(parseErrorDetail("""{"detail":["a bare string in the list"]}"""))
    assertNull(parseErrorDetail("""{"detail":null}"""))
}

// ---------------------------------------------------------------------------
// URLs
// ---------------------------------------------------------------------------

internal fun urlsAreTrailingSlashSafe() {
    val withSlash = controlEndpoints("https://animator.example.com/", "maratona-2026")
    val without = controlEndpoints("https://animator.example.com", "maratona-2026")
    assertEquals(without, withSlash)
    assertEquals("https://animator.example.com/c/maratona-2026/control/step", without.step)
    assertEquals("https://animator.example.com/c/maratona-2026/control/start-reveal", without.start)
    assertEquals("https://animator.example.com/c/maratona-2026/control/jump-team", without.jump)
    assertEquals(
        "https://animator.example.com/c/maratona-2026/control/jump-pending",
        without.jumpPending,
    )
    assertEquals(
        "https://animator.example.com/c/maratona-2026/control/controller-lease/claim",
        without.leaseClaim,
    )
    assertEquals(
        "https://animator.example.com/c/maratona-2026/control/controller-lease/heartbeat",
        without.leaseHeartbeat,
    )
    assertEquals(
        "https://animator.example.com/c/maratona-2026/meta",
        metaUrl("https://animator.example.com//", "maratona-2026"),
    )
    assertEquals(
        "https://animator.example.com/c/maratona-2026/reveal/events?scope=global",
        revealEventsUrl("https://animator.example.com", "maratona-2026", GLOBAL_SCOPE),
    )
}

internal fun unsafePathSegmentsAreRejected() {
    // A slug is interpolated into a path, so a separator or dot-segment could
    // redirect the request to a different endpoint entirely.
    for (bad in listOf("../admin", "a/b", "a?b", "a#b", "", "   ", "..")) {
        assertFailsWith<IllegalArgumentException>("expected rejection of '$bad'") {
            controlEndpoints("https://animator.example.com", bad)
        }
    }
    assertFailsWith<IllegalArgumentException> {
        revealEventsUrl("https://animator.example.com", "maratona-2026", "not/a/scope")
    }
}

// ---------------------------------------------------------------------------
// control visibility
// ---------------------------------------------------------------------------

internal fun controlVisibilityTruthTable() {
    assertEquals(ControlVisibility.NONE, controlsForState(null, stateUnusable = true))
    assertEquals(ControlVisibility.NONE, controlsForState(null, stateLoadFailed = true))
    assertEquals(
        ControlVisibility.NONE,
        controlsForState(projectionInPhase(RevealPhase.REVEALING), stateUnusable = true),
    )

    val noCeremony = controlsForState(null)
    assertTrue(noCeremony.startVisible)
    assertTrue(!noCeremony.stepVisible && !noCeremony.backVisible && !noCeremony.jumpVisible)

    assertEquals(noCeremony, controlsForState(projectionInPhase(RevealPhase.IDLE)))

    val revealing = controlsForState(projectionInPhase(RevealPhase.REVEALING))
    assertTrue(!revealing.startVisible)
    assertTrue(revealing.startOverVisible && revealing.resetVisible)
    assertTrue(revealing.stepVisible && revealing.backVisible && revealing.jumpVisible)
    assertTrue(revealing.jumpPendingVisible)
    assertTrue(
        !controlsForState(
            projectionInPhase(RevealPhase.REVEALING).copy(
                nextCell = NextRevealCell("team-1", "problem-1", "A"),
            ),
        ).jumpPendingVisible,
    )
    // Back is NOT gated on revealed_count: a step can be a pure cursor move, so
    // "0 revealed" does not mean "nothing to undo".
    assertTrue(
        controlsForState(projectionInPhase(RevealPhase.REVEALING).copy(revealedCount = 0)).backVisible,
    )

    val done = controlsForState(projectionInPhase(RevealPhase.DONE))
    assertTrue(done.startOverVisible && done.resetVisible && done.backVisible)
    assertTrue(!done.stepVisible && !done.jumpVisible && !done.jumpPendingVisible)

    // Show media is gated on a focused team rather than on the phase: the cue
    // names no team of its own, so the server has nothing to read without a
    // cursor. It survives into `done` deliberately — the champion's photo is
    // the moment the control exists for. Must match `controlsForState` in
    // animator/static/js/control.js exactly.
    assertTrue(!revealing.mediaVisible)
    assertTrue(!done.mediaVisible)
    assertTrue(
        controlsForState(projectionInPhase(RevealPhase.REVEALING).copy(focusedTeamId = "team-1")).mediaVisible,
    )
    assertTrue(
        controlsForState(projectionInPhase(RevealPhase.DONE).copy(focusedTeamId = "team-1")).mediaVisible,
    )
    assertTrue(
        !controlsForState(projectionInPhase(RevealPhase.IDLE).copy(focusedTeamId = "team-1")).mediaVisible,
    )
}

/**
 * "Did the ceremony move" must match `ceremonySignature` in
 * `animator/static/js/ceremony-media-cue.js` exactly.
 *
 * The projector closes its media overlay when this value changes, and both
 * operator panels reset their Show/Hide label on the same signal. A second
 * definition would drift, and the symptom is a button describing the opposite of
 * what is on the projector.
 */
internal fun ceremonySignatureMatchesTheProjector() {
    assertEquals("none", ceremonySignature(null))

    val base = projectionInPhase(RevealPhase.REVEALING).copy(revealedCount = 3, focusedTeamId = "t1")
    assertEquals("revealing|3|t1", ceremonySignature(base))
    assertEquals(ceremonySignature(base), ceremonySignature(base.copy()))

    // An unchanged reload must not read as movement — that is the whole bug this
    // guards: clearing the label while the overlay is still up.
    assertEquals(ceremonySignature(base), ceremonySignature(base.copy(frozenCount = 99)))

    // Every command shape does read as movement.
    assertTrue(ceremonySignature(base) != ceremonySignature(base.copy(revealedCount = 4)))
    assertTrue(ceremonySignature(base) != ceremonySignature(base.copy(focusedTeamId = "t2")))
    assertTrue(ceremonySignature(base) != ceremonySignature(base.copy(focusedTeamId = null)))
    assertTrue(ceremonySignature(base) != ceremonySignature(base.copy(phase = RevealPhase.DONE)))
    assertTrue(ceremonySignature(base) != ceremonySignature(base.copy(phase = RevealPhase.IDLE)))
}

// ---------------------------------------------------------------------------
// request shapes
// ---------------------------------------------------------------------------

internal fun bodilessCommandsSendNoBody() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(200, projectionJson()) }
    val client = unlockedClient(transport)

    val commands = listOf<suspend () -> CommandOutcome>(
        { client.step() },
        { client.back() },
        { client.reset() },
        { client.jumpPending() },
    )
    for (command in commands) {
        command()
        val request = transport.last()
        // step/back/reset/jump-pending request models are extra="forbid"; even "{}" is
        // pointless and any stray field would be a 422.
        assertNull(request.body, "a bodiless command must send no body")
        assertTrue("Content-Type" !in request.headers, "no body means no Content-Type")
        assertEquals(HttpMethod.POST, request.method)
        assertEquals("Bearer operator-token", request.headers["Authorization"])
        assertEquals("application/json", request.headers["Accept"])
    }
}

/**
 * The media cues answer `204` and are unlike every other command on the wire.
 *
 * Each assertion here corresponds to something the server relies on: no body
 * (the routes take none), no `Idempotency-Key` (the receipt ring lives in saved
 * state these commands never write), and the controller id (raising or blanking
 * a projector is a control action, so ownership is required in both directions).
 */
internal fun mediaCuesAreBodilessAndKeyless() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(204, "") }
    val client = unlockedClient(transport)

    assertEquals(MediaCueOutcome.Sent, client.showMedia())
    assertEquals("${ENDPOINTS.showTeamMedia}", transport.last().url)
    assertEquals(MediaCueOutcome.Sent, client.hideMedia())
    assertEquals("${ENDPOINTS.hideTeamMedia}", transport.last().url)

    for (request in transport.sent) {
        assertEquals(HttpMethod.POST, request.method)
        assertNull(request.body, "a media cue takes no body")
        assertTrue("Content-Type" !in request.headers, "no body means no Content-Type")
        assertEquals("Bearer operator-token", request.headers["Authorization"])
        assertEquals("controller-00000001", request.headers[CONTROLLER_ID_HEADER])
        assertTrue(
            "Idempotency-Key" !in request.headers,
            "a cue persists nothing, so there is no receipt ring to match a key against",
        )
    }
}

/**
 * A failed cue must never engage the ambiguity lock, and `hide` must survive it.
 *
 * The lock exists so a retried `step` cannot reveal two teams. A cue reveals
 * nothing, so locking over one would be pure cost — and hiding is precisely what
 * an operator needs when an ambiguous `5xx` has left a photograph over the
 * board. `show` stays suppressed there, because raising an overlay while the
 * ceremony's true position is unknown risks showing the wrong team.
 */
internal fun mediaCuesNeverLockAndHideSurvivesTheLock() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(503, """{"detail":"unavailable"}""") }
    val client = unlockedClient(transport)

    val refused = client.showMedia()
    assertTrue(refused is MediaCueOutcome.Refused, "a 503 on a cue is stated, not ambiguous")
    assertTrue(!client.isBlocked, "a failed cue must not lock the pad")

    transport.responder = { throw TransportFailure("network down") }
    assertTrue(client.showMedia() is MediaCueOutcome.Failed)
    assertTrue(!client.isBlocked, "a transport failure on a cue must not lock the pad either")

    // Now lock the client for real, with a genuinely ambiguous reveal command.
    assertTrue(client.step() is CommandOutcome.Ambiguous)
    assertTrue(client.isBlocked)

    transport.responder = { HttpResponse(204, "") }
    assertEquals(MediaCueOutcome.Suppressed, client.showMedia())
    assertEquals(MediaCueOutcome.Sent, client.hideMedia())
    assertTrue(client.isBlocked, "hiding resolves nothing about the ambiguous command")
}

/** A cue must not become the attempt that **Retry same command** re-sends. */
internal fun mediaCuesDoNotBecomeTheRetryableAttempt() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { throw TransportFailure("network down") }
    val client = unlockedClient(transport)

    assertTrue(client.step() is CommandOutcome.Ambiguous)
    assertTrue(client.canRetryLastAttempt)

    transport.responder = { HttpResponse(204, "") }
    client.hideMedia()

    // Still the step, which is the command whose outcome is genuinely unresolved.
    assertTrue(client.canRetryLastAttempt)
    transport.responder = { HttpResponse(200, projectionJson()) }
    client.retryLastAttempt()
    assertEquals(ENDPOINTS.step, transport.last().url)
}

internal fun startAndJumpBodyShapes() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(200, projectionJson()) }
    val client = unlockedClient(transport)

    client.start(siteId = null, restart = false)
    assertEquals("""{"site_id":null,"restart":false}""", transport.last().body)
    assertEquals("application/json", transport.last().headers["Content-Type"])

    client.start(siteId = "22222222-2222-2222-2222-222222222222", restart = true)
    assertEquals(
        """{"site_id":"22222222-2222-2222-2222-222222222222","restart":true}""",
        transport.last().body,
    )

    // A blank site id means the global ceremony and must be sent as null:
    // start-reveal requires exact equality with the token's own scope.
    client.start(siteId = "   ", restart = false)
    assertEquals("""{"site_id":null,"restart":false}""", transport.last().body)

    client.jump("aaaa1111-0000-0000-0000-000000000001")
    assertEquals("""{"team_id":"aaaa1111-0000-0000-0000-000000000001"}""", transport.last().body)

    // GET state carries the credential but never an Idempotency-Key: a read has
    // nothing to replay.
    client.loadState()
    assertTrue("Idempotency-Key" !in transport.last().headers)
    assertEquals(HttpMethod.GET, transport.last().method)
}

internal fun freshKeyPerAttempt() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(200, projectionJson()) }
    val client = unlockedClient(transport)

    client.step()
    client.step()
    client.back()
    client.jumpPending()

    val sentKeys = transport.sent.mapNotNull { it.headers["Idempotency-Key"] }
    assertEquals(4, sentKeys.size)
    assertEquals(sentKeys.toSet().size, sentKeys.size, "two deliberate presses are two commands")
    for (key in sentKeys) {
        assertTrue(IDEMPOTENCY_KEY_PATTERN.matches(key), "key must satisfy the server's pattern: $key")
    }
    // A v4 UUID — the production generator — also satisfies the server pattern.
    assertTrue(IDEMPOTENCY_KEY_PATTERN.matches(java.util.UUID.randomUUID().toString()))
}

internal fun controllerHeaderCoversEveryMutation() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(200, projectionJson()) }
    val client = unlockedClient(transport)

    client.start(null, false)
    client.step()
    client.back()
    client.reset()
    client.jump("team-00000001")
    client.jumpPending()

    assertEquals(6, transport.sent.size)
    for (request in transport.sent) {
        assertEquals("controller-00000001", request.headers[CONTROLLER_ID_HEADER])
        assertEquals(HttpMethod.POST, request.method)
    }
    assertTrue(CONTROLLER_ID_PATTERN.matches(java.util.UUID.randomUUID().toString()))
}

internal fun stateGetIsControllerIndependent() = runBlocking {
    val transport = FakeTransport()
    transport.responder = { HttpResponse(200, projectionJson()) }
    val client = unlockedClient(transport)
    client.loadState()
    assertTrue(CONTROLLER_ID_HEADER !in transport.last().headers)
}

internal fun leaseOperationsUseDedicatedEndpoints() = runBlocking {
    val transport = FakeTransport()
    val client = CommandClient(
        transport,
        ENDPOINTS,
        controllerId = "controller-00000001",
        newKey = { "key-00000001" },
    )
    client.setSecret("operator-token")
    transport.responder = { HttpResponse(200, leaseJson()) }

    client.claimLease()
    client.heartbeatLease()
    client.takeoverLease()
    client.releaseLease()

    assertEquals(
        listOf(
            ENDPOINTS.leaseClaim,
            ENDPOINTS.leaseHeartbeat,
            ENDPOINTS.leaseTakeover,
            ENDPOINTS.leaseRelease,
        ),
        transport.sent.map { it.url },
    )
    for (request in transport.sent) {
        assertEquals("controller-00000001", request.headers[CONTROLLER_ID_HEADER])
        assertEquals("Bearer operator-token", request.headers["Authorization"])
        assertTrue("Idempotency-Key" !in request.headers)
        assertNull(request.body)
    }
}

/** The readout wording matches `projectorCopy` in control-ownership.js, null included. */
internal fun projectorLabelMatchesTheWebPanel() {
    assertEquals("Projector count unavailable", projectorLabel(null))
    assertEquals("No projectors connected", projectorLabel(0))
    assertEquals("1 projector connected", projectorLabel(1))
    assertEquals("3 projectors connected", projectorLabel(3))
}
