# How to implement the animator phases

Use one fresh coding-agent session for each phase, beginning with
[Phase 00](Phase-00.md) and continuing in order through
[Phase 21](Phase-21.md). Each session must inspect the current repository,
produce a short execution checklist, wait for your approval, implement only its
phase, and satisfy that phase's validation and completion criteria.

## Prepare each session

Before starting a phase, confirm that the previous phase is complete and
committed. Copy the prompt from
[the implementation prompt](implementation-prompt.md), replace the phase
placeholder, and send it to the coding agent from the repository root.

Use a capable code-oriented model with enough context for the phase. Don't ask
one session to implement multiple phase files.

## Run the phase

Follow this operating cycle for every phase:

1. Start a fresh session for the next `Phase-XX.md` file.
2. Let the agent inspect the repository and produce its concise checklist.
3. Review the checklist for scope creep, omissions, and invalid assumptions.
4. Approve execution only when the checklist matches the phase boundary.
5. Require every completion criterion and validation command to pass.
6. Review and commit the phase as one logical change.
7. Start a fresh session for the following phase.

## Decide whether a phase is complete

Don't advance merely because the agent wrote most of the code. Advance only
when the agent explicitly verifies that all completion criteria are satisfied
and reports the result of every required validation command.

If a phase uncovers a repository mismatch, require the agent to explain it. Let
the agent resolve the mismatch within the current phase when the correction
stays inside its scope. When the correction changes a later phase's assumptions,
update that later plan before continuing.

## Preserve phase isolation

A separate session per phase gives the next agent a clean context and forces it
to inspect the implementation left by earlier phases instead of relying on
conversation history. Keep unrelated worktree changes out of the phase commit,
and don't let an agent implement future phases early.

## Next steps

Start with [Phase 00](Phase-00.md) and use the exact workflow in
[the implementation prompt](implementation-prompt.md).
