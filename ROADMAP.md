# NOCA roadmap

This document gives a high-level, module-by-module summary of accepted but
not-yet-implemented work across NOCA. It is a reading guide, not the source of
truth: every item is indexed in [docs/BACKLOG.md](docs/BACKLOG.md), which links
to the Gitea issue holding its full contract, remaining scope, and status. You
must consult that issue before starting implementation work.

## Autojudge

Autojudge's open work is entirely about the output-checker validation
strategy: persisting checker attempt diagnostics separately from ordinary
per-test-case results, staging coherent reference data per job, aligning
interactive solution-test diagnostics with the same contract, and removing the
`PER_LANGUAGE_LIMITS` environment variable so a validator only ever sees the
submitted language's own limits.

See [Autojudge](docs/BACKLOG.md#autojudge) for the four pending items.

## Shared problem data and packages

The shared package layer needs strategy-aware pairing, validation, and
normalization rules so an optional checker `.out` reference file round-trips
correctly through ZIP uploads, problem packages, and contest backups.

See [Shared problem data and packages](docs/BACKLOG.md#shared-problem-data-and-packages)
for the pending item.

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

Web has two pending items from the public post-contest problem-set archive
(`GET /problem-set/{slug}.zip`):

- Harden the on-disk archive cache in `web/services/problem_set_cache.py`:
  stop re-hashing the whole file on every request, address the per-process-only
  build lock under a multi-replica deployment, and prune the unbounded
  `_build_locks` dictionary. This item also notes that replacing the local-disk
  cache (`NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH`) with an S3-compatible endpoint
  would give a persistent, replica-shared cache.
- Decouple problem-set and editorial release from scoreboard release: add a
  separate flag so publishing a contest's scoreboard doesn't also publish its
  secret test data, validator source, and editorials.

See [Web](docs/BACKLOG.md#web) for both items.

## Document rendering

Two ideas are recorded but not yet accepted contracts: server-side PDF export
of Markdown statements through a Playwright-based worker, and server-side
LaTeX/math rendering. Both entries explain why an existing open-source
service (`pdfoid`, `mathoid`) was evaluated and rejected in favor of a
NOCA-native approach.

See [Document rendering](docs/BACKLOG.md#document-rendering) for details and
rationale.

## Implemented

Contracts that have landed stay in the backlog as a pointer to the commit that
implemented them, rather than being deleted. The validation-strategy
immutability guarantee is the one entry recorded so far; two narrower bullets
from its original contract remain open under
[Web and Arena](docs/BACKLOG.md#web-and-arena).

See [Implemented](docs/BACKLOG.md#implemented) for commit references.
