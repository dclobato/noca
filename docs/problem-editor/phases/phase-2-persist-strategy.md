# Phase 2 — Persist the validation strategy

Make the validation strategy an explicit, immutable, stored fact, and make every
behavioral decision read it instead of inferring it from validator presence.

**The UI does not change in this phase.** The existing combined editor keeps
working exactly as it does today; it simply stops driving judging decisions
through inference.

Authoritative detail: `docs/problem-editor/PLAN.md`, "Phase 1 — Persist the validation
strategy".

## Scope

### Enum and schema

`shared/enumerations.py`, next to `CustomValidatorActiveState` (every enum in
the file is a `StrEnum`; lowercase values have precedent in `Environment` and
`StatementLanguage`):

```python
class ProblemValidatorType(StrEnum):
    STANDARD = "standard"
    INTERACTIVE = "interactive"
    OUTPUT_CHECKER = "checker"
```

Add to both `problems` (`shared/db_schema/problem.py`) and `arena_problems`
(`shared/db_schema/arena/arena_problems.py`):

- `validator_type` — NOT NULL, declared with the verified
  `SAEnum(..., values_callable=lambda e: [m.value for m in e])` convention.
- `artifact_generation` — `BigInteger`, NOT NULL, default `0`. Phase 4's crash
  fence. It lands here so the schema settles in one migration, even though
  nothing increments it until Phase 4.

### Migration

One migration, `202608120001_problem_validator_type.py`:

1. Create the enum type **exactly once**:
   `postgresql.ENUM("standard", "interactive", "checker", name="problemvalidatortype").create(bind, checkfirst=True)`,
   then reference it with `create_type=False` on both `add_column` calls.
   Skipping this makes the second table emit a duplicate `CREATE TYPE` and the
   migration fails. Precedent: `202606220001_add_arena_user_badges_table.py:48-54`.
2. Add both `validator_type` columns as temporarily nullable.
3. Backfill `interactive` where any custom-validator row exists — including rows
   holding only pending or invalid revisions —
   `UPDATE problems SET validator_type='interactive' WHERE id IN (SELECT problem_id FROM problem_custom_validators)`,
   plus the Arena mirror.
4. Backfill everything else `standard`.
5. `SET NOT NULL` on both.
6. Add both `artifact_generation` columns.
7. Downgrade drops the columns, then the type with `.drop(bind, checkfirst=True)`.

No permanent server default on `validator_type`: every creation path states it
explicitly, so a path that forgets fails loudly instead of silently becoming
Standard. Neither table is new or high-churn — no autovacuum tuning migration.

### Creation paths

Set `validator_type` at creation and import only: `web/services/problem_service`
create path, `arena/services/admin_problem_service.create_problem`, and both
package importers. Until Phase 5 introduces the chooser, the existing create
forms pass `INTERACTIVE` when a validator source is supplied and `STANDARD`
otherwise — preserving today's observable behavior exactly.

### Immutability

- `admin_problem_service.update_problem` and Web's `edit_problem_submit` raise
  if a caller supplies a value differing from the stored one.
- Package import into an existing problem rejects a disagreeing strategy.
- Validator upload, replacement, and removal never change it. An Interactive
  problem whose source is removed **stays Interactive** and becomes
  non-judgeable until an active `VALID` revision exists.
- **Stated boundary:** this covers every supported application workflow, not
  direct SQL. Nothing detects a strategy changed with `psql`.

### Replace the inference

`status_view()` (`shared/services/custom_validator.py:210`) is called 41 times
across 13 files. Switch every *behavioral* decision to
`problem.validator_type is ProblemValidatorType.INTERACTIVE`; keep
`status_view()` only for the compile-status badge. Cover at least:

- Web and Arena problem create, edit, and test-case services and routes.
- Test-case ZIP parsing, storage, add/edit/download/export, including
  `require_output=not interactive` (`shared/tc_zip.py:80`).
- Sample-interaction visibility and validation.
- Submission and solution-test eligibility gates, both modules.
- Arena problem enablement.
- Problem-package import/export and Contest backup/restore.
- Public and administrative problem-detail indicators.
- Rejudge and problem-validation paths.

### AutoJudge dispatch

Not a call-site swap. `AutojudgeDb.get_custom_validator_dispatch_state`
(`autojudge/db/_custom_validator.py:79`) currently selects only from the
custom-validator table and derives `configured` from
`active_source is not None or candidate_source is not None`. Rewrite it to join
the domain's problem table and return the explicit strategy:

- **Standard** → no interactive validator, regardless of any stale validator row.
- **Interactive** → require an active `VALID` revision; otherwise fail closed as
  non-judgeable.
- **Output checker** → fail as unsupported. Never fall back to Standard.

Also update `autojudge/dispatch.py`, `submission_job.py`,
`arena_submission_job.py`, `solution_test_job.py`, and test-case loading.

### Judgeability contract

One shared decision, applied at every execution boundary — Arena enablement,
Contest and Arena submission creation, Web solution tests, AutoJudge dispatch
and preflight, administrative status displays, and full Interactive export:

- **Standard** is judgeable when ≥1 test case exists and every case has a
  present expected-output file (present-but-empty is valid).
- **Interactive** is judgeable when ≥1 secret input-only case exists, an active
  `VALID` validator exists, and no case carries Standard sample/output
  semantics.

Incomplete drafts are permitted; judgeability is enforced at the gates, not at
Save. Arena problems continue to start disabled; Contest lifecycle permissions
are unchanged.

## Commits

1. Enum, both columns, migration, backfill, immutability guards.
2. The inference replacement across Web, Arena, shared, and AutoJudge — its own
   commit, because a judging regression across 41 call sites plus dispatch must
   be bisectable in isolation.

## Tests

- **Migration** (style of `tests/web/test_contest_global_medals_migration.py`):
  validator rows in any state backfill to `interactive`; everything else to
  `standard`; both columns NOT NULL; the enum type is created once and dropped
  on downgrade.
- Service and ORM attempts to change the strategy fail.
- Validator source removal leaves the problem Interactive and non-judgeable.
- A pending or invalid Interactive candidate keeps the problem Interactive.
- Dispatch uses the explicit strategy: a Standard problem carrying a stale
  validator row dispatches as Standard; an Interactive problem with removed
  source is refused as non-judgeable, never judged with the token comparator; a
  stored `checker` fails as unsupported.
- Judgeability gates refuse incomplete drafts at submission, solution-test,
  Arena enablement, and full export; completing the data restores judgeability.

Focused run: migration, submission, solution-test, and worker tests.

## Gate

Existing editor behavior is unchanged end to end, every judging decision reads
the stored strategy, and the focused suites are green.
