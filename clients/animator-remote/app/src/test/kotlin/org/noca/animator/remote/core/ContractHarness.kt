//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.core

import java.io.File
import kotlinx.coroutines.runBlocking

/**
 * The fake transport and fixtures the contract checks share.
 *
 * [CommandClient] takes its HTTP port as a dependency precisely so this can exist:
 * every safety rule in the client is exercised against a scripted responder, with
 * no network, no Android, and no emulator.
 */

/** Records every request and answers from a scriptable responder. */
internal class FakeTransport : HttpTransport {
    val sent = mutableListOf<HttpRequest>()
    var responder: (HttpRequest) -> HttpResponse = { HttpResponse(200, "{}") }

    override suspend fun send(request: HttpRequest): HttpResponse {
        sent += request
        return responder(request)
    }

    /** The last request issued, or a failure when nothing was sent. */
    fun last(): HttpRequest = sent.lastOrNull() ?: error("no request was sent")
}

/** Anchor for classpath resource lookup. */
private class HarnessMarker

internal val ENDPOINTS = controlEndpoints("https://animator.example.com", "maratona-2026")

/**
 * Reads a fixture from the classpath when present, else from the source tree.
 *
 * Gradle puts `src/test/resources` on the test classpath; the container runner has
 * no classpath resources and passes a directory instead. Supporting both is what
 * lets one file serve both execution paths.
 */
internal fun fixture(name: String): String {
    HarnessMarker::class.java.getResourceAsStream("/fixtures/$name")?.use { stream ->
        return stream.readBytes().decodeToString()
    }
    val dir = System.getenv("ANIMATOR_REMOTE_FIXTURES")
        ?: System.getProperty("animator.remote.fixtures")
        ?: "app/src/test/resources/fixtures"
    return File(dir, name).readText()
}

internal fun projectionJson(): String = fixture("projection.json")

internal fun leaseJson(): String = fixture("controller-lease.json")

/** A client with a deterministic key generator, already holding a credential. */
internal fun unlockedClient(
    transport: FakeTransport,
    keys: MutableList<String> = mutableListOf(),
): CommandClient {
    var counter = 0
    val client = CommandClient(transport, ENDPOINTS, controllerId = "controller-00000001") {
        counter += 1
        "key-000000$counter".also { keys += it }
    }
    client.setSecret("operator-token")
    val responder = transport.responder
    transport.responder = { HttpResponse(200, leaseJson()) }
    runBlocking { client.claimLease() }
    transport.sent.clear()
    transport.responder = responder
    return client
}

/** A minimal projection in [phase], for the visibility truth table. */
internal fun projectionInPhase(phase: String): RevealProjection = RevealProjection(
    contestId = "c1",
    scope = GLOBAL_SCOPE,
    siteId = null,
    siteName = null,
    phase = phase,
    revealedCount = 0,
    frozenCount = 0,
)
