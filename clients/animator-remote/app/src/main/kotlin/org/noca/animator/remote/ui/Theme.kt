//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * A committed dark theme.
 *
 * The remote is held by an operator standing beside a projector in a darkened
 * auditorium. Following the system theme would risk a full-brightness white
 * screen at the worst possible moment, so the app does not offer the choice.
 * Green is reserved for the one control that advances the ceremony; amber and red
 * are reserved for the ambiguous-outcome and refusal states, so colour alone
 * never has to carry a safety-critical distinction.
 */
private val RemoteColors = darkColorScheme(
    primary = Color(0xFF4ADE80),
    onPrimary = Color(0xFF04310F),
    primaryContainer = Color(0xFF166534),
    onPrimaryContainer = Color(0xFFDCFCE7),
    secondary = Color(0xFF94A3B8),
    onSecondary = Color(0xFF0F172A),
    secondaryContainer = Color(0xFF1E293B),
    onSecondaryContainer = Color(0xFFE2E8F0),
    tertiary = Color(0xFFFBBF24),
    onTertiary = Color(0xFF2A1B00),
    background = Color(0xFF101418),
    onBackground = Color(0xFFE7EDF4),
    surface = Color(0xFF161B22),
    onSurface = Color(0xFFE7EDF4),
    surfaceVariant = Color(0xFF1F2630),
    onSurfaceVariant = Color(0xFFB6C2D2),
    error = Color(0xFFF87171),
    onError = Color(0xFF3A0A0A),
    errorContainer = Color(0xFF7F1D1D),
    onErrorContainer = Color(0xFFFEE2E2),
    outline = Color(0xFF3A4553),
)

/** Applies the remote's colour scheme. */
@Composable
fun AnimatorRemoteTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = RemoteColors, content = content)
}
