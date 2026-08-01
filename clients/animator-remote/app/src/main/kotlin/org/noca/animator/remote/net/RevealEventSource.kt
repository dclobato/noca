//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.net

import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources

/**
 * A nudge from the ceremony's public event feed.
 *
 * The animator's `/reveal/events` stream carries **invalidation nudges, not
 * state**: the payload is metadata only, and the client's contract is to refetch
 * authoritative state. So this type deliberately carries no ceremony data — a
 * nudge means "refetch", nothing more, and a missed nudge is harmless.
 */
sealed interface RevealNudge {

    /**
     * The stream is live.
     *
     * Emitted from the server's `reveal_ready` event, which arrives **after** the
     * server's Valkey subscription is established. Reconciling here rather than
     * on connection-open is what closes the window in which a publication would
     * reach neither the client's fetch nor its subscription — pub/sub has no
     * replay.
     */
    data object Ready : RevealNudge

    /** The ceremony changed; refetch state. */
    data object StateChanged : RevealNudge
}

/**
 * Subscribes to one ceremony's public, credential-free event feed.
 *
 * The operator token is deliberately **not** sent here. This feed grants nothing
 * and needs nothing, so there is no reason to expose a ceremony credential to
 * it.
 */
class RevealEventSource(
    private val client: OkHttpClient = OkHttpTransport.sseClient(),
) {

    /**
     * Streams nudges from [url] until the collector is cancelled.
     *
     * The flow **completes** (rather than throwing) when the connection fails, so
     * the caller can reconnect with its own backoff. Reconnecting is the caller's
     * business because the correct action on reconnect is to refetch state, and
     * only the caller can do that.
     */
    fun nudges(url: String): Flow<RevealNudge> = callbackFlow {
        val request = Request.Builder()
            .url(url)
            .header("Accept", "text/event-stream")
            .build()

        val listener = object : EventSourceListener() {
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                when (type) {
                    "reveal_ready" -> trySend(RevealNudge.Ready)
                    "reveal_state_changed" -> trySend(RevealNudge.StateChanged)
                    // Any other event type is ignored rather than treated as a
                    // change: a future event this client does not understand must
                    // not be read as a ceremony mutation.
                    else -> Unit
                }
            }

            override fun onClosed(eventSource: EventSource) {
                close()
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                response?.close()
                close()
            }
        }

        val eventSource = EventSources.createFactory(client).newEventSource(request, listener)
        awaitClose { eventSource.cancel() }
    }
}
