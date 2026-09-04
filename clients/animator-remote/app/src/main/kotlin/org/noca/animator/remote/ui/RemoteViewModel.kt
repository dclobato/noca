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
import org.noca.animator.remote.core.ControllerLeaseState
import org.noca.animator.remote.core.GLOBAL_SCOPE
import org.noca.animator.remote.core.HeartbeatStep
import org.noca.animator.remote.core.HttpMethod
import org.noca.animator.remote.core.HttpRequest
import org.noca.animator.remote.core.LeaseOutcome
import org.noca.animator.remote.core.MediaCueOutcome
import org.noca.animator.remote.core.RELOAD_REQUIRED
import org.noca.animator.remote.core.RevealProjection
import org.noca.animator.remote.core.SequenceOutcome
import org.noca.animator.remote.core.SiteMeta
import org.noca.animator.remote.core.heartbeatStep
import org.noca.animator.remote.core.UNKNOWN_OUTCOME
import org.noca.animator.remote.core.ceremonySignature
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
    val leaseState: ControllerLeaseState = ControllerLeaseState.UNCLAIMED,
    /**
     * The projectors this remote's commands reach, as the server last reported.
     *
     * Every successful lease operation carries `projector_count`, so the value
     * refreshes at the heartbeat cadence with no request of its own. It is
     * held only while the lease is active: a number under a banner that no
     * longer means control would be a stale claim. [ProjectorReadout.count] is
     * `null` when the server could not tell, which is shown as such and never
     * as zero.
     */
    val projectors: ProjectorReadout? = null,
    /**
     * Whether this remote believes a team-media overlay is on the projectors.
     *
     * Local rather than read from the projection, because the cue persists
     * nothing: there is no authoritative "is the photo up" to read. It stays
     * honest because the projector clears its overlay on exactly the signal that
     * resets this flag — a changed `ceremonySignature`, and *only* that. See
     * `RemoteViewModel.mediaShownAfter` for why "any applied projection" would
     * be wrong.
     */
    val mediaShown: Boolean = false,
) {
    /** Whether any command may be issued right now. */
    val commandsEnabled: Boolean
        get() = hasToken && leaseState == ControllerLeaseState.ACTIVE && !inFlight && !blocked

    /**
     * Whether the media cue may be sent right now.
     *
     * Hiding is deliberately permitted while [blocked]. That is the case it
     * matters most in: an ambiguous outcome with a photograph covering the board
     * is exactly when the operator needs the board back, and hiding can never
     * double-apply. Raising one stays behind the ordinary gate, because putting
     * a face on screen while the ceremony's true position is unknown risks
     * showing the wrong team.
     */
    val mediaEnabled: Boolean
        get() = if (mediaShown) {
            hasToken && leaseState == ControllerLeaseState.ACTIVE && !inFlight
        } else {
            commandsEnabled
        }
}

/** One reported projector count; `count == null` means the server could not tell. */
data class ProjectorReadout(val count: Int?)

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
    private var heartbeatJob: Job? = null
    private var foreground = false
    private var settings = RemoteSettings()
    private var lastSignature: String? = null

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
            heartbeatJob?.cancel()
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
                    leaseState = ControllerLeaseState.UNCLAIMED,
                    projectors = null,
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
            heartbeatJob?.cancel()
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
        if (active.hasSecret && outcome.validatesCredential()) {
            applyLeaseOutcome(active.claimLease())
        }
        if (active.hasSecret) {
            restartStream()
        }
    }

    /** Forgets the token, in memory and on disk. */
    fun forgetToken() {
        val active = client
        viewModelScope.launch {
            heartbeatJob?.cancel()
            active?.releaseLease()
            vault.clear()
            active?.forgetSecret()
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
                    leaseState = ControllerLeaseState.UNCLAIMED,
                    projectors = null,
                )
            }
        }
    }

    /** Claims an empty lease again; this never takes control from another panel. */
    fun retryControllerLease() {
        val active = client ?: return
        viewModelScope.launch {
            _state.update { it.copy(inFlight = true, error = null) }
            val outcome = active.claimLease()
            _state.update { it.copy(inFlight = false) }
            applyLeaseOutcome(outcome)
        }
    }

    /** Explicit takeover after the UI's warning has been confirmed. */
    fun takeoverControllerLease() {
        val active = client ?: return
        viewModelScope.launch {
            _state.update { it.copy(inFlight = true, error = null) }
            val outcome = active.takeoverLease()
            _state.update { it.copy(inFlight = false) }
            applyLeaseOutcome(outcome)
        }
    }

    /** Starts prompt renewal and periodic heartbeats while the app is foregrounded. */
    fun onForeground() {
        foreground = true
        val active = client ?: return
        if (!active.hasSecret) return
        viewModelScope.launch {
            val outcome = if (active.leaseState == ControllerLeaseState.ACTIVE) {
                active.heartbeatLease()
            } else {
                active.claimLease()
            }
            applyLeaseOutcome(outcome)
        }
    }

    /** Stops heartbeats and releases ownership best-effort in the background. */
    fun onBackground() {
        foreground = false
        heartbeatJob?.cancel()
        val active = client ?: return
        viewModelScope.launch { applyLeaseOutcome(active.releaseLease()) }
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

    fun jumpPending() = command { it.jumpPending() }

    /**
     * Raises or lowers the focused team's media on every projector in scope.
     *
     * Deliberately not routed through [command]: a cue answers `204` and carries
     * no projection, and — crucially — a failure must not engage the ambiguity
     * lock. Only a confirmed cue flips [RemoteUiState.mediaShown], so a refused
     * one leaves the button describing what is actually on the projector and the
     * operator's next press repeats the attempt rather than sending its opposite.
     */
    fun toggleMedia() {
        val active = client ?: return
        val showing = !_state.value.mediaShown
        viewModelScope.launch {
            _state.update { it.copy(inFlight = true, error = null) }
            val outcome = if (showing) active.showMedia() else active.hideMedia()
            _state.update { it.copy(inFlight = false) }
            applyMediaOutcome(outcome, showing)
        }
    }

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
                leaseState = active?.leaseState ?: ControllerLeaseState.UNCLAIMED,
                projection = active?.projection,
                visibility = active?.visibility ?: ControlVisibility.NONE,
                stateUnusable = active?.stateUnusable == true,
                canRetry = active?.canRetryLastAttempt == true,
                noCeremony = outcome is CommandOutcome.NoCeremony,
                mediaShown = mediaShownAfter(active?.projection, current.mediaShown),
                status = status ?: current.status,
                error = error,
                streaming = if (outcome is CommandOutcome.AuthFailure) false else current.streaming,
            )
        }
    }

    /**
     * Maps a cue's result onto the UI without ever touching the ambiguity lock.
     *
     * The one thing this must not do is set `blocked`: a cue reveals nothing, so
     * a failed one is simply pressed again. It reports *sent*, never *displayed*
     * — the animator publishes the cue and cannot learn whether a projector
     * rendered it.
     */
    private fun applyMediaOutcome(outcome: MediaCueOutcome, showing: Boolean) {
        val active = client
        var status: String? = null
        var error: String? = null
        var shown: Boolean? = null

        when (outcome) {
            MediaCueOutcome.Sent -> {
                shown = showing
                status = if (showing) "Team media sent to the projectors." else ""
            }

            is MediaCueOutcome.Refused -> {
                error = outcome.detail ?: "The media cue was refused."
            }

            is MediaCueOutcome.Failed -> {
                error = outcome.cause
            }

            MediaCueOutcome.AuthFailure -> {
                vault.clear()
                streamJob?.cancel()
                status = ""
                error = "Invalid or expired operator token. Enter it again."
            }

            MediaCueOutcome.Suppressed -> Unit
        }

        _state.update { current ->
            current.copy(
                hasToken = active?.hasSecret == true,
                leaseState = active?.leaseState ?: ControllerLeaseState.UNCLAIMED,
                mediaShown = shown ?: current.mediaShown,
                status = status ?: current.status,
                error = error,
                streaming = if (outcome is MediaCueOutcome.AuthFailure) false else current.streaming,
            )
        }
    }

    /**
     * Mirrors client ownership into the UI without disturbing the ticker.
     *
     * [applyLeaseOutcome] cancels the heartbeat job on any non-active outcome,
     * so the retry path — which must keep ticking — cannot use it just to make
     * a transient state visible.
     */
    private fun syncLeaseState() {
        val active = client ?: return
        _state.update { it.copy(leaseState = active.leaseState) }
    }

    /**
     * The projector readout after a lease outcome.
     *
     * A successful operation replaces it with what the server reported; a
     * stated loss of control or a release clears it; a tolerated blip or a
     * suppressed renewal keeps the last reading, since the lease is still
     * believed active.
     */
    private fun projectorsAfter(outcome: LeaseOutcome, current: ProjectorReadout?): ProjectorReadout? =
        when (outcome) {
            is LeaseOutcome.Active -> ProjectorReadout(outcome.lease.projectorCount)
            LeaseOutcome.ReadOnly, LeaseOutcome.Lost, LeaseOutcome.AuthFailure, LeaseOutcome.Released -> null
            is LeaseOutcome.Unavailable, LeaseOutcome.Suppressed -> current
        }

    /** Maps lease ownership onto command authority without discarding projection state. */
    private fun applyLeaseOutcome(outcome: LeaseOutcome) {
        val active = client ?: return
        val message = when (outcome) {
            is LeaseOutcome.Active -> null
            LeaseOutcome.ReadOnly -> "Another controller is active for this ceremony."
            LeaseOutcome.Lost -> "Controller lease lost. Control moved or expired."
            is LeaseOutcome.Unavailable -> outcome.cause
            LeaseOutcome.AuthFailure -> "Invalid or expired operator token. Enter it again."
            LeaseOutcome.Released, LeaseOutcome.Suppressed -> null
        }
        if (outcome is LeaseOutcome.AuthFailure) {
            vault.clear()
            streamJob?.cancel()
        }
        _state.update {
            it.copy(
                hasToken = active.hasSecret,
                leaseState = active.leaseState,
                projectors = projectorsAfter(outcome, it.projectors),
                // Success and no-op outcomes keep any visible command error:
                // a heartbeat landing mid-banner must not erase what the
                // operator is reading.
                error = message ?: it.error,
            )
        }
        if (outcome is LeaseOutcome.Active && foreground) {
            if (heartbeatJob?.isActive != true) {
                heartbeatJob = viewModelScope.launch {
                    var missed = 0
                    // `leaseRenewable` stops the loop once the server has stated
                    // an ownership answer elsewhere (a command's `409`, a
                    // release): renewal would answer `Suppressed` forever.
                    while (isActive && foreground && active.leaseRenewable) {
                        delay(active.heartbeatIntervalSeconds * 1_000L)
                        // `renewLease`, not `heartbeatLease`: a tolerated blip
                        // has already moved ownership to UNAVAILABLE, and the
                        // one-shot guard would never reach the server again.
                        val heartbeat = active.renewLease()
                        when (heartbeatStep(heartbeat, missed)) {
                            HeartbeatStep.CONFIRMED -> {
                                // Recovered from a tolerated blip: republish
                                // ownership or the panel stays fail-closed on a
                                // lease that is demonstrably alive again.
                                if (missed > 0) {
                                    syncLeaseState()
                                }
                                missed = 0
                                // The confirmed renewal is also the readout's
                                // refresh; a reading is only as fresh as the
                                // last heartbeat that carried it.
                                _state.update { it.copy(projectors = projectorsAfter(heartbeat, it.projectors)) }
                            }
                            // Only a real missed renewal spends the budget. One
                            // suppressed by an in-flight command must not, or a
                            // burst of commands leaves no tolerance at all.
                            HeartbeatStep.RETRY -> if (heartbeat is LeaseOutcome.Unavailable) {
                                missed += 1
                                // Fail closed while retrying — but not through
                                // applyLeaseOutcome, which cancels this ticker.
                                syncLeaseState()
                            }
                            HeartbeatStep.TERMINATE -> {
                                heartbeatJob = null
                                applyLeaseOutcome(heartbeat)
                                break
                            }
                        }
                    }
                }
            }
        } else if (outcome is LeaseOutcome.Active) {
            viewModelScope.launch { applyLeaseOutcome(active.releaseLease()) }
        } else {
            heartbeatJob?.cancel()
            heartbeatJob = null
        }
    }

    private fun CommandOutcome.validatesCredential(): Boolean =
        this is CommandOutcome.Confirmed ||
            this is CommandOutcome.NoCeremony ||
            this is CommandOutcome.StateUnusable

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
                // A nudge can carry movement this remote did not cause — another
                // panel starting over, or a controller that took the scope. The
                // projector closes its overlay on that just the same, so the
                // label has to follow it here too, not only on our own commands.
                mediaShown = mediaShownAfter(active.projection, current.mediaShown),
            )
        }
    }

    /**
     * Whether a media overlay is still believed to be up after [projection].
     *
     * The remote holds this locally because a cue persists nothing — there is no
     * authoritative "is the photo up" to read. What keeps it honest is that the
     * projector closes its overlay on exactly one signal, a changed
     * [ceremonySignature], so the label is cleared on that and on nothing else.
     *
     * Clearing it on *every* applied projection instead would be wrong in the
     * common case: an unchanged reload, or a refresh that returns identical
     * state, leaves the photograph on the projector while the button flips back
     * to "Show team media" — and the operator's next press would re-show it
     * rather than take it down.
     */
    private fun mediaShownAfter(projection: RevealProjection?, current: Boolean): Boolean {
        val signature = ceremonySignature(projection)
        val moved = lastSignature != null && signature != lastSignature
        lastSignature = signature
        return if (moved) false else current
    }

    override fun onCleared() {
        streamJob?.cancel()
        heartbeatJob?.cancel()
        super.onCleared()
    }
}
