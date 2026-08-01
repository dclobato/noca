//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.store

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import android.util.Log
import java.nio.charset.StandardCharsets
import java.security.GeneralSecurityException
import java.security.KeyStore
import java.util.Base64
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Persists the operator token encrypted under a hardware-backed Keystore key.
 *
 * The web operator panel keeps its credential in memory only and makes you retype
 * it after every reload. That is the right trade-off for a shared desktop
 * browser; it is the wrong one for a phone held by an operator on stage, where
 * Android may kill a backgrounded app between two ceremony steps and a 64-hex
 * token cannot be retyped under stage lights. So the token is persisted — but
 * never in plaintext, and never anywhere it can be extracted without the device
 * unlocked.
 *
 * `androidx.security:security-crypto` would do the same job, but its deprecation
 * status varies across versions and this is the one security-critical store in
 * the app; a direct 60-line Keystore usage has no moving dependency underneath
 * it.
 *
 * The ciphertext lives in ordinary SharedPreferences, which is safe because the
 * AES key never leaves the Keystore. `allowBackup="false"` in the manifest keeps
 * even the ciphertext out of cloud and adb backups.
 */
class TokenVault(context: Context) {

    private val preferences =
        context.applicationContext.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    /** Whether a stored token is present. Says nothing about whether it is valid. */
    val hasStoredToken: Boolean
        get() = preferences.contains(TOKEN_KEY)

    /**
     * Encrypts and stores [token], replacing any previous one.
     *
     * @return `true` when the ciphertext was durably written.
     */
    fun save(token: String): Boolean {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, obtainKey())
        val ciphertext = cipher.doFinal(token.toByteArray(StandardCharsets.UTF_8))
        val encoder = Base64.getEncoder()
        val payload = "${encoder.encodeToString(cipher.iv)}:${encoder.encodeToString(ciphertext)}"
        return preferences.edit().putString(TOKEN_KEY, payload).commit()
    }

    /**
     * Returns the stored token, or `null` when there is none or it cannot be read.
     *
     * Failures are graded rather than treated alike, because deleting the stored
     * credential is itself destructive: an operator who loses it mid-ceremony has
     * to retype a long token under stage lights.
     *
     *  * A **permanently invalidated key** or a **failed authentication tag** can
     *    never decrypt this blob — the key is gone or the payload is corrupt — so
     *    the blob is dropped and the operator re-prompted.
     *  * A **malformed payload** likewise cannot improve, so it is dropped.
     *  * Any **other** security error is treated as possibly transient and the blob
     *    is **kept**. Returning `null` re-prompts for this launch without throwing
     *    away a credential that may well decrypt on the next one.
     *
     * Every failure is logged by exception type. The token itself is never logged.
     * Caller-visible detail matters here: this method returning `null` silently, in
     * every case, is precisely what made an earlier restore bug invisible.
     */
    fun load(): String? {
        val payload = preferences.getString(TOKEN_KEY, null) ?: return null
        return try {
            val parts = payload.split(':')
            require(parts.size == 2) { "malformed stored token payload" }
            val decoder = Base64.getDecoder()
            val iv = decoder.decode(parts[0])
            val ciphertext = decoder.decode(parts[1])
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, obtainKey(), GCMParameterSpec(GCM_TAG_BITS, iv))
            String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8)
        } catch (error: KeyPermanentlyInvalidatedException) {
            Log.w(TAG, "the Keystore key was invalidated; discarding the stored token")
            clear()
            null
        } catch (error: AEADBadTagException) {
            Log.w(TAG, "the stored token failed authentication; discarding it")
            clear()
            null
        } catch (error: IllegalArgumentException) {
            Log.w(TAG, "the stored token payload is malformed; discarding it")
            clear()
            null
        } catch (error: GeneralSecurityException) {
            // Kept on purpose: this may be transient, and the blob may decrypt later.
            Log.w(TAG, "could not read the stored token (${error::class.java.simpleName}); keeping it")
            null
        }
    }

    /** Removes the stored token. */
    fun clear() {
        preferences.edit().remove(TOKEN_KEY).commit()
    }

    /** Returns the Keystore key for this app, creating it on first use. */
    private fun obtainKey(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE_PROVIDER).apply { load(null) }
        (keyStore.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { entry ->
            return entry.secretKey
        }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE_PROVIDER)
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                // A fresh IV per encryption, enforced by the Keystore itself.
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return generator.generateKey()
    }

    private companion object {
        const val TAG = "AnimatorRemoteVault"
        const val PREFERENCES_NAME = "animator-remote-credential"
        const val TOKEN_KEY = "operator-token"
        const val KEYSTORE_PROVIDER = "AndroidKeyStore"
        const val KEY_ALIAS = "animator-remote-operator-token"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val GCM_TAG_BITS = 128
    }
}
