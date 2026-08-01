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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.text.KeyboardOptions
import org.noca.animator.remote.core.GLOBAL_SCOPE

/** The label shown for the contest-wide ceremony. */
private const val GLOBAL_LABEL = "Global ceremony"

/**
 * Connection and credential entry.
 *
 * The contest slug is typed rather than chosen: the animator has no
 * contest-enumeration endpoint by design — an unknown slug, a disabled contest,
 * and a disabled control kill-switch all answer the same bare `404` — so there is
 * genuinely nothing for the app to list.
 */
@Composable
fun SetupScreen(
    state: RemoteUiState,
    viewModel: RemoteViewModel,
) {
    var baseUrl by rememberSaveable(state.settingsLoaded) { mutableStateOf(state.settings.baseUrl) }
    var slug by rememberSaveable(state.settingsLoaded) { mutableStateOf(state.settings.slug) }
    var scope by rememberSaveable(state.settingsLoaded) { mutableStateOf(state.settings.scope) }
    var token by rememberSaveable { mutableStateOf("") }
    var scopeMenuOpen by remember { mutableStateOf(false) }

    val scopeLabel = if (scope == GLOBAL_SCOPE) {
        GLOBAL_LABEL
    } else {
        state.sites.firstOrNull { it.siteId == scope }?.name ?: scope
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            // Outside the scroll on purpose: the inset padding is fixed chrome, and
            // the content scrolls within it. safeDrawing also tracks the IME, so the
            // token field stays reachable once the keyboard is up.
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = "Animator Remote",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            text = "Reveal ceremony operator control",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Spacer(modifier = Modifier.height(4.dp))

        OutlinedTextField(
            value = baseUrl,
            onValueChange = { baseUrl = it },
            label = { Text("Animator URL") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
            modifier = Modifier.fillMaxWidth(),
        )

        OutlinedTextField(
            value = slug,
            onValueChange = { slug = it },
            label = { Text("Contest slug") },
            supportingText = { Text("As it appears in /c/<slug>/") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
            modifier = Modifier.fillMaxWidth(),
        )

        // The scope list comes from the public /meta feed, so the operator picks a
        // server-provided site id rather than typing one: start-reveal requires an
        // exact match with the token's own scope.
        Column {
            Text(
                text = "Ceremony",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(modifier = Modifier.height(4.dp))
            OutlinedButton(
                onClick = { scopeMenuOpen = true },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(scopeLabel)
            }
            DropdownMenu(expanded = scopeMenuOpen, onDismissRequest = { scopeMenuOpen = false }) {
                DropdownMenuItem(
                    text = { Text(GLOBAL_LABEL) },
                    onClick = {
                        scope = GLOBAL_SCOPE
                        scopeMenuOpen = false
                    },
                )
                for (site in state.sites) {
                    DropdownMenuItem(
                        text = { Text(site.name) },
                        onClick = {
                            scope = site.siteId
                            scopeMenuOpen = false
                        },
                    )
                }
            }
            if (state.sites.isEmpty()) {
                TextButton(
                    onClick = { viewModel.saveSettings(baseUrl, slug, scope) },
                    enabled = baseUrl.isNotBlank() && slug.isNotBlank(),
                ) {
                    Text("Load site list")
                }
            }
        }

        OutlinedTextField(
            value = token,
            onValueChange = { token = it },
            label = { Text("Operator token") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Password,
                imeAction = ImeAction.Done,
            ),
            modifier = Modifier.fillMaxWidth(),
        )

        Button(
            onClick = {
                val entered = token
                token = ""
                viewModel.connect(baseUrl = baseUrl, slug = slug, scope = scope, token = entered)
            },
            enabled = !state.inFlight && baseUrl.isNotBlank() && slug.isNotBlank() && token.isNotBlank(),
            modifier = Modifier
                .fillMaxWidth()
                .height(56.dp),
        ) {
            Text(if (state.inFlight) "Connecting…" else "Connect")
        }

        state.error?.let { message ->
            Text(
                text = message,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        // Distinguish "you have never entered a token" from "the one you saved
        // could not be read", which are the same empty field but not the same
        // situation.
        if (state.storedTokenUnreadable) {
            Text(
                text = "A saved token could not be read on this device, so it must be entered again.",
                color = MaterialTheme.colorScheme.tertiary,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        if (state.status.isNotBlank()) {
            Text(
                text = state.status,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        Spacer(modifier = Modifier.height(8.dp))

        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = "The token is stored encrypted on this device and sent only as a bearer " +
                    "credential over HTTPS.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        TextButton(onClick = { viewModel.forgetToken() }) {
            Text("Forget stored token")
        }
    }
}
