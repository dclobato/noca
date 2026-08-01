//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

internal val GOLD = Color(0xFFFBBF24)
internal val SILVER = Color(0xFFCBD5E1)
internal val BRONZE = Color(0xFFD08A5A)

/** A small filled circle, used for medal bands and the stream indicator. */
@Composable
internal fun Dot(color: Color, size: Dp = 10.dp) {
    Spacer(
        modifier = Modifier
            .size(size)
            .background(color = color, shape = CircleShape),
    )
}

/** A one-line message card. */
@Composable
internal fun MessageBanner(text: String, container: Color, onContainer: Color) {
    Card(colors = CardDefaults.cardColors(containerColor = container)) {
        Text(
            text = text,
            modifier = Modifier.padding(10.dp),
            style = MaterialTheme.typography.bodySmall,
            color = onContainer,
        )
    }
}

/** The medal band colour for a projection's `medal` value, if any. */
internal fun medalColor(medal: String?): Color? = when (medal) {
    "gold" -> GOLD
    "silver" -> SILVER
    "bronze" -> BRONZE
    else -> null
}
