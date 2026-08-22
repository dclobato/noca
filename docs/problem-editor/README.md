# Problem editor planning documents

The phased execution slices that built the two problem editors now live as
Gitea issues rather than as files under `phases/`. Each issue carries its
phase's full text, its blocking relationships, and the commits that closed it.

| Phase | Issue | Gate |
| --- | --- | --- |
| 1 Correct the plan | #67 | Plan corrections verified against the codebase |
| 2 Persist the validation strategy | #68 | Strategy stored and authoritative; UI unchanged |
| 3 Version packages and backups | #69 | v2 packages and v2 backups, both v1-compatible |
| 4 Build safe editor transactions | #70 | Edit-aware artifact swap proven under four failure points |
| 5 Add the chooser and the tabbed editor | #71 | Chooser + tabs shipped; satellite routes untouched |
| 6 Unify editor mutations | #72 | Every editor-initiated mutation goes through Save |
| 8 Split the editor: definition vs judgment data | #73 | Two editors shipped; docs complete, full suite green |

Every phase shipped, so every issue is closed. There is no phase 7: it was
"documentation and validation" for the unified editor phase 6 produced, and
usage of that editor changed the design instead, so phase 8 superseded it and
carried both the change and phase 7's document list.

The blocking graph is not a straight line — 3, 4, and 5 all fan out from 2, and
6 needs both 4 and 5:

```
1(#67) → 2(#68) ─┬→ 3(#69)
                 ├→ 4(#70) ─┬→ 6(#72) → 8(#73)
                 └→ 5(#71) ─┘
```

Two of those edges are load-bearing rather than incidental. Phase 4 had to
precede phase 6, because folding a ZIP upload into a Save that still committed
before writing files is the one sequencing mistake that loses author data. Phase
5 had to precede phase 6 so the tabs were built while the satellite routes still
worked, keeping the editor out of a half-migrated state.

Phase 6's implementation report is a comment on #72.

[PLAN.md](PLAN.md) — the authoritative combined design the phases execute —
stays here. For what the editors actually do today, see
[docs/ARCHITECTURE.md](../ARCHITECTURE.md),
[web/docs/ROUTES.md](../../web/docs/ROUTES.md), and
[arena/docs/ROUTES.md](../../arena/docs/ROUTES.md).
