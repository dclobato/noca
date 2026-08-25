//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.noca.animator.remote.core.teamLabel
import org.noca.animator.remote.core.ControllerLeaseState

/** A destructive or positional action awaiting confirmation. */
internal sealed interface Confirm {
    data object StartOver : Confirm
    data object Rebuild : Confirm
    data object Reset : Confirm
    data object Takeover : Confirm
    data class Jump(val teamId: String, val label: String) : Confirm
}

/**
 * The operator's remote: status, command pad, and standings.
 *
 * The layout puts the ambiguous-outcome banner directly under the status header
 * and above every control, because while it is showing, nothing else the operator
 * could press matters.
 */
@Composable
fun RemoteScreen(
    state: RemoteUiState,
    viewModel: RemoteViewModel,
) {
    var confirm by remember { mutableStateOf<Confirm?>(null) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            // targetSdk 36 means Android draws this app edge-to-edge and does not
            // inset the window for the system bars, so the insets must be consumed
            // here or the bottom row lands underneath the navigation bar — where it
            // cannot be tapped at all. safeDrawingPadding covers the status bar, the
            // navigation bar, and the cutout, and is a no-op on the older API levels
            // where the system still insets the window itself.
            .safeDrawingPadding()
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        StatusHeader(state)

        when {
            state.blocked -> AmbiguousBanner(state = state, viewModel = viewModel)
            state.leaseState != ControllerLeaseState.ACTIVE -> LeaseBanner(
                state = state,
                onTakeover = { confirm = Confirm.Takeover },
                onRetry = viewModel::retryControllerLease,
            )
            state.error != null -> MessageBanner(
                text = state.error,
                container = MaterialTheme.colorScheme.errorContainer,
                onContainer = MaterialTheme.colorScheme.onErrorContainer,
            )
            state.status.isNotBlank() -> MessageBanner(
                text = state.status,
                container = MaterialTheme.colorScheme.surfaceVariant,
                onContainer = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        if (state.stateUnusable) {
            Button(
                onClick = { confirm = Confirm.Rebuild },
                enabled = !state.inFlight,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp),
            ) {
                Text("Rebuild state")
            }
        }

        CommandPad(
            state = state,
            onStep = viewModel::step,
            onBack = viewModel::back,
            onStepMany = { viewModel.stepMany(10) },
            onBackMany = { viewModel.backMany(10) },
            onJumpPending = viewModel::jumpPending,
            onStart = { viewModel.start(restart = false) },
            onStartOver = { confirm = Confirm.StartOver },
            onReset = { confirm = Confirm.Reset },
        )

        TeamList(
            projection = state.projection,
            jumpEnabled = state.visibility.jumpVisible && state.commandsEnabled,
            onJump = { team -> confirm = Confirm.Jump(team.teamId, teamLabel(team)) },
            modifier = Modifier.weight(1f),
        )

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedButton(
                onClick = viewModel::reloadState,
                enabled = !state.inFlight,
                modifier = Modifier.weight(1f),
            ) {
                Text("Reload state")
            }
            TextButton(onClick = viewModel::forgetToken) {
                Text("Disconnect")
            }
        }
    }

    confirm?.let { pending ->
        ConfirmDialog(
            pending = pending,
            state = state,
            onDismiss = { confirm = null },
            onConfirmed = {
                confirm = null
                when (pending) {
                    Confirm.StartOver, Confirm.Rebuild -> viewModel.start(restart = true)
                    Confirm.Reset -> viewModel.reset()
                    Confirm.Takeover -> viewModel.takeoverControllerLease()
                    is Confirm.Jump -> viewModel.jump(pending.teamId)
                }
            },
        )
    }
}

/** Shows command authority independently from the last readable projection. */
@Composable
private fun LeaseBanner(
    state: RemoteUiState,
    onTakeover: () -> Unit,
    onRetry: () -> Unit,
) {
    val text = when (state.leaseState) {
        ControllerLeaseState.READ_ONLY -> "Another controller is active for this ceremony."
        ControllerLeaseState.LOST -> "Controller lease lost. Control moved or expired."
        ControllerLeaseState.UNAVAILABLE -> "Controller lease unavailable. Commands remain disabled."
        ControllerLeaseState.UNCLAIMED -> "Controller authority has not been claimed."
        ControllerLeaseState.ACTIVE -> return
    }
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(text, style = MaterialTheme.typography.bodyMedium)
            Spacer(modifier = Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (state.leaseState == ControllerLeaseState.READ_ONLY ||
                    state.leaseState == ControllerLeaseState.LOST
                ) {
                    OutlinedButton(onClick = onTakeover, enabled = !state.inFlight) {
                        Text("Take over control…")
                    }
                }
                if (state.leaseState == ControllerLeaseState.UNAVAILABLE ||
                    state.leaseState == ControllerLeaseState.UNCLAIMED
                ) {
                    OutlinedButton(onClick = onRetry, enabled = !state.inFlight) {
                        Text("Retry lease")
                    }
                }
            }
        }
    }
}

@Composable
private fun StatusHeader(state: RemoteUiState) {
    val projection = state.projection
    val scopeName = projection?.siteName ?: "Global ceremony"
    val phase = projection?.phase ?: if (state.noCeremony) "no ceremony" else "—"

    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = scopeName,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                // The nudge stream is a convenience, never a correctness
                // requirement, so its absence is shown quietly rather than as an
                // error the operator has to act on.
                Spacer(modifier = Modifier.width(8.dp))
                Dot(
                    color = if (state.streaming) {
                        MaterialTheme.colorScheme.primary
                    } else {
                        MaterialTheme.colorScheme.outline
                    },
                )
            }

            Text(
                text = phase.uppercase(),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            if (projection != null) {
                Text(
                    text = "${projection.revealedCount} / ${projection.frozenCount} revealed",
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.Bold,
                )
                val nextCell = projection.nextCell
                if (nextCell != null) {
                    val team = projection.teams.firstOrNull { it.teamId == nextCell.teamId }
                    Text(
                        text = "Next: ${team?.let(::teamLabel) ?: nextCell.teamId} · problem ${nextCell.label}",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.primary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            } else if (state.contestName.isNotBlank()) {
                Text(
                    text = state.contestName,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/**
 * The recovery banner for an unknown command outcome.
 *
 * Two actions, and the distinction between them is the whole point. **Retry same
 * command** re-sends the identical attempt under its original `Idempotency-Key`,
 * which the server replays rather than applying twice — safe even if the original
 * did land. **Reload state** abandons the attempt and refetches the truth. Simply
 * pressing Step again is exactly what must not happen here, which is why the pad
 * stays disabled until one of these resolves it.
 */
@Composable
private fun AmbiguousBanner(state: RemoteUiState, viewModel: RemoteViewModel) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text = "Command outcome unknown",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
            Text(
                text = state.error
                    ?: "The command may or may not have been applied. Resolve before continuing.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
            Spacer(modifier = Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = viewModel::reloadState,
                    enabled = !state.inFlight,
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Reload state")
                }
                if (state.canRetry) {
                    OutlinedButton(
                        onClick = viewModel::retryLastAttempt,
                        enabled = !state.inFlight,
                        modifier = Modifier.weight(1f),
                    ) {
                        Text("Retry same command")
                    }
                }
            }
        }
    }
}

@Composable
private fun ConfirmDialog(
    pending: Confirm,
    state: RemoteUiState,
    onDismiss: () -> Unit,
    onConfirmed: () -> Unit,
) {
    val counts = state.projection?.let { "${it.revealedCount} / ${it.frozenCount} revealed" }

    val (title, message, action) = when (pending) {
        Confirm.StartOver -> Triple(
            "Start the ceremony over?",
            "This rebuilds the ranking from current contest data. Runs judged since the ceremony " +
                "began are included, so the reveal order may differ from what was already shown. " +
                counts.orEmpty(),
            "Discard and start over",
        )

        Confirm.Rebuild -> Triple(
            "Rebuild the reveal state?",
            "The stored state cannot be read. Rebuilding replaces it with a new frozen snapshot " +
                "from current contest data and starts the ceremony.",
            "Rebuild state",
        )

        Confirm.Reset -> Triple(
            "Reset the ceremony to idle?",
            "This clears the reveal progress but preserves the original frozen contest snapshot. " +
                "Start reveal must be selected again before the ceremony can continue.",
            "Reset to idle",
        )

        Confirm.Takeover -> Triple(
            "Take over ceremony control?",
            "The former panel immediately loses command authority. Its state view remains available.",
            "Take over control",
        )

        is Confirm.Jump -> Triple(
            "Jump to ${pending.label}?",
            "The ceremony's focus moves to this team.",
            "Jump",
        )
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(message) },
        confirmButton = { TextButton(onClick = onConfirmed) { Text(action) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
