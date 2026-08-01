//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.core

/**
 * The HTTP port the command client talks through.
 *
 * `control.js` takes `fetchImpl` as an explicit dependency so a Node test can
 * exercise the real state machine with no browser. This is the same seam for the
 * same reason: [CommandClient] holds the safety rules that keep a ceremony from
 * double-revealing a team, and those rules must be testable on a plain JVM with
 * no Android, no OkHttp, and no network.
 */

/** The two methods the control API uses. */
enum class HttpMethod {
    GET,
    POST,
}

/**
 * One outbound request.
 *
 * @property method HTTP method.
 * @property url Absolute URL.
 * @property headers Complete header set; the transport adds nothing.
 * @property body Serialized JSON body, or `null` for a bodiless request. The
 *   control API's step/back/reset take **no** body: their request models are
 *   `extra="forbid"`, so anything unexpected is a 422.
 */
data class HttpRequest(
    val method: HttpMethod,
    val url: String,
    val headers: Map<String, String>,
    val body: String? = null,
)

/**
 * One inbound response.
 *
 * @property status HTTP status code.
 * @property body Raw response body; may be empty.
 */
data class HttpResponse(
    val status: Int,
    val body: String,
)

/**
 * Raised by a transport that never obtained an HTTP status at all.
 *
 * This is the *ambiguous* failure mode and is deliberately distinct from any
 * status-bearing response: a request that died in flight may still have been
 * applied by the server, so it can never be treated as a refusal.
 */
class TransportFailure(
    message: String,
    cause: Throwable? = null,
) : Exception(message, cause)

/**
 * Sends one request and returns its response.
 *
 * Implementations must translate every connectivity, TLS, and timeout error into
 * [TransportFailure] and must not translate an HTTP error status into an
 * exception — the status is what distinguishes a stated refusal from an
 * ambiguous outcome.
 */
fun interface HttpTransport {
    suspend fun send(request: HttpRequest): HttpResponse
}
