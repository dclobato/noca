# Development Environment Setup

The local development workflow now lives in [BOOTSTRAP.md](./BOOTSTRAP.md).

Use the `Local development` quick start and the `Local Development Bootstrap`
sections there as the maintained source for host-run setup of all workspace
modules: `web`, `arena`, `autojudge`, `rating`, `aiassistant`, `healthmonitor`,
and `animator`. The non-Python `landingpage` module runs through its dedicated
Caddy container; for local work, `uv run python landingpage/serve_dev.py` serves
the page with the container's own headers and no Docker. See its
[module README](../landingpage/README.md).

Real-Valkey tests use logical DB 15 during serial runs. Pytest-xdist assigns
DBs 1–14 to its workers so their `FLUSHDB` calls cannot collide. Because Valkey
Pub/Sub ignores logical database selection, every test process also uses a
distinct `noca:test:<run>:<worker>:` channel prefix; local applications on DB 0
never receive test events.

## Skips are audited, and CI requires the full suite

A skip is indistinguishable from a pass in a summary line. That is not
hypothetical: the animator remote's Kotlin contract test spent a release cycle
skipping on a stale image pin while CI stayed green and reported nothing.

Every run therefore prints a **skip audit** naming what was skipped and why, and
under `CI` any skip outside four sanctioned groups fails the session:

| sanctioned group | why it is not run in CI |
| --- | --- |
| `real_docker` | starts real judge containers; deliberately kept out (see below) |
| `real_openai` | needs a paid OpenAI key |
| `real_ipqualityscore` | needs a paid IPQualityScore key |
| `tests/browser/` | Playwright drives a *running* Web/Arena instance |

The two paid-API groups and the browser suite need a credential or a live
service CI does not have. `real_docker` is different, and the distinction is
worth stating so it is not later mistaken for staleness: CI *could* run those
eight tests. Six need `dclobato/noca-judge-python3:compile` and `:run` (the
resolver matches on repository suffix, so the published images satisfy the
`noca/` registry defaults) and the other two build a `FROM scratch` image
inline and need no judge image at all. Two 45 MB pulls and
`NOCA_RUN_REAL_DOCKER_TESTS=1` would enable the group.

They are kept out by choice. CI runs against the *host* Docker daemon, so
enabling them would start real judge containers on that machine on every push,
and it would make every run depend on Docker Hub being reachable — under this
audit a registry outage becomes a red build rather than a skip. Building the
images in the job is possible too (a build context streams from the client, so
it works where a bind mount does not) but would test the image build rather
than the judge.

Everything else must run, so a database, a toolchain, or a fixture that goes
away is reported rather than absorbed. `real_db` and `real_valkey` are
deliberately **not** sanctioned: CI provides PostgreSQL and Valkey, so tests
carrying those markers must actually run.

The browser suite is matched by path rather than by marker because its skips
include a collection-level `importorskip`, which produces a report with no
markers on it at all.

`NOCA_REQUIRE_FULL_SUITE` states the demand explicitly and wins in both
directions whenever it is present at all, including when empty — a CI
environment that genuinely cannot run part of the suite opts out deliberately,
and a developer can opt in without pretending to be CI. The policy itself is
covered by `tests/test_skip_audit.py`, which needs no toolchain and so never
skips.

Note that a plain `uv run pytest` on a developer machine may report skips CI
would refuse — most commonly the real-PostgreSQL tests, which use
`tests/conftest.py`'s `test/test` defaults unless `NOCA_DB_*` is exported.
Running with `NOCA_REQUIRE_FULL_SUITE=1` locally is a quick way to see what your
environment is not covering.
