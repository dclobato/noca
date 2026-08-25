//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import java.io.StringReader
import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// ---------------------------------------------------------------------------
// Version: taken from the uv workspace, never duplicated here
// ---------------------------------------------------------------------------
//
// The repository keeps exactly one version, in the root pyproject.toml, and every
// Python workspace member reads it through Hatchling's regex source. A second,
// hand-maintained version here would eventually disagree with the server this app
// drives, and "remote 1.0.0 against NOCA 15.0.0" is not a useful bug report from
// an operator. So read the same line with the same pattern the members use.
//
// `providers.fileContents` rather than `File.readText()`: it registers the file as
// a configuration input, so the configuration cache is invalidated on a version
// bump instead of quietly building an APK with the previous version.

private val WORKSPACE_VERSION_PATTERN = Regex("""(?m)^version = "([^"]+)"""")

val workspaceVersionFile = layout.projectDirectory.file("../../../pyproject.toml")

val workspaceVersion: String =
    providers.fileContents(workspaceVersionFile).asText.orNull
        ?.let { text -> WORKSPACE_VERSION_PATTERN.find(text)?.groupValues?.get(1) }
        // Escape hatch for building this Gradle project outside the monorepo,
        // where there is no workspace version to read.
        ?: providers.gradleProperty("animatorRemoteVersion").orNull
        ?: error(
            "Could not read the workspace version from ${workspaceVersionFile.asFile}. " +
                "Build from inside the NOCA repository, or pass -PanimatorRemoteVersion=<x.y.z>.",
        )

/**
 * Derives Android's monotonic integer version code from the workspace semver.
 *
 * Android refuses to install an APK whose `versionCode` is not greater than the
 * installed one, so this must increase with every release. Reserving two decimal
 * digits each for minor and patch keeps the ordering identical to semver ordering:
 * 15.0.0 becomes 150000, and 15.1.2 becomes 150102.
 *
 * Deliberately strict. A version this cannot parse (a `.dev0` or `rc1` suffix, or a
 * minor/patch past 99) fails the build with an explanation, rather than silently
 * shipping a version code that sorts wrongly and leaves users unable to update.
 */
fun androidVersionCode(version: String): Int {
    val parts = version.split('.')
    require(parts.size == 3) {
        "workspace version '$version' is not a three-part semver; " +
            "teach androidVersionCode how to order it before releasing"
    }
    val numbers = parts.map { part ->
        part.toIntOrNull() ?: error(
            "workspace version '$version' has the non-numeric component '$part'; " +
                "teach androidVersionCode how to order it before releasing",
        )
    }
    val (major, minor, patch) = numbers
    require(minor < 100 && patch < 100) {
        "workspace version '$version' exceeds two digits of minor or patch, which would " +
            "break version-code ordering; widen the scheme in androidVersionCode"
    }
    return major * 10_000 + minor * 100 + patch
}

// ---------------------------------------------------------------------------
// Release signing: env vars first, gitignored keystore.properties second
// ---------------------------------------------------------------------------
//
// The app is signed with a self-managed release key -- there is no Play App
// Signing safety net, so this keystore is the only thing that can ever produce an
// update to an installed copy. It therefore never lives in the repository, and
// the build reads it from one of two places:
//
//   1. Environment variables (NOCA_ANDROID_*), which is what the build container
//      and any CI runner use: the keystore is bind-mounted read-only and the
//      passwords never touch the working tree.
//   2. `keystore.properties` at the project root, gitignored, for a developer
//      building on the host who would rather not export four variables.
//
// Environment wins per value, so a file can hold the boring parts (path, alias)
// while a password stays in the environment.
//
// `providers.environmentVariable` / `providers.fileContents` rather than
// `System.getenv` / `File.readText`: the configuration cache is enabled in
// gradle.properties, and only the provider forms register as configuration
// inputs. The direct calls would be read once and then cached, so rotating a key
// would silently keep signing with the previous one.

private val KEYSTORE_PROPERTIES_FILE = "keystore.properties"

class ReleaseSigningMaterial(
    val storeFile: File,
    val storePassword: String,
    val keyAlias: String,
    val keyPassword: String,
)

val keystoreFileProperties: Map<String, String> =
    providers.fileContents(layout.projectDirectory.file("../$KEYSTORE_PROPERTIES_FILE"))
        .asText.orNull
        ?.let { text ->
            val parsed = Properties().apply { load(StringReader(text)) }
            parsed.stringPropertyNames().associateWith { name -> parsed.getProperty(name) }
        }
        ?: emptyMap()

/** Reads one signing value, preferring the environment over the properties file. */
fun signingValue(environmentName: String, propertyName: String): String? =
    providers.environmentVariable(environmentName).orNull?.takeIf(String::isNotBlank)
        ?: keystoreFileProperties[propertyName]?.takeIf(String::isNotBlank)

val signingInputs = mapOf(
    "NOCA_ANDROID_KEYSTORE / storeFile" to signingValue("NOCA_ANDROID_KEYSTORE", "storeFile"),
    "NOCA_ANDROID_KEYSTORE_PASSWORD / storePassword" to
        signingValue("NOCA_ANDROID_KEYSTORE_PASSWORD", "storePassword"),
    "NOCA_ANDROID_KEY_ALIAS / keyAlias" to signingValue("NOCA_ANDROID_KEY_ALIAS", "keyAlias"),
    "NOCA_ANDROID_KEY_PASSWORD / keyPassword" to
        signingValue("NOCA_ANDROID_KEY_PASSWORD", "keyPassword"),
)

// Partial credentials are a mistake, never an intention. Supplying three of four
// and getting an *unsigned* APK is the failure mode worth ruling out here: it is
// silent, and it is only discovered when the phone refuses to install the file.
val releaseSigning: ReleaseSigningMaterial? =
    if (signingInputs.values.all { it == null }) {
        null
    } else {
        val missing = signingInputs.filterValues { it == null }.keys
        require(missing.isEmpty()) {
            "release signing is partially configured; missing: ${missing.joinToString(", ")}"
        }
        val configuredStore = signingInputs.getValue("NOCA_ANDROID_KEYSTORE / storeFile")!!
        // A relative path resolves against the project root, so the same
        // `keystore.properties` works on the host and inside the build container.
        val storeFile = File(configuredStore).let { candidate ->
            if (candidate.isAbsolute) candidate else rootDir.resolve(configuredStore)
        }
        require(storeFile.isFile) {
            "release keystore '$storeFile' does not exist or is not a file"
        }
        ReleaseSigningMaterial(
            storeFile = storeFile,
            storePassword = signingInputs.getValue("NOCA_ANDROID_KEYSTORE_PASSWORD / storePassword")!!,
            keyAlias = signingInputs.getValue("NOCA_ANDROID_KEY_ALIAS / keyAlias")!!,
            keyPassword = signingInputs.getValue("NOCA_ANDROID_KEY_PASSWORD / keyPassword")!!,
        )
    }

android {
    namespace = "org.noca.animator.remote"
    compileSdk = 36

    // Pinned rather than left to AGP's default so the build container can install
    // exactly one build-tools package. An unpinned default that moves with an AGP
    // bump would make the image silently wrong: Gradle would try to download the
    // version it actually wants, at build time, into a read-only SDK.
    buildToolsVersion = "36.0.0"

    defaultConfig {
        applicationId = "org.noca.animator.remote"
        minSdk = 26
        targetSdk = 36
        versionCode = androidVersionCode(workspaceVersion)
        versionName = workspaceVersion
    }

    signingConfigs {
        releaseSigning?.let { material ->
            create("release") {
                storeFile = material.storeFile
                storePassword = material.storePassword
                keyAlias = material.keyAlias
                keyPassword = material.keyPassword
                // Which signature schemes are applied is left to AGP, which
                // derives them from minSdk. At minSdk 26 that is v2 alone: every
                // supported device verifies APK Signature Scheme v2, and the
                // legacy v1 JAR signature would only add size and a second way
                // to be wrong.
            }
        }
    }

    buildTypes {
        debug {
            // Kept distinct so a debug build can be installed beside a release
            // one, and so the cleartext exception in src/debug/AndroidManifest.xml
            // can never reach a release APK.
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            // Null when no credentials are configured, which leaves an unsigned
            // build. `requireReleaseSigning` below is what stops that reaching a
            // release task; the null here only keeps `configure`/`test` working
            // on a machine with no keystore at all.
            signingConfig = signingConfigs.findByName("release")
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    // Compile *to* Java 17 while running on whatever JDK Gradle was started with.
    //
    // A `jvmToolchain(17)` pin would be a hard requirement: Gradle would demand a
    // JDK 17 installation and fail with "No matching toolchains found" on a machine
    // that has only a newer one — which is the normal case, and is what the build
    // container provides (JDK 21). Targeting 17 without pinning the toolchain
    // builds identically on JDK 17, 21, and later.
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    sourceSets {
        getByName("main") { java.srcDirs("src/main/kotlin") }
        getByName("test") { java.srcDirs("src/test/kotlin") }
    }

    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}")
    }
}

// A release artifact that is not signed cannot be installed, and AGP reports that
// only as an `-unsigned` filename a script is free to ignore. Refuse the build
// instead, naming both ways to supply the key.
//
// Checked against the requested task names at configuration time rather than in a
// task action: an action defined in a build script captures the script object,
// which the configuration cache cannot serialize. The start parameter is part of
// the cache key, so this is re-evaluated whenever the requested tasks change --
// and it fails before any work is done rather than after the build has compiled.

val RELEASE_PACKAGING_TASKS = setOf("assembleRelease", "bundleRelease", "installRelease")

val releasePackagingRequested = gradle.startParameter.taskNames.any { requested ->
    RELEASE_PACKAGING_TASKS.any { name -> requested == name || requested.endsWith(":$name") }
}

if (releasePackagingRequested && releaseSigning == null) {
    error(
        "release signing is not configured. Set NOCA_ANDROID_KEYSTORE, " +
            "NOCA_ANDROID_KEYSTORE_PASSWORD, NOCA_ANDROID_KEY_ALIAS and " +
            "NOCA_ANDROID_KEY_PASSWORD, or create keystore.properties in the project " +
            "root. See README.md, 'Building a signed release APK'.",
    )
}

kotlin {
    // Must match `compileOptions` above, or the Kotlin plugin fails the build on
    // inconsistent JVM-target compatibility between the Java and Kotlin tasks.
    compilerOptions {
        jvmTarget = JvmTarget.JVM_17
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.datastore.preferences)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui.tooling.preview)
    debugImplementation(libs.androidx.compose.ui.tooling)

    implementation(libs.okhttp)
    implementation(libs.okhttp.sse)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)

    testImplementation(kotlin("test"))
}
