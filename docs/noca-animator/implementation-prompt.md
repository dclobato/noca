# Animator phase implementation prompt

Copy the prompt below into a fresh coding-agent session for each phase. Replace
`XX` with the two-digit phase number, review the agent's execution checklist,
and approve it before the agent edits files.

```text
Implement docs/noca-animator/Phase-XX.md.

Before editing:

1. Read AGENTS.md completely.
2. Read docs/noca-animator/PLANO_UNIFICADO.md completely.
3. Read docs/noca-animator/Phase-XX.md completely.
4. Inspect the current repository files referenced by the phase.
5. Confirm that all dependencies from earlier phases are present.
6. Check PyPI as required by AGENTS.md before introducing code or dependencies.
7. Produce a concise execution checklist based on the current repository state.

Stop after presenting the checklist and wait for my explicit approval before
editing files or executing the implementation.

After approval, implement the entire phase, including its tests and
documentation updates.

Follow the phase scope literally. Do not implement later phases or unrelated
cleanup. Preserve unrelated worktree changes.

Run every validation command required by the phase. Correct all errors before
finishing.

At the end, report:

- What was implemented.
- Any necessary deviation from the phase and why.
- Tests and validation commands run, with results.
- Any remaining blocker that prevents the completion criteria from being met.
- Whether the phase completion criteria are fully satisfied.
```

For the full operating cycle and phase-completion rules, read
[the animator implementation guide](HOWTO.md).
