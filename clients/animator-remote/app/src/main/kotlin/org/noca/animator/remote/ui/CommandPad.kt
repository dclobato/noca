//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * The button pad.
 *
 * Two properties are deliberate. **Visibility comes from
 * [org.noca.animator.remote.core.controlsForState]** — the same single mapping the
 * web panel uses — so a control can never be reachable here while hidden there.
 * And **every button shares one `enabled` flag** derived from
 * [RemoteUiState.commandsEnabled], so an ambiguous outcome disables the whole pad
 * at once rather than leaving some path open.
 *
 * Step is visually dominant and the destructive actions are deliberately quiet:
 * this pad is operated in a dark room, at speed, by someone also watching a
 * projector.
 */
@Composable
internal fun CommandPad(
    state: RemoteUiState,
    onStep: () -> Unit,
    onBack: () -> Unit,
    onStepMany: () -> Unit,
    onBackMany: () -> Unit,
    onStart: () -> Unit,
    onStartOver: () -> Unit,
    onReset: () -> Unit,
) {
    val visibility = state.visibility
    val enabled = state.commandsEnabled

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (visibility.startVisible) {
            Button(
                onClick = onStart,
                enabled = enabled,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(64.dp),
            ) {
                Text("Start reveal", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            }
        }

        if (visibility.stepVisible || visibility.backVisible) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (visibility.backVisible) {
                    OutlinedButton(
                        onClick = onBack,
                        enabled = enabled,
                        modifier = Modifier
                            .weight(1f)
                            .height(76.dp),
                    ) {
                        Text("◀ BACK", fontSize = 16.sp)
                    }
                }
                if (visibility.stepVisible) {
                    Button(
                        onClick = onStep,
                        enabled = enabled,
                        modifier = Modifier
                            .weight(1.7f)
                            .height(76.dp),
                    ) {
                        Text("STEP ▶", fontSize = 22.sp, fontWeight = FontWeight.Bold)
                    }
                }
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (visibility.backVisible) {
                    OutlinedButton(
                        onClick = onBackMany,
                        enabled = enabled,
                        modifier = Modifier
                            .weight(1f)
                            .height(48.dp),
                    ) {
                        Text("◀◀ 10")
                    }
                }
                if (visibility.stepVisible) {
                    OutlinedButton(
                        onClick = onStepMany,
                        enabled = enabled,
                        modifier = Modifier
                            .weight(1f)
                            .height(48.dp),
                    ) {
                        Text("10 ▶▶")
                    }
                }
            }
        }

        if (visibility.resetVisible || visibility.startOverVisible) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (visibility.resetVisible) {
                    OutlinedButton(
                        onClick = onReset,
                        enabled = enabled,
                        colors = ButtonDefaults.outlinedButtonColors(
                            contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                        modifier = Modifier.weight(1f),
                    ) {
                        Text("Reset", fontSize = 13.sp)
                    }
                }
                if (visibility.startOverVisible) {
                    OutlinedButton(
                        onClick = onStartOver,
                        enabled = enabled,
                        colors = ButtonDefaults.outlinedButtonColors(
                            contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                        modifier = Modifier.weight(1f),
                    ) {
                        Text("Start over", fontSize = 13.sp)
                    }
                }
            }
        }
    }
}
