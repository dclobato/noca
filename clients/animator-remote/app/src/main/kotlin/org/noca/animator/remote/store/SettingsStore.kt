//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.store

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import org.noca.animator.remote.core.GLOBAL_SCOPE

/** The animator this remote points at by default. */
const val DEFAULT_BASE_URL: String = "https://animator.onlinejudge.com.br/"

/**
 * Non-secret connection settings.
 *
 * The operator token is deliberately absent: it lives in [TokenVault], encrypted.
 * Keeping the two apart means a future addition to this record cannot
 * accidentally serialize a credential into plaintext preferences.
 *
 * @property baseUrl Animator base URL.
 * @property slug Contest slug. Typed by the operator, because the animator has no
 *   contest-enumeration endpoint by design — an unknown slug and a disabled
 *   contest answer the same bare `404`, so there is nothing to discover.
 * @property scope `global`, or a site id from the public `/meta` feed.
 */
data class RemoteSettings(
    val baseUrl: String = DEFAULT_BASE_URL,
    val slug: String = "",
    val scope: String = GLOBAL_SCOPE,
) {
    /** Whether these settings name a contest at all. */
    val isComplete: Boolean get() = baseUrl.isNotBlank() && slug.isNotBlank()
}

private val Context.settingsDataStore: DataStore<Preferences> by
    preferencesDataStore(name = "animator-remote-settings")

/** Reads and writes the non-secret connection settings. */
class SettingsStore(context: Context) {

    private val store = context.applicationContext.settingsDataStore

    /** The current settings, re-emitted on every change. */
    val settings: Flow<RemoteSettings> = store.data.map { preferences ->
        RemoteSettings(
            baseUrl = preferences[BASE_URL] ?: DEFAULT_BASE_URL,
            slug = preferences[SLUG].orEmpty(),
            scope = preferences[SCOPE] ?: GLOBAL_SCOPE,
        )
    }

    /** Persists [settings]. */
    suspend fun save(settings: RemoteSettings) {
        store.edit { preferences ->
            preferences[BASE_URL] = settings.baseUrl.trim()
            preferences[SLUG] = settings.slug.trim()
            preferences[SCOPE] = settings.scope.trim().ifEmpty { GLOBAL_SCOPE }
        }
    }

    private companion object {
        val BASE_URL = stringPreferencesKey("base_url")
        val SLUG = stringPreferencesKey("slug")
        val SCOPE = stringPreferencesKey("scope")
    }
}
