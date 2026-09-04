//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package org.noca.animator.remote.core

import kotlin.system.exitProcess

// The contract runner.
//
// The checks are framework-free plain functions (see WireContractChecks.kt and
// LockContractChecks.kt) with this `main()` runner, so the exact same sources
// compile and run two ways:
//
//   * under `kotlinc` in a container on a machine with no Android SDK — how they
//     are verified during development and from `uv run pytest`
//     (tests/animator/test_remote_core_kotlin.py), mirroring the way
//     tests/animator/test_ceremony_js.py wraps the Node contract tests for
//     `control.js`;
//   * under Gradle's test task, via the thin JUnit wrapper in CoreContractTest.kt.

private class Failure(val name: String, val error: Throwable)

/** Runs every contract check and returns a description of each failure. */
fun runCoreContract(): List<String> {
    // `() -> Any?` rather than `() -> Unit`: the suspend checks are expression
    // bodies over `runBlocking`, whose value is the block's, and it is discarded.
    val checks: List<Pair<String, () -> Any?>> = listOf(
        // wire contract
        "projection fixture parses" to ::projectionFixtureParses,
        "meta fixture parses" to ::metaFixtureParses,
        "controller lease fixture parses" to ::controllerLeaseFixtureParses,
        "meta without has_started decodes as started" to ::metaWithoutHasStartedDecodesAsStarted,
        "unknown fields are ignored" to ::unknownFieldsAreIgnored,
        "teamLabel prefers the full name" to ::teamLabelPrefersFullname,
        "error detail parses both shapes" to ::errorDetailParsesBothShapes,
        "error detail survives hostile bodies" to ::errorDetailSurvivesHostileBodies,
        "urls are trailing-slash safe" to ::urlsAreTrailingSlashSafe,
        "unsafe path segments are rejected" to ::unsafePathSegmentsAreRejected,
        "control visibility truth table" to ::controlVisibilityTruthTable,
        "projector label matches the web panel" to ::projectorLabelMatchesTheWebPanel,
        "ceremony signature matches the projector" to ::ceremonySignatureMatchesTheProjector,
        "bodiless commands send no body" to ::bodilessCommandsSendNoBody,
        "media cues are bodiless and keyless" to ::mediaCuesAreBodilessAndKeyless,
        "media cues never lock and hide survives the lock" to ::mediaCuesNeverLockAndHideSurvivesTheLock,
        "media cues do not become the retryable attempt" to ::mediaCuesDoNotBecomeTheRetryableAttempt,
        "start and jump body shapes" to ::startAndJumpBodyShapes,
        "a fresh key per attempt" to ::freshKeyPerAttempt,
        "controller header covers every mutation" to ::controllerHeaderCoversEveryMutation,
        "state GET is controller independent" to ::stateGetIsControllerIndependent,
        "lease operations use dedicated endpoints" to ::leaseOperationsUseDedicatedEndpoints,
        // safety contract
        "stated refusals do not lock" to ::statedRefusalsDoNotLock,
        "mutation 409 loses lease without ambiguity" to ::mutationConflictLosesLeaseWithoutAmbiguity,
        "403 forgets the credential" to ::forbiddenForgetsCredential,
        "403 on state load forgets the credential" to ::forbiddenOnStateLoadForgetsCredential,
        "claim conflict is read only" to ::claimConflictIsReadOnly,
        "heartbeat conflict loses lease" to ::heartbeatConflictLosesLease,
        "lease unavailable fails closed" to ::leaseUnavailableFailsClosed,
        "takeover is explicit and restores authority" to ::takeoverRestoresAuthority,
        "release suppresses later mutations" to ::releaseSuppressesLaterMutations,
        "heartbeat policy tolerates blips and suppression" to ::heartbeatPolicyToleratesBlipsAndSuppression,
        "renewal retries reach the server after a blip" to ::renewalRetriesReachTheServerAfterABlip,
        "ambiguous outcomes lock and suppress" to ::ambiguousOutcomesLock,
        "transport failure locks" to ::transportFailureLocks,
        "unreadable success locks" to ::unreadableSuccessLocks,
        "explicit reload clears the lock" to ::explicitReloadClearsTheLock,
        "failed reload keeps the lock" to ::failedReloadKeepsTheLock,
        "nudge refresh never clears the lock" to ::nudgeRefreshNeverClearsTheLock,
        "nudge failure leaves state alone" to ::nudgeFailureLeavesStateAlone,
        "retry reuses the original key" to ::retryReusesTheOriginalKey,
        "refused retry keeps the lock" to ::retryRefusedKeepsTheLock,
        "retry without an attempt does nothing" to ::retryWithoutAnAttemptDoesNothing,
        "state 404 means no ceremony" to ::stateNotFoundMeansNoCeremony,
        "unusable stored state is recoverable" to ::unusableStoredStateIsRecoverable,
        "other state failure hides controls without locking" to ::otherStateFailureHidesControlsWithoutLocking,
        "sequence stops at first failure" to ::sequenceStopsAtFirstFailure,
        "sequence stops on ambiguity" to ::sequenceStopsOnAmbiguityWithoutConsumingSteps,
        "zero-length sequence sends nothing" to ::zeroLengthSequenceSendsNothing,
    )

    val failures = mutableListOf<Failure>()
    for ((name, check) in checks) {
        try {
            check()
            println("  ok    $name")
        } catch (error: Throwable) {
            failures += Failure(name, error)
            println("  FAIL  $name")
            println("        ${error::class.simpleName}: ${error.message}")
        }
    }
    println("${checks.size - failures.size}/${checks.size} checks passed")
    return failures.map { "${it.name}: ${it.error.message}" }
}

/** Entry point for the container run. */
fun main() {
    if (runCoreContract().isNotEmpty()) {
        exitProcess(1)
    }
}
