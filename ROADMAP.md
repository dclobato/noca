# NOCA roadmap

This document gives a high-level, module-by-module summary of accepted but
not-yet-implemented work across NOCA. It is a reading guide, not the source of
truth: every item is indexed in [docs/BACKLOG.md](docs/BACKLOG.md), which links
to the Gitea issue holding its full contract, remaining scope, and status. You
must consult that issue before starting implementation work.

The backlog currently holds 13 open and 17 implemented entries.

## Autojudge

Autojudge's open work is entirely about the output-checker validation
strategy: persisting checker attempt diagnostics separately from ordinary
per-test-case results, staging coherent reference data per job, and removing
the `PER_LANGUAGE_LIMITS` environment variable so a validator only ever sees
the submitted language's own limits.

See [Autojudge](docs/BACKLOG.md#autojudge) for the three pending items.

## Shared problem data and packages

The shared package layer needs strategy-aware pairing, validation, and
normalization rules so an optional checker `.out` reference file round-trips
correctly through ZIP uploads, problem packages, and contest backups.

Two cross-cutting items are indexed under the same heading:

- Add "Login with Google" to Arena, which would be the first external identity
  provider integration in the repository — Arena authentication is local-only
  today (password hash, optional TOTP, HS256 session cookie).
- Reap derived objects on S3 backends without lifecycle rules (an idea, not yet
  an accepted contract). It follows from the portability decision that NOCA
  rely only on features every S3-compatible provider has, which rules out
  leaning on bucket lifecycle expiration in the two items that proposed it.

See [Shared problem data and packages](docs/BACKLOG.md#shared-problem-data-and-packages)
for all three items.

## Web and Arena

Web and Arena share four pending items that keep problem authoring and
administration consistent across both domains: letting problem owners manage
checker `.out` reference files, requiring an explicit rejudgment after a
checker or interactive test-case change, showing checker diagnostics only to
authorized audiences, and closing two remaining gaps in the validation-strategy
immutability guarantee (enabling the disabled `checker` creation option once
checker support lands, and rejecting a mismatched `validator_type` on a future
package-based update-import path).

See [Web and Arena](docs/BACKLOG.md#web-and-arena) for all four items.

## Web

Web has one pending item, from the public post-contest problem-set archive
(`GET /problem-set/{slug}.zip`): harden the on-disk archive cache in
`web/services/problem_set_cache.py` — stop re-hashing the whole file on every
request, address the per-process-only build lock under a multi-replica
deployment, and prune the unbounded `_build_locks` dictionary. The entry also
notes that replacing the local-disk cache (`NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH`)
with an S3-compatible endpoint would give a persistent, replica-shared cache.

See [Web](docs/BACKLOG.md#web) for the item.

## Document rendering

Two ideas are recorded but not yet accepted contracts: server-side PDF export
of Markdown statements through a Playwright-based worker, and a fourth
`table-caption` directive for the shared Markdown pipeline. The PDF entry
explains why an existing open-source service (`pdfoid`) was evaluated and
rejected in favor of a NOCA-native approach.

See [Document rendering](docs/BACKLOG.md#document-rendering) for details and
rationale.

## Implemented

Contracts that have landed stay in the backlog as a pointer to the issue that
recorded what was built, rather than being deleted. Seventeen entries are
recorded so far, including the validation-strategy immutability guarantee (two
narrower bullets from its original contract remain open under
[Web and Arena](docs/BACKLOG.md#web-and-arena)), the decoupling of
problem-set/editorial release from scoreboard release, server-side LaTeX/math
rendering, and the Web contest-chrome work.

See [Implemented](docs/BACKLOG.md#implemented) for issue references.
