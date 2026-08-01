//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.noca.animator.remote.core.CommandClient
import org.noca.animator.remote.core.CommandOutcome
import org.noca.animator.remote.core.ControlVisibility
import org.noca.animator.remote.core.GLOBAL_SCOPE
import org.noca.animator.remote.core.HttpMethod
import org.noca.animator.remote.core.HttpRequest
import org.noca.animator.remote.core.RELOAD_REQUIRED
import org.noca.animator.remote.core.RevealProjection
import org.noca.animator.remote.core.SequenceOutcome
import org.noca.animator.remote.core.SiteMeta
import org.noca.animator.remote.core.UNKNOWN_OUTCOME
import org.noca.animator.remote.core.controlEndpoints
import org.noca.animator.remote.core.metaUrl
import org.noca.animator.remote.core.parseContestMeta
import org.noca.animator.remote.core.revealEventsUrl
import org.noca.animator.remote.net.OkHttpTransport
import org.noca.animator.remote.net.RevealEventSource
import org.noca.animator.remote.store.RemoteSettings
import org.noca.animator.remote.store.SettingsStore
import org.noca.animator.remote.store.TokenVault

/** Everything the UI renders. */
data class RemoteUiState(
    val settings: RemoteSettings = RemoteSettings(),
    val settingsLoaded: Boolean = false,
    val hasToken: Boolean = false,
    val inFlight: Boolean = false,
    val blocked: Boolean = false,
    val streaming: Boolean = false,
    val projection: RevealProjection? = null,
    val visibility: ControlVisibility = ControlVisibility.NONE,
    val stateUnusable: Boolean = false,
    val canRetry: Boolean = false,
    val noCeremony: Boolean = false,
    val status: String = "",
    val error: String? = null,
    val sites: List<SiteMeta> = emptyList(),
    val contestName: String = "",
    /**
     * A stored token is being restored and validated.
     *
     * The UI must show a restoring state rather than the setup form while this is
     * true: presenting an empty token field during the restore invites an operator
     * to retype a credential they had already saved.
     */
    val restoringSession: Boolean = false,
    /** A token was stored but could not be decrypted, so it must be re-entered. */
    val storedTokenUnreadable: Boolean = false,
) {
    /** Whether any command may be issued right now. */
    val commandsEnabled: Boolean get() = hasToken && !inFlight && !blocked
}

/**
 * Drives one ceremony from the UI.
 *
 * Every call into [CommandClient] happens on `viewModelScope`, whose dispatcher
 * is `Dispatchers.Main.immediate`. That is not incidental: the client is
 * single-dispatcher-confined by design (its JavaScript original gets the same
 * guarantee from the browser event loop), and the transport is the only thing
 * that hops to an I/O thread.
 */
class RemoteViewModel(application: Application) : AndroidViewModel(application) {

    private val settingsStore = SettingsStore(application)
    private val vault = TokenVault(application)
    private val transport = OkHttpTransport()
    private val eventSource = RevealEventSource()

    private val _state = MutableStateFlow(RemoteUiState())
    val state: StateFlow<RemoteUiState> = _state.asStateFlow()

    private var client: CommandClient? = null
    private var streamJob: Job? = null
    private var settings = RemoteSettings()

    init {
        viewModelScope.launch {
            settings = settingsStore.settings.first()

            // Read the credential BEFORE any network call, and publish the
            // restoring state in the same update that publishes the settings.
            //
            // Both orderings matter. Reading the vault after `loadMeta()` would
            // make the credential wait on an unrelated request for the site list,
            // and announcing `settingsLoaded` without `restoringSession` would show
            // the setup form -- with an empty token field -- for the whole duration
            // of the restore. Either way an operator reopening the app mid-ceremony
            // is invited to retype a token that was already saved.
            val hadStoredToken = settings.isComplete && vault.hasStoredToken
            val storedToken = if (settings.isComplete) vault.load() else null

            _state.update {
                it.copy(
                    settings = settings,
                    settingsLoaded = true,
                    hasToken = false,
                    restoringSession = storedToken != null,
                    storedTokenUnreadable = hadStoredToken && storedToken == null,
                )
            }

            if (!settings.isComplete) {
                return@launch
            }
            rebuildClient()
            if (storedToken != null) {
                try {
                    performUnlock(storedToken, persist = false)
                } finally {
                    _state.update { it.copy(restoringSession = false) }
                }
            }
            // The site list is only needed by the scope picker, so it loads last.
            loadMeta()
        }
    }

    /** Persists connection settings and rebuilds the client against them. */
    fun saveSettings(baseUrl: String, slug: String, scope: String) {
        viewModelScope.launch {
            val updated = RemoteSettings(baseUrl = baseUrl.trim(), slug = slug.trim(), scope = scope.trim())
            settingsStore.save(updated)
            settings = updated
            client = null
            streamJob?.cancel()
            _state.update {
                it.copy(
                    settings = updated,
                    projection = null,
                    visibility = ControlVisibility.NONE,
                    hasToken = false,
                    blocked = false,
                    streaming = false,
                    error = null,
                    status = "",
                    sites = emptyList(),
                    contestName = "",
                )
            }
            if (updated.isComplete) {
                rebuildClient()
                loadMeta()
            }
        }
    }

    /**
     * Saves settings and unlocks with [token] in one ordered step.
     *
     * Separate from [saveSettings] + [unlock] on purpose: those two are each
     * asynchronous, and calling them in sequence from the UI would race — the
     * unlock could reach the client built for the *previous* slug.
     */
    fun connect(baseUrl: String, slug: String, scope: String, token: String) {
        viewModelScope.launch {
            val updated = RemoteSettings(
                baseUrl = baseUrl.trim(),
                slug = slug.trim(),
                scope = scope.trim().ifEmpty { GLOBAL_SCOPE },
            )
            settingsStore.save(updated)
            settings = updated
            streamJob?.cancel()
            _state.update {
                it.copy(settings = updated, error = null, status = "", storedTokenUnreadable = false)
            }
            rebuildClient()
            if (!updated.isComplete) {
                return@launch
            }
            // Unlock first: the operator is waiting on the connection, not on the
            // scope picker's site list.
            performUnlock(token, persist = true)
            loadMeta()
        }
    }

    /**
     * Accepts a token and loads authoritative state with it.
     *
     * Deliberately `suspend` and private rather than a `launch`-ing public entry
     * point: startup must *await* the restore before choosing which screen to show,
     * and [connect] must run it strictly after the settings are saved. Both callers
     * already own a coroutine, so a fire-and-forget variant would only reintroduce
     * the ordering bug it replaced.
     *
     * @param persist Whether to store the token encrypted. `false` when it *came*
     *   from the vault, so a silent restore does not rewrite what it just read.
     */
    private suspend fun performUnlock(token: String, persist: Boolean) {
        val active = client ?: return
        if (token.isBlank()) {
            return
        }
        active.setSecret(token)
        _state.update { it.copy(inFlight = true, error = null, status = "") }
        val outcome = active.loadState()
        _state.update { it.copy(inFlight = false) }
        // Only store a token the server did not reject.
        if (persist && outcome !is CommandOutcome.AuthFailure) {
            vault.save(token)
        }
        applyOutcome(outcome)
        if (active.hasSecret) {
            restartStream()
        }
    }

    /** Forgets the token, in memory and on disk. */
    fun forgetToken() {
        vault.clear()
        client?.forgetSecret()
        streamJob?.cancel()
        _state.update {
            it.copy(
                hasToken = false,
                projection = null,
                visibility = ControlVisibility.NONE,
                blocked = false,
                streaming = false,
                canRetry = false,
                noCeremony = false,
                status = "",
                error = null,
            )
        }
    }

    fun start(restart: Boolean) {
        // start-reveal must name the token's own scope exactly, `null` included.
        val siteId = settings.scope.takeIf { it != GLOBAL_SCOPE }
        command { it.start(siteId = siteId, restart = restart) }
    }

    fun step() = command { it.step() }

    fun back() = command { it.back() }

    fun reset() = command { it.reset() }

    fun jump(teamId: String) = command { it.jump(teamId) }

    fun stepMany(count: Int) = sequence("Advancing", count) { it.stepMany(count) }

    fun backMany(count: Int) = sequence("Returning", count) { it.backMany(count) }

    /** The operator's explicit acknowledgement of an unknown outcome. */
    fun reloadState() {
        val active = client ?: return
        viewModelScope.launch {
            _state.update { it.copy(inFlight = true, error = null, status = "Reloading ceremony state…") }
            val outcome = active.reloadState()
            _state.update { it.copy(inFlight = false) }
            applyOutcome(outcome)
        }
    }

    /**
     * Re-sends the ambiguous attempt under its original key.
     *
     * Permitted while locked — that is the point. The server replays the most
     * recent key instead of applying it again.
     */
    fun retryLastAttempt() {
        val active = client ?: return
        viewModelScope.launch {
            _state.update { it.copy(inFlight = true, error = null, status = "Retrying the same command…") }
            val outcome = active.retryLastAttempt()
            _state.update { it.copy(inFlight = false) }
            applyOutcome(outcome)
        }
    }

    // ------------------------------------------------------------------
    // internals
    // ------------------------------------------------------------------

    private fun rebuildClient() {
        client = runCatching {
            CommandClient(transport, controlEndpoints(settings.baseUrl, settings.slug))
        }.getOrElse { error ->
            _state.update { it.copy(error = error.message ?: "The contest slug or URL is not usable.") }
            null
        }
    }

    /** Issues one command, refusing when the client is locked or busy. */
    private fun command(block: suspend (CommandClient) -> CommandOutcome) {
        val active = client ?: return
        viewModelScope.launch {
            _state.update { it.copy(inFlight = true, error = null) }
            val outcome = block(active)
            _state.update { it.copy(inFlight = false) }
            applyOutcome(outcome)
        }
    }

    private fun sequence(
        label: String,
        count: Int,
        block: suspend (CommandClient) -> SequenceOutcome,
    ) {
        val active = client ?: return
        viewModelScope.launch {
            _state.update { it.copy(inFlight = true, error = null, status = "$label 0 / $count…") }
            val outcome = block(active)
            _state.update {
                it.copy(inFlight = false, status = "$label ${outcome.completed} / ${outcome.requested}")
            }
            applyOutcome(outcome.last)
        }
    }

    /** Maps one outcome onto operator-facing state. */
    private fun applyOutcome(outcome: CommandOutcome) {
        val active = client
        var status: String? = null
        var error: String? = null

        when (outcome) {
            is CommandOutcome.Confirmed -> {
                status = ""
            }

            CommandOutcome.NoCeremony -> {
                status = "No ceremony has been started for this credential."
            }

            is CommandOutcome.StatedRefusal -> {
                error = outcome.detail ?: "The command was refused."
            }

            is CommandOutcome.Ambiguous -> {
                status = UNKNOWN_OUTCOME
                error = RELOAD_REQUIRED
            }

            CommandOutcome.AuthFailure -> {
                // The server rejected it, so the stored copy is worthless too.
                vault.clear()
                streamJob?.cancel()
                status = ""
                error = "Invalid or expired operator token. Enter it again."
            }

            CommandOutcome.Suppressed -> Unit

            is CommandOutcome.StateUnusable -> {
                error = "The stored reveal state cannot be read. Use Rebuild state to replace it."
            }

            is CommandOutcome.StateLoadFailed -> {
                error = "The ceremony state could not be loaded."
            }
        }

        _state.update { current ->
            current.copy(
                hasToken = active?.hasSecret == true,
                blocked = active?.isBlocked == true,
                projection = active?.projection,
                visibility = active?.visibility ?: ControlVisibility.NONE,
                stateUnusable = active?.stateUnusable == true,
                canRetry = active?.canRetryLastAttempt == true,
                noCeremony = outcome is CommandOutcome.NoCeremony,
                status = status ?: current.status,
                error = error,
                streaming = if (outcome is CommandOutcome.AuthFailure) false else current.streaming,
            )
        }
    }

    /** Loads the public meta feed, which supplies the site list for the picker. */
    private suspend fun loadMeta() {
        val url = runCatching { metaUrl(settings.baseUrl, settings.slug) }.getOrNull() ?: return
        val response = runCatching {
            transport.send(HttpRequest(HttpMethod.GET, url, mapOf("Accept" to "application/json")))
        }.getOrNull() ?: return
        if (response.status !in 200..299) {
            return
        }
        val meta = parseContestMeta(response.body) ?: return
        _state.update { it.copy(sites = meta.sites, contestName = meta.name) }
    }

    /**
     * Subscribes to the ceremony's public nudge feed, reconnecting with backoff.
     *
     * The stream is only ever a *hint*: each nudge triggers a refetch of
     * authoritative state, and [CommandClient.refreshFromNudge] guarantees that
     * refetch can never release the ambiguous-outcome lock. A missed nudge is
     * therefore harmless, which is why a plain reconnect loop is sufficient and no
     * replay mechanism is needed.
     */
    private fun restartStream() {
        streamJob?.cancel()
        val url = runCatching { revealEventsUrl(settings.baseUrl, settings.slug, settings.scope) }
            .getOrNull() ?: return

        streamJob = viewModelScope.launch {
            var backoffMs = 1_000L
            while (isActive) {
                try {
                    eventSource.nudges(url).collect {
                        backoffMs = 1_000L
                        _state.update { current -> current.copy(streaming = true) }
                        refreshDisplay()
                    }
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: Throwable) {
                    // Fall through to the backoff below; a dropped stream is
                    // expected on a phone moving between networks.
                }
                _state.update { current -> current.copy(streaming = false) }
                delay(backoffMs)
                backoffMs = (backoffMs * 2).coerceAtMost(30_000L)
            }
        }
    }

    /** Refreshes the projection for display only, never touching the lock. */
    private suspend fun refreshDisplay() {
        val active = client ?: return
        if (!active.hasSecret) {
            return
        }
        val outcome = active.refreshFromNudge()
        if (outcome is CommandOutcome.Suppressed) {
            return
        }
        _state.update { current ->
            current.copy(
                projection = active.projection,
                visibility = active.visibility,
                blocked = active.isBlocked,
                stateUnusable = active.stateUnusable,
                noCeremony = outcome is CommandOutcome.NoCeremony,
            )
        }
    }

    override fun onCleared() {
        streamJob?.cancel()
        super.onCleared()
    }
}
