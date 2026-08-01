//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.noca.animator.remote.core.RevealProjection
import org.noca.animator.remote.core.TeamRevealView
import org.noca.animator.remote.core.teamLabel

/**
 * The standings, in the projection's own ranking order.
 *
 * Rows are tappable only while `jump-team` is actually available, so the gesture
 * cannot be offered in a phase where the server would refuse it.
 */
@Composable
internal fun TeamList(
    projection: RevealProjection?,
    jumpEnabled: Boolean,
    onJump: (TeamRevealView) -> Unit,
    modifier: Modifier = Modifier,
) {
    val teams = projection?.teams.orEmpty()
    if (teams.isEmpty()) {
        Spacer(modifier = modifier)
        return
    }

    Column(modifier = modifier) {
        Text(
            text = if (jumpEnabled) "Standings — tap to jump" else "Standings",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 4.dp),
        )
        LazyColumn(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            items(teams, key = { it.teamId }) { team ->
                TeamRow(
                    team = team,
                    focused = team.teamId == projection?.focusedTeamId,
                    enabled = jumpEnabled,
                    onClick = { onJump(team) },
                )
            }
        }
    }
}

@Composable
private fun TeamRow(
    team: TeamRevealView,
    focused: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    val container = if (focused) {
        MaterialTheme.colorScheme.primaryContainer
    } else {
        MaterialTheme.colorScheme.surface
    }
    val onContainer = if (focused) {
        MaterialTheme.colorScheme.onPrimaryContainer
    } else {
        MaterialTheme.colorScheme.onSurface
    }

    Card(
        colors = CardDefaults.cardColors(containerColor = container),
        modifier = Modifier
            .fillMaxWidth()
            .then(if (enabled) Modifier.clickable(onClick = onClick) else Modifier),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "${team.currentRank}",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = onContainer,
                modifier = Modifier.width(28.dp),
            )
            medalColor(team.medal)?.let { color ->
                Dot(color = color)
                Spacer(modifier = Modifier.width(6.dp))
            }
            Text(
                // The full name, falling back to the login — the same rule the
                // projector uses, so operator and audience never disagree.
                text = teamLabel(team),
                style = MaterialTheme.typography.bodyMedium,
                color = onContainer,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Text(
                text = "${team.solved}",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = onContainer,
                modifier = Modifier.width(28.dp),
            )
            Text(
                text = "${team.penalty}",
                style = MaterialTheme.typography.bodySmall,
                color = if (focused) onContainer else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.width(48.dp),
            )
            if (focused) {
                Text(text = "◀", color = onContainer)
            }
        }
    }
}
