//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.core

import kotlin.test.Test
import kotlin.test.assertTrue

/**
 * Runs the command-client contract under Gradle's test task.
 *
 * The checks themselves live in `CoreContract.kt` as framework-free functions so
 * that the identical file also runs under a bare `kotlinc` in a container, on a
 * machine with no Android SDK. This class is only the JUnit entry point.
 */
class CoreContractTest {

    @Test
    fun coreContractHolds() {
        val failures = runCoreContract()
        assertTrue(
            failures.isEmpty(),
            "command client contract failures:\n" + failures.joinToString("\n"),
        )
    }
}
