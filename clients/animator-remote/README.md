<!--
  NOCA -- Next Online Contest Administrator
  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
-->

# Animator Remote (Android)

A native Android remote control for the animator's post-freeze **reveal ceremony**.

The ceremony is normally driven from `GET /c/{slug}/control?scope=…` in a browser.
During a real award ceremony the operator is on stage, away from the machine
driving the projector, and a laptop browser is an awkward remote. This app is the
same control surface on a phone: enter the animator URL, the contest slug, the
ceremony scope, and an operator token, then drive `step`, `back`, jump-to-team,
and jump-to-pending from large buttons.

It talks to the control API, including its controller lease. The app and animator
server must be upgraded in lockstep: a server that requires controller ownership
rejects mutations from an older remote with `422`.

## What it does

- All seven control commands: `start-reveal`, `step`, `back`, `reset`,
  `jump-team`, `jump-pending`, and `state`
- Step 10 / Back 10, as bounded client-side sequences of single commands
- Jump to next pending, which stops before the next `?` without revealing it
- Status header: scope, phase, `revealed / frozen` counts, and the next cell
- Tappable standings list, rendered from the projection, for `jump-team`
- Live sync from the ceremony's public SSE nudge feed, with reconnect backoff
- The operator token stored encrypted under an Android Keystore key
- Screen kept awake while a ceremony is `revealing`
- One active controller lease per ceremony scope, with read-only fallback and
  explicit takeover

## Controller lease

After the server accepts the operator credential, the remote claims
`/c/{slug}/control/controller-lease/claim`. It generates a UUID controller id
for the running app process and sends it only in the
`X-Animator-Controller-Id` header. The id never enters a URL, log, setting, or
persistent storage.

While the app is foregrounded, it renews ownership at the interval returned by
the server. The current contract uses a 45-second lease and a 10-second heartbeat.
A renewal that collides with an in-flight command is simply retried next tick
and does not count against the blip budget; a transport or store blip is
tolerated twice inside the lease TTL, and each retry is a real round trip rather
than a short-circuit on the ownership the blip left unverified. The panel is
fail-closed while retrying — commands are disabled and the projection stays
visible — and re-enables itself as soon as a renewal succeeds, so a one-second
network hiccup costs no manual recovery. Only an exhausted budget reports
control unavailable. Backgrounding or disposing the controller stops heartbeats
and attempts a best-effort release; expiry remains authoritative if that request
doesn't arrive.

The remote exposes four ownership states:

- **Active:** ceremony mutations are enabled.
- **Read-only:** another controller owns the scope; state remains visible.
- **Lease lost:** ownership moved or expired; commands and heartbeats stop.
- **Unavailable:** ownership couldn't be verified; commands fail closed while
  the last projection remains visible.

The remote never takes over automatically. **Take over control…** appears only
when the remote is read-only or has lost its lease, and its confirmation warns
that the former panel immediately loses command authority. `GET /control/state`
remains lease-independent, so every state can still reconcile the ceremony.

## The safety rule this app is built around

The animator's `503` from the reveal store — and any `5xx`, and any dropped
connection — is **ambiguous**: it can arrive *after* the server's fenced save has
already committed. Pressing `step` again in that situation reveals two teams in
front of an audience.

So the remote copies the web panel's rule exactly. A refusal the server *stated*
(`400`, `403`, `404`, `409`, `422`) applied nothing, and the controls re-enable
immediately. Anything else locks every command — a locked client sends no request
at all — until the operator resolves it with one of two actions:

- **Reload state** — abandon the attempt and refetch the truth.
- **Retry same command** — re-send the identical attempt under its original
  `Idempotency-Key`. The server replays the most recent key instead of applying it
  again, returning the original projection with no save and no publish. This is
  the one provably safe retry, and the web panel cannot offer it because it
  discards the key.

An SSE nudge may refresh the *display* but can never release the lock: a nudge
says someone changed the ceremony, not that this operator's command applied.

## Prerequisites

Building this app needs **Docker** and nothing else. The toolchain — a JDK, the
Android SDK, and the build tools that sign an APK — lives in a container defined
by this project, so a NOCA checkout stays what it otherwise is: a Python uv
workspace with no Java on it.

- **Docker**, with a local daemon. See
  [Building with the build container](#building-with-the-build-container).
- A phone with **Android 8.0 (API 26)** or newer, to install the result on.

There is deliberately no documented host-toolchain or IDE path. One supported way
to build means the version pins in `gradle/libs.versions.toml` describe what
actually produced an APK, rather than what one machine happened to have
installed.

## Building with the build container

A NOCA checkout has no JDK and no Android SDK. Rather than requiring both on
every machine that might need to cut an APK, `tools/build-container/Dockerfile`
carries them, and `tools/build-apk.sh` drives it.

```bash
cd clients/animator-remote
tools/build-apk.sh --debug          # unsigned debug APK, no keystore needed
tools/build-apk.sh                  # signed release APK (see the next section)
```

The first invocation builds the image, which downloads roughly 1 GB of Android
SDK; after that a clean build is a couple of minutes and an incremental one is
seconds. Output lands in the normal place, `app/build/outputs/apk/<variant>/`,
owned by you rather than by root — the container runs as your uid:gid.

Other entry points:

| Command | Does |
| --- | --- |
| `tools/build-apk.sh --debug` | `:app:assembleDebug` |
| `tools/build-apk.sh` | `:app:assembleRelease`, refusing to run unsigned |
| `tools/build-apk.sh -- :app:test` | any Gradle task, passed through verbatim |
| `tools/build-apk.sh --shell` | interactive shell in the toolchain |
| `tools/build-apk.sh --rebuild-image` | rebuild the image before building |

### What the image is, and is not

It is a **developer** image: `noca/animator-remote-build`, JDK 21 plus
`platforms;android-36`, `build-tools;36.0.0`, `platform-tools`, and pre-accepted
licences. It is deliberately absent from `containers/build.sh`,
`containers/docker-bake.hcl`, and the published image set — nothing in a NOCA
deployment runs it, it has no runtime contract with any module, and it can be
deleted at any time (`docker rmi noca/animator-remote-build`).

Two version facts are load-bearing and will bite on the next upgrade:

- **JDK 21, not the judge images' JDK 25.** Gradle 8.14.5 supports up to Java 24;
  JDK 25 needs Gradle 9.1. The Kotlin and Java judge containers therefore cannot
  build this app, despite carrying a Kotlin toolchain — and they hold no Android
  SDK either. `tools/run-core-tests.sh` legitimately uses the Kotlin judge image
  because the contract tests are plain JVM Kotlin with no Android in them.
- **`buildToolsVersion` is pinned** in `app/build.gradle.kts` to the version the
  image installs. Left at AGP's default it would move on an AGP bump, and the
  build would then try to download build-tools at build time into an SDK the
  build user cannot write.

### Caches, and cleaning up

Gradle's dependency cache lives in the named volume
`noca-animator-remote-gradle`, so it survives between builds and is not scattered
through your home directory. Reclaim it with:

```bash
docker volume rm noca-animator-remote-gradle    # ~1 GB of Maven artifacts
docker rmi noca/animator-remote-build           # ~2.5 GB of toolchain
```

The script needs a **local** Docker daemon, because it bind-mounts the working
tree. This is the one place it diverges from `tools/run-core-tests.sh`, which
copies sources in so it survives a remote or sibling daemon under Gitea Actions.
A Gradle build reads the whole project, writes hundreds of megabytes of
intermediates, and is only tolerable because its cache persists — none of which
survives a copy-in/copy-out round trip.

The repository root is mounted at its own host path rather than at `/work`,
because `app/build.gradle.kts` reads the version from `../../../pyproject.toml`.
Mounting only the client would silently fall back to requiring
`-PanimatorRemoteVersion`.

## Building a signed release APK

The app is signed with a **self-managed release key**. There is no Play App
Signing safety net behind it, which makes one consequence worth stating plainly
before anything else: *if this keystore is lost, no future build can ever update
an installed copy of the app.* Every operator would have to uninstall — losing
their stored token — and reinstall. Back it up somewhere that survives the
machine.

### 1. Create the keystore, once

Run `keytool` in the build container, which already has the JDK:

```bash
tools/build-apk.sh --shell
keytool -genkeypair -v \
    -keystore ~/noca-animator-remote.keystore -storetype PKCS12 \
    -alias animator-remote -keyalg RSA -keysize 4096 -validity 10000 \
    -dname "CN=NOCA, O=NOCA, C=BR"
```

`-validity 10000` (about 27 years) is the conventional value: an expired
certificate cannot sign an update. Keep the keystore **outside** the repository —
`.gitignore` covers `*.keystore`, `*.jks`, and `keystore.properties`, but a file
that is never in the tree cannot be committed by an ignore rule that someone
edits later.

### 2. Supply it to the build

Two sources, checked per value, environment first:

```bash
export NOCA_ANDROID_KEYSTORE=~/noca-animator-remote.keystore
export NOCA_ANDROID_KEYSTORE_PASSWORD=...
export NOCA_ANDROID_KEY_ALIAS=animator-remote
export NOCA_ANDROID_KEY_PASSWORD=...
tools/build-apk.sh
```

The script bind-mounts the keystore **read-only** at a path outside the working
tree and passes the passwords as container environment variables rather than as
`docker run` arguments, which would be visible in `ps` to every user on the host
for the length of the build.

Or put them in `clients/animator-remote/keystore.properties` (gitignored), which
saves exporting four variables in every shell:

```properties
storeFile=/home/you/noca-animator-remote.keystore
storePassword=...
keyAlias=animator-remote
keyPassword=...
```

`storeFile` may point anywhere: the script reads it, mounts that keystore into
the container read-only, and overrides only that one value, so the keystore stays
outside the repository while the other three values come from the file. A
relative path resolves against the project root instead. Environment wins per
value, so the file can hold the boring parts while a password stays in the
environment.

Partial credentials — three of the four — **fail the build**. The alternative is
an APK that is silently unsigned, which is only discovered when a phone refuses
to install it. For the same reason, `assembleRelease` with no credentials at all
fails at configuration time rather than producing `app-release-unsigned.apk`.

### 3. Verify before distributing

Check what actually signed the file before it leaves your machine, using the same
container that produced it:

```bash
docker run --rm -v "$PWD:$PWD" -w "$PWD" noca/animator-remote-build \
    apksigner verify --verbose --print-certs \
    app/build/outputs/apk/release/app-release.apk
```

Expect `Verified using v2 scheme (APK Signature Scheme v2): true` and your own
certificate DN. Without `--verbose`, `apksigner` prints the certificate but not
the scheme lines. v1 (JAR signing) is correctly **false**: at `minSdk 26` every
supported device verifies v2, so AGP omits the legacy signature.

### About Play Store publishing

Play has not accepted APKs for new applications since August 2021 — it requires
an **Android App Bundle**, and an AAB requires enrolling in Play App Signing,
where Google holds the app signing key and you hold an upload key. That is a
different signing model from the self-managed key above.

Nothing here blocks that move: `:app:bundleRelease` already picks up the same
`signingConfig` and the same guard, so it works the day you decide to enrol. What
changes is the meaning of the keystore — it becomes an *upload* key, recoverable
through Play support rather than irreplaceable — and the artifact you upload.
Until then, `assembleRelease` produces the APK you hand to operators directly.

## Server prerequisites

The control API is gated three times, and the first two gates answer the same bare
`404` an unknown slug does — deliberately, so neither can be used to probe the
others. If the app reports "not found", check both:

1. `NOCA_ANIMATOR_ENABLE_CONTROL=true` in the animator's environment. It defaults
   to **false**.
2. The contest's `animator_enabled` column is `true`.

Then the operator token must match the ceremony scope: a site token authorizes
exactly its own site, and a global token (`site_secrets.site_id IS NULL`) exactly
the global ceremony. `start-reveal` requires the scope to match the token's own
scope exactly, so picking the wrong entry in the **Ceremony** dropdown is a `403`.

## Using it

1. **Animator URL** — e.g. `https://animator.onlinejudge.com.br/` (the default).
2. **Contest slug** — as it appears in `/c/<slug>/`. It must be typed: the
   animator has no contest-enumeration endpoint by design, so there is nothing to
   list.
3. **Ceremony** — `Global ceremony` or a site, loaded from the public `/meta`
   feed. Tap **Load site list** if the dropdown is empty.
4. **Operator token** — stored encrypted after the server accepts it. **Disconnect**
   forgets it.

`Reset` and `Start over` are destructive and always confirm first. `Start over`
rebuilds the ranking from *current* contest data, so runs judged since the
ceremony began are included and the reveal order may differ from what the
audience has already seen.

## Tests

The safety-critical logic lives in `app/src/main/kotlin/.../core/`, which imports
no Android and no OkHttp: HTTP is an injected function type. That is what makes it
verifiable without a device.

Two ways to run them, both container-based:

```bash
tools/build-apk.sh -- :app:testDebugUnitTest   # the full Gradle test task
tools/run-core-tests.sh                        # the core contract checks alone
```

The second reuses NOCA's own `noca-judge-kotlin` compile image (JDK + `kotlinc` + the
kotlinx-serialization plugin) and fetches only the serialization and coroutines
runtime jars. Both paths run the same checks, from the same file.

It uses the rolling `:compile` tag deliberately: a version-pinned tag ages into a
permanent skip once it is no longer the image a checkout actually has. Set
`NOCA_KOTLIN_IMAGE` to reproduce against a specific build.

A missing image is pulled rather than treated as an absent toolchain — skipping
is the right answer only when the environment genuinely cannot run this, not when
the image is one `docker pull` away. The pull happens only when the image is
absent, so a developer who already has it stays offline-capable and pays nothing;
`NOCA_KOTLIN_NO_PULL` disables it.

Sources and jars are copied into the container with `docker cp` rather than
bind-mounted, so the script is correct wherever the Docker daemon is not the
machine running it. Under Gitea Actions the job is itself a container holding the
*host* daemon's socket, with the checkout in a Docker volume: a `-v` there is
resolved against the host filesystem, where the path does not exist, so Docker
mounts a freshly created empty directory and the build fails with "no source
files" while every path looks right from outside. Copying also makes the
read-only guarantee absolute — the container never sees the working tree.

Both are wired into the Python suite:

- `tests/animator/test_remote_core_kotlin.py` runs the script above and **skips**
  when Docker or the image is absent, mirroring how
  `tests/animator/test_ceremony_js.py` wraps the Node tests for `control.js`.
  That leniency is switched off where the coverage is load-bearing: under `CI`,
  or wherever `NOCA_KOTLIN_REQUIRED` is set, every reason it would have skipped
  becomes a failure instead. A skip is indistinguishable from a pass, and this
  test spent a release cycle skipping on a stale image pin without saying so.
  `NOCA_KOTLIN_REQUIRED` wins in both directions whenever it is present at all,
  so a CI environment that truly cannot run Docker opts out deliberately —
  setting it to an empty value, `0`, `false`, or `no` — rather than by accident.
- `tests/animator/test_remote_client_contract.py` needs no Kotlin toolchain and
  **never skips**. It validates the shared JSON fixtures against the animator's own
  Pydantic models and checks that the Kotlin models name every field those models
  serialize — so a server change that would break this app fails `uv run pytest`.

If that contract test fails after a server-side change, regenerate the fixtures in
`app/src/test/resources/fixtures/` from the current models and extend
`core/ApiModels.kt` to match.

## Security notes

- The token is sent **only** as an `Authorization: Bearer` header. It is never put
  in a URL, a log, the settings store, or a query string.
- At rest it is encrypted with AES/GCM under a key generated in the Android
  Keystore, which never leaves the Keystore. An unreadable blob (device credential
  removed, data restored to another device) is discarded and the operator is
  re-prompted.
- `allowBackup="false"`: even the ciphertext stays out of cloud and adb backups.
- **Cleartext HTTP is permitted in every build, release included**
  (`usesCleartextTraffic="true"` in `src/main/AndroidManifest.xml`). A contest
  venue commonly runs the animator as `http://<lan-ip>:8003`, and enforcing TLS
  would make the shipped remote unusable exactly where it is needed. The cost is
  real and worth restating: the token is a bearer credential, taking over a live
  ceremony is a supported operation, so anyone observing the venue network can
  capture the token and seize the reveal. Use an HTTPS animator wherever one
  exists, and treat a token used over cleartext as disclosed once the contest
  ends. Android has no middle setting here — the flag is per app. Narrowing it
  means a `res/xml/network_security_config.xml` naming each permitted host,
  which must know the venue address at build time.
- OkHttp's automatic `retryOnConnectionFailure` is **disabled**: a silent re-send
  of a failed `POST` is exactly the event this app must surface rather than hide.
- The SSE nudge feed is credential-free and is subscribed to without the token.
- The controller id is an in-memory UUID sent only as
  `X-Animator-Controller-Id`. It is not a credential and is never persisted.

## Distributing the APK

Build it as above, then hand `app/build/outputs/apk/release/app-release.apk` to
each operator over USB (`adb install -r <file>`) or any file transfer; the phone
asks them to allow installation from that source.

Four things to know before handing the file out:

- **The APK talks cleartext HTTP if asked to**, in release as much as in debug,
  so it works against a venue animator with no TLS. That also means an operator
  token travels unencrypted on such a link — see [Security notes](#security-notes)
  before handing the file to people who will use it on untrusted wifi.
- **A release APK is a different app from a debug one.** Debug carries
  `applicationIdSuffix = ".debug"`, so the two install side by side and do not
  share settings or the stored token.
- **Nothing secret is in the APK.** No token is embedded and the default base URL
  is not a credential, so possession of the file grants no ceremony access. Each
  operator enters their own token, encrypted per device. Distribute the token
  separately from the app.
- **Signing key changes are destructive.** An APK signed with a different key
  than the installed one cannot update it: every operator must uninstall first,
  which discards their stored token. This is the practical reason the keystore
  matters more than the APK does.

## Versions

**The app version is not maintained here.** `versionName` is read from the `version`
line of the repository's root `pyproject.toml` — the same single source of truth the
eight Python workspace members read through Hatchling's regex source — and
`versionCode` is derived from it as `major * 10000 + minor * 100 + patch`
(15.0.0 becomes 150000), which keeps Android's required monotonic ordering
identical to semver ordering.

So `/bump-version <x.y.z>` covers this client automatically: it edits the root
`pyproject.toml`, and the next APK build picks the new version up. There is no
Android-specific bump step, and nothing to keep in sync by hand.

Consequences worth knowing:

- A non-semver version (`15.1.0rc1`, or a minor/patch past 99) **fails the Android
  build** with an explanation rather than shipping a version code that sorts
  wrongly and leaves users unable to update.
- Building this Gradle project outside the monorepo has no workspace version to
  read, so pass `-PanimatorRemoteVersion=<x.y.z>`.

Toolchain versions are pinned in `gradle/libs.versions.toml`: AGP 8.13.2,
Kotlin 2.3.21, Gradle 8.14.5, Compose BOM 2026.06.01, OkHttp 5.4.0.

AGP is deliberately the last 8.x rather than a 9.x: this app is small and a
conservative, well-understood plugin line removes a class of first-build
surprises. Bumping is a one-line change once a build is known to work.
