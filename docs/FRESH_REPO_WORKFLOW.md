# Working With `noca` and `noca-fresh`

This document explains how to keep developing in `noca` while publishing stable changes through `noca-fresh`.

## Context

- `noca` is the original development repository.
- `noca-fresh` is a separate repository created from the current state of `noca`.
- `noca-fresh` has a single Git commit in history.
- `noca-fresh` has a single Alembic baseline migration.
- The two repositories now have unrelated Git histories.

Because of that, you should not expect to open a GitHub PR directly from a branch in `noca` into `noca-fresh`.

## Recommended Workflow

1. Keep doing normal day-to-day development in `noca`.
2. Commit freely in `noca` while features are in progress.
3. When a change is stable, port it into `noca-fresh`.
4. Create a branch in `noca-fresh` for that ported work.
5. Commit the change in `noca-fresh`.
6. Open the PR from the `noca-fresh` branch into `noca-fresh/main`.

In short: `noca` remains your working repo, and `noca-fresh` is the repo where you prepare and submit the final PRs.

## Why Not a Direct PR?

GitHub PRs work naturally when branches belong to the same repository history.

Here, `noca-fresh` was intentionally recreated with:

- one squashed commit
- one squashed migration baseline

That means `noca` and `noca-fresh` are related by file content, not by Git ancestry.

## Safe Ways To Move Changes

### Option 1: Manual Copy

Use this when the change is small or easy to review.

1. Implement and commit the change in `noca`.
2. Open the same files in `noca-fresh`.
3. Copy the final code across manually.
4. Review the result in `noca-fresh`.
5. Commit and open the PR from `noca-fresh`.

### Option 2: Patch-Based Transfer

Use this when the change is larger and you want a cleaner transfer.

From `noca`, generate a patch:

```powershell
git diff <base-commit>..<feature-commit> > ..\noca-change.patch
```

Then in `noca-fresh`, apply it:

```powershell
git apply ..\noca-change.patch
```

After applying:

1. Inspect the result in `noca-fresh`.
2. Fix conflicts or context mismatches if needed.
3. Run tests there if the change affects runtime behavior.
4. Commit in `noca-fresh`.
5. Open the PR from `noca-fresh`.

### Option 3: Cherry-Pick Logic, Not History

Do not try to preserve original commit ancestry between the repos.

Treat `noca-fresh` as a content port:

- bring over the final code
- make a new commit in `noca-fresh`
- review and merge there

## Migration Rule

`noca-fresh` no longer uses the old Alembic chain from `noca`.

Important rule:

- new migrations for `noca-fresh` must be created on top of the single baseline migration already present in `noca-fresh`
- do not copy old historical migration files from `noca` into `noca-fresh`

If a feature in `noca` adds or changes schema:

1. port the model/schema change into `noca-fresh`
2. create a new migration in `noca-fresh`
3. validate it in `noca-fresh`

## Practical Example

Feature flow:

1. Build feature `X` in `noca`.
2. Commit it in `noca`.
3. Switch to `noca-fresh`.
4. Create a branch such as `feature/x`.
5. Port the relevant file changes.
6. If schema changed, generate the migration in `noca-fresh`.
7. Run checks in `noca-fresh`.
8. Commit in `noca-fresh`.
9. Open the PR to `noca-fresh/main`.

## Summary

- Develop in `noca`.
- Publish through `noca-fresh`.
- Move changes by copying or applying patches.
- Open PRs from branches inside `noca-fresh`.
- Keep all future migration history for the fresh repo inside `noca-fresh`.
