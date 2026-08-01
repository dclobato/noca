//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import org.noca.animator.remote.core.RevealPhase
import org.noca.animator.remote.ui.AnimatorRemoteTheme
import org.noca.animator.remote.ui.RemoteScreen
import org.noca.animator.remote.ui.RemoteViewModel
import org.noca.animator.remote.ui.SetupScreen

/** The single activity hosting the remote. */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            AnimatorRemoteTheme {
                val viewModel: RemoteViewModel = viewModel()
                val state by viewModel.state.collectAsStateWithLifecycle()

                // A ceremony can sit on one team for minutes while an announcer
                // talks. The screen locking mid-reveal would cost the operator a
                // fumbled unlock in front of an audience.
                KeepScreenOn(state.projection?.phase == RevealPhase.REVEALING)

                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    when {
                        !state.settingsLoaded -> LoadingIndicator()

                        // A stored token is being restored. Showing the setup form
                        // here would present an empty token field and invite the
                        // operator to retype a credential they had already saved.
                        state.restoringSession -> LoadingIndicator("Reconnecting…")

                        !state.settings.isComplete || !state.hasToken ->
                            SetupScreen(state = state, viewModel = viewModel)

                        else -> RemoteScreen(state = state, viewModel = viewModel)
                    }
                }
            }
        }
    }
}

@Composable
private fun LoadingIndicator(label: String? = null) {
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            CircularProgressIndicator()
            if (label != null) {
                Text(text = label, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/** Holds the screen awake while [enabled]. */
@Composable
private fun KeepScreenOn(enabled: Boolean) {
    val view = LocalView.current
    DisposableEffect(enabled) {
        view.keepScreenOn = enabled
        onDispose { view.keepScreenOn = false }
    }
}
