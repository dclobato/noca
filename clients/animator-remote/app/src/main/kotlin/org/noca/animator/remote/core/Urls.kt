//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.core

/**
 * Endpoint construction for one contest on one animator deployment.
 *
 * Two rules are enforced here rather than trusted to the caller:
 *
 *  1. **The base URL is normalized.** An operator typing
 *     `https://animator.example.com/` and one typing it without the trailing
 *     slash must produce the same request, not one with a doubled `//`.
 *  2. **Path segments are validated, never escaped.** A slug or site id is
 *     interpolated into a URL path, so a value containing `/`, `?`, `#`, or `..`
 *     could redirect the request to a different endpoint entirely. Rejecting
 *     such a value outright is safer than percent-encoding it and hoping every
 *     intermediary agrees on the decoding.
 */

/** The literal scope value naming the contest-wide ceremony. */
const val GLOBAL_SCOPE: String = "global"

/** Characters allowed in a slug, site id, or team id used as a path segment. */
private val SAFE_SEGMENT = Regex("^[A-Za-z0-9._~-]+$")

/** The control and controller-lease endpoints for one contest. */
data class ControlEndpoints(
    val state: String,
    val start: String,
    val step: String,
    val back: String,
    val reset: String,
    val jump: String,
    val jumpPending: String,
    val leaseClaim: String,
    val leaseHeartbeat: String,
    val leaseRelease: String,
    val leaseTakeover: String,
)

/** Trims whitespace and any trailing slashes from a base URL. */
fun normalizeBaseUrl(baseUrl: String): String = baseUrl.trim().trimEnd('/')

/**
 * Returns [segment] unchanged when it is safe to interpolate into a URL path.
 *
 * @throws IllegalArgumentException if it is empty or holds anything outside
 *   [SAFE_SEGMENT] — including a path separator or a dot-segment.
 */
fun requireSafeSegment(segment: String): String {
    val trimmed = segment.trim()
    require(trimmed.isNotEmpty()) { "path segment must not be empty" }
    require(SAFE_SEGMENT.matches(trimmed)) {
        "path segment contains characters that are not allowed in a URL path: $segment"
    }
    require(trimmed != "." && trimmed != "..") { "path segment must not be a dot-segment" }
    return trimmed
}

/** The `/c/{slug}` prefix every animator contest route hangs off. */
fun contestBaseUrl(baseUrl: String, slug: String): String =
    "${normalizeBaseUrl(baseUrl)}/c/${requireSafeSegment(slug)}"

/** The public contest meta feed, used to build the scope picker. */
fun metaUrl(baseUrl: String, slug: String): String = "${contestBaseUrl(baseUrl, slug)}/meta"

/**
 * Validates a ceremony scope: either [GLOBAL_SCOPE] or a site id.
 *
 * The animator resolves an unknown scope to the same bare `404` an unknown slug
 * produces, so a malformed value is worth catching before it costs a request.
 */
fun requireValidScope(scope: String): String =
    if (scope.trim() == GLOBAL_SCOPE) GLOBAL_SCOPE else requireSafeSegment(scope)

/** The credential-free spectator state feed for one ceremony. */
fun revealStateUrl(baseUrl: String, slug: String, scope: String): String =
    "${contestBaseUrl(baseUrl, slug)}/reveal/state?scope=${requireValidScope(scope)}"

/** The credential-free SSE feed carrying ceremony invalidation nudges. */
fun revealEventsUrl(baseUrl: String, slug: String, scope: String): String =
    "${contestBaseUrl(baseUrl, slug)}/reveal/events?scope=${requireValidScope(scope)}"

/**
 * Builds the seven operator control endpoints.
 *
 * None of them takes a query parameter: every command derives its scope from the
 * credential and the stored session, so no request may redirect a ceremony.
 */
fun controlEndpoints(baseUrl: String, slug: String): ControlEndpoints {
    val base = "${contestBaseUrl(baseUrl, slug)}/control"
    val leaseBase = "$base/controller-lease"
    return ControlEndpoints(
        state = "$base/state",
        start = "$base/start-reveal",
        step = "$base/step",
        back = "$base/back",
        reset = "$base/reset",
        jump = "$base/jump-team",
        jumpPending = "$base/jump-pending",
        leaseClaim = "$leaseBase/claim",
        leaseHeartbeat = "$leaseBase/heartbeat",
        leaseRelease = "$leaseBase/release",
        leaseTakeover = "$leaseBase/takeover",
    )
}
