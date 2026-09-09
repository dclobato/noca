# Arena solver reconciliation, after v21.0.0

This runbook corrects `arena_problem_solvers` once, after upgrading past
v21.0.0. Until this release the table was written on first solve and deleted
nowhere, so every rejudge that withdrew an Accepted verdict left a solver row
standing. The autojudge now reconciles the pair on every Arena judgment that
finishes with a verdict or `FAILED`, but rows that drifted before that are still wrong and nothing
corrects them on its own.

There is **no Alembic migration** in this change, so no schema gate sequences
the deploy for you. The ordering below is the whole procedure.

The window needs the queue-consuming workers paused. PostgreSQL, Valkey, Web,
Arena, Health Monitor, and Animator can stay up: participants can browse and
submit throughout, their submissions simply queue until the workers resume.

<!-- prettier-ignore -->
> [!CAUTION]
> Do not run the reconciliation script while an autojudge from v21.0.0 or
> earlier is still consuming the queue. That image does not reconcile, so it
> keeps producing exactly the drift the script is removing, and the script
> writes without holding the per-pair advisory lock the new judge takes — a
> judgment settling between the script's read and its write is silently
> overwritten with a stale value.

## Understand what the script changes

`scripts/arena/reconcile_arena_solvers.py` applies the same rule the judge now
applies, over every `(user, problem)` pair that has either a solver row or a
live Accepted submission:

- no Accepted submission remains — the solver row is deleted;
- an Accepted submission remains and no row exists — the row is inserted;
- an Accepted submission remains and `solved_at` disagrees — it moves to the
  first live Accepted submission.

"Still Accepted" means each submission's most recent non-`SUPERSEDED` judgment,
kept only when it is `DONE` with a final verdict of `AC` — the same view the
badge rules use. The rule itself lives in
`shared/services/arena_query_helpers.first_live_ac_per_pair_select`, which the
judge and this script share, so a per-pair reconciliation and a whole-corpus
pass cannot disagree.

The script touches **no other table**. In particular it does not adjust
`arena_problem_ratings.solved_users` or `total_tries_before_solve`: those are a
batch-derived cache that `rate_all_problems()` rewrites from the solver rows on
every rating cycle. Correcting the rows is the whole job; the counters follow.

Badges are not affected. An already-earned Problems 25 or Full Clear is never
revoked, even once the solve behind it stops counting — solver rows follow the
verdict, badges do not. See [docs/ARENA_BADGES.md](docs/ARENA_BADGES.md).

## Prepare the deployment

1. Build or pull images containing this release for `autojudge` and `arena`.
   The judge is the one that must be rolled before the script runs; the Arena
   image is needed only because it carries the script. Beyond that, `web`,
   `arena`, and `rating` contain only additive `shared/` changes and are not
   sequenced against the judge.

2. Define a helper that runs Python from the new Arena image without invoking
   its normal entrypoint, which would upgrade the database directly to `head`.
   `PUID` and `PGID` must match the application user configured in Compose:

   ```bash
   run_arena_python() {
       docker compose run --rm --no-deps \
           --user "$PUID:$PGID" \
           --entrypoint /app/.venv/bin/python \
           arena "$@"
   }
   ```

3. Confirm the script is present in the image. It ships in the Arena image
   (`containers/arena/Dockerfile` copies it to
   `/app/scripts/arena/reconcile_arena_solvers.py`), so an image built before
   this release will not have it:

   ```bash
   run_arena_python /app/scripts/arena/reconcile_arena_solvers.py --help
   ```

4. Take a PostgreSQL backup, or at minimum dump the one table the script
   rewrites, so a bad run can be restored without a full database restore:

   ```bash
   docker compose exec -T postgres sh -c \
       'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --table=arena_problem_solvers' \
       > solvers-before.dump
   test -s solvers-before.dump
   ```

## Pause the queue-consuming workers

Pause `autojudge` from the Arena admin dashboard. PostgreSQL is the
authoritative source for pause state, so the workers converge on it from their
own polling; wait until the dashboard shows the judge as paused rather than
assuming the command took effect immediately.

Pausing the mailer and AI assistant is not required — neither writes
`arena_problem_solvers`.

## Roll the autojudge image

Deploy the new `autojudge` image while it is paused. Confirm it comes up and
reports presence on the dashboard, still paused.

From this point no process is creating new drift.

## Inspect the drift

Run the script read-only first. Its counts tell you how much drift accumulated,
which is worth recording before you change anything:

```bash
run_arena_python /app/scripts/arena/reconcile_arena_solvers.py --dry-run
```

```text
DRY RUN: 4812 pairs, 37 deleted, 0 inserted, 12 re-anchored, 4763 unchanged
```

Each corrected pair is logged at `INFO` with its user, problem, and the
timestamps involved, so the dry run is also the record of what the real run will
do. `--problem-id <uuid>` restricts the pass to one problem if you want to
inspect a specific rejudge before committing to the whole corpus.

A large `deleted` count is expected in proportion to how many mass rejudges the
deployment has done. `inserted` is normally zero: it means a pair holds an
Accepted submission that never produced a solver row.

## Apply the correction

```bash
run_arena_python /app/scripts/arena/reconcile_arena_solvers.py
```

The script commits once, at the end of the pass. Re-running it is safe and
idempotent — a second run over an already-corrected corpus reports every pair
unchanged.

## Resume the workers

Resume `autojudge` from the Arena admin dashboard and confirm the queue drains.

## Rebuild the derived counters

`arena_problem_ratings.solved_users` and `total_tries_before_solve` still hold
their pre-correction values until the rating worker's next problem-difficulty
cycle rewrites them from the corrected rows. With `NOCA_RATING_INTERVAL` at its
`86400` default that is up to a day away, and a freshly restarted rating worker
waits a full interval rather than computing at boot.

To close the window inside the maintenance window instead, restart `rating`
once with:

```dotenv
NOCA_RATING_COMPUTE_ON_STARTUP=true
```

Then restore the setting to its previous value so subsequent restarts do not
each trigger a full recomputation. Leaving the counters to catch up on their own
is also fine — the only surface reading them between cycles is the AC-rate
percentage on the problem browse list; the displayed difficulty is written
solely by that cycle and does not move until it runs regardless.

## Verify

1. Every solver row agrees with the live verdicts:

   ```bash
   run_arena_python /app/scripts/arena/reconcile_arena_solvers.py --dry-run
   ```

   Expect `0 deleted, 0 inserted, 0 re-anchored`.

2. A rejudge now corrects the table by itself. Pick a problem with a known
   solver, rejudge it from the Arena admin, and confirm the solver count on the
   problem page tracks the new verdicts.

3. After the rating cycle has run, a problem whose solvers shrank shows a
   difficulty consistent with its corrected solver base.

## Recover from a failure

The script's only writes are to `arena_problem_solvers`, and it commits once at
the end, so a run that fails partway commits nothing. If a completed run
produced a result you want to undo, restore the table from the dump taken during
preparation.

`--data-only` restores rows but does not clear the ones already there, and
`--clean` only drops objects it is about to recreate — which a data-only restore
never does. Used together they leave the current rows in place, and the restore
then fails on primary-key conflicts. Empty the table explicitly first, and run
the restore itself in one transaction so a partial restore rolls back:

```bash
docker compose exec -T postgres sh -c \
    'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        -c "TRUNCATE arena_problem_solvers"'

docker compose exec -T postgres sh -c \
    'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --single-transaction \
        --table=arena_problem_solvers' \
    < solvers-before.dump
```

The table is empty between the two commands. If the restore fails, the table
stays empty and the restore must be re-run — do not resume the workers in
between. Verify the row count before you do:

```bash
docker compose exec -T postgres sh -c \
    'psql -tAc "SELECT count(*) FROM arena_problem_solvers" -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Restoring the old rows does not restore the old behavior: the new judge keeps
reconciling on every finishing judgment, so the rows drift back into agreement as
submissions are judged. Rolling the judge back to the v21.0.0 image is the only
way to stop that.
