//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import org.jetbrains.kotlin.gradle.dsl.JvmTarget

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

android {
    namespace = "org.noca.animator.remote"
    compileSdk = 36

    defaultConfig {
        applicationId = "org.noca.animator.remote"
        minSdk = 26
        targetSdk = 36
        versionCode = androidVersionCode(workspaceVersion)
        versionName = workspaceVersion
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
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    // Compile *to* Java 17 while running on whatever JDK Gradle was started with.
    //
    // A `jvmToolchain(17)` pin would be a hard requirement: Gradle would demand a
    // JDK 17 installation and fail with "No matching toolchains found" on a machine
    // that has only a newer one — which is the normal case, since Android Studio
    // bundles a JDK 21 (JBR) and no 17. Targeting 17 without pinning the toolchain
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
