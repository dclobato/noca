---
description: Bump project version
allowed-tools: Bash(git diff:*), Read
argument-hint: [version]
---
# Context
We are going to bump version and create a conventional commit with changelog. 

# Task
- Run all tests and check if they are green.  If some test fail, alert user and ask what he wants to do: fix test based on code, or fix code based on test. Apply the fixe choosed by the user
- If we have any residual red test, stop this skill and ask user to fix the problems.
- Check current project version on pryproject.toml
- Bump project version to $ARGUMENTS
- Sync all projects package using `uv sync --all-packages --reinstall-package noca-shared --reinstall-package noca-web --reinstall-package noca-arena --reinstall-package noca-autojudge --reinstall-package noca-rating --reinstall-package noca-aiassistant
- Update uv.lock by running `uv lock`
- Update CHANGELOG.md with all changes since last version bump
- Create the conventional commit, keeping on commit message the changelog since last minor version
- Tag the commit with v<version>
- Push commit and tags

