//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.net

import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.noca.animator.remote.core.HttpMethod
import org.noca.animator.remote.core.HttpRequest
import org.noca.animator.remote.core.HttpResponse
import org.noca.animator.remote.core.HttpTransport
import org.noca.animator.remote.core.TransportFailure

/** The single JSON media type the control API speaks. */
private val JSON = "application/json; charset=utf-8".toMediaType()

/** An empty POST body, for the commands whose request model forbids fields. */
private val EMPTY_BODY = ByteArray(0).toRequestBody(null, 0, 0)

/**
 * The one real implementation of [HttpTransport].
 *
 * It does exactly two things beyond calling OkHttp: it converts every I/O error
 * into [TransportFailure], and it converts nothing else. An HTTP error status
 * must arrive at [org.noca.animator.remote.core.CommandClient] as a status,
 * because the status is what separates a stated refusal from an ambiguous
 * outcome — the distinction the whole safety model rests on.
 */
class OkHttpTransport(
    private val client: OkHttpClient = defaultClient(),
) : HttpTransport {

    override suspend fun send(request: HttpRequest): HttpResponse = withContext(Dispatchers.IO) {
        val builder = Request.Builder().url(request.url)
        for ((name, value) in request.headers) {
            builder.header(name, value)
        }
        when (request.method) {
            HttpMethod.GET -> builder.get()
            HttpMethod.POST -> builder.post(request.body?.toRequestBody(JSON) ?: EMPTY_BODY)
        }

        try {
            client.newCall(builder.build()).execute().use { response ->
                HttpResponse(status = response.code, body = response.body.string())
            }
        } catch (failure: IOException) {
            throw TransportFailure(failure.message ?: "the request did not complete", failure)
        }
    }

    companion object {
        /**
         * The command client's HTTP client.
         *
         * `retryOnConnectionFailure` is **disabled deliberately**. OkHttp's
         * automatic retry would re-send a `POST` that failed before a response
         * arrived, which is precisely the event this app treats as ambiguous and
         * surfaces to the operator. Our own recovery is explicit and safe — the
         * same attempt re-sent under its original `Idempotency-Key`, which the
         * server replays — and a silent retry underneath that would make the
         * observed behaviour impossible to reason about mid-ceremony.
         */
        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .retryOnConnectionFailure(false)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .writeTimeout(20, TimeUnit.SECONDS)
            .build()

        /**
         * A client for the Server-Sent Events feed.
         *
         * The read timeout must be infinite: an SSE stream is idle by design
         * between ceremony steps, and FastAPI's 15 s comment heartbeat is the
         * only traffic on a quiet ceremony. `pingInterval` keeps a NAT or mobile
         * carrier from silently dropping the idle connection.
         */
        fun sseClient(): OkHttpClient = OkHttpClient.Builder()
            .retryOnConnectionFailure(true)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .pingInterval(30, TimeUnit.SECONDS)
            .build()
    }
}
