# Animator planning documents

The 23 implementation phases that built the animator module now live as Gitea
issues rather than as `Phase-XX.md` files in this directory. Each issue carries
its phase's full text, its blocking relationship to the previous phase, and —
for the phases that shipped — the commit that closed it.

| Phase | Issue | State |
| --- | --- | --- |
| 00 Extract the shared scoreboard projection | #44 | closed |
| 01 Add animator site and access-control schema | #45 | closed |
| 02 Implement site settings and operator-secret services | #46 | closed |
| 03 Add the Web animator administration page | #47 | closed |
| 04 Scaffold the standalone animator runtime | #48 | closed |
| 05 Build contest metadata and scoreboard snapshot feeds | #49 | closed |
| 06 Render the initial live scoreboard interface | #50 | closed |
| 07 Stream live contest events over SSE | #51 | closed |
| 08 Animate live scoreboard updates | #52 | closed |
| 09 Model reveal sessions and build frozen projections | #53 | closed |
| 10 Implement the reveal state machine | #54 | closed |
| 11 Persist and publish reveal sessions in Valkey | #55 | closed |
| 12 Expose authenticated reveal control APIs | #56 | closed |
| 13 Add reveal spectator APIs and team photos | #57 | closed |
| 14 Build reveal projector and operator interfaces | #58 | closed |
| 15 Expose team metadata and avatar | #59 | open (optional) |
| 16 Store and merge team presentation profiles | #60 | open (optional) |
| 17 Add presentation-profile administration | #61 | open (optional) |
| 18 Package and wire production deployment | #62 | closed |
| 19 Harden reveal recovery and concurrency | #63 | closed |
| 20 Add animator observability | #64 | open |
| 21 Prove end-to-end behavior and finalize documentation | #65 | open |
| 22 Enforce one active reveal controller | #66 | open (optional) |

The required path runs 00 → 14 → 18 → 19 → 20 → 21. Phases 15–17 and 22 are
optional backlog items, to be implemented only when their stated operational
requirement exists.

What remains here is the reference material those phases were derived from:

- [PLANO_UNIFICADO.md](PLANO_UNIFICADO.md) — the unified implementation plan the
  phases cite by section number
- [ANIMATOR_MODULE_PROPOSAL.md](ANIMATOR_MODULE_PROPOSAL.md) — the original
  module proposal
- `base-plans/`, `compatible-layer/`, `maratona-revelator-docs/` — the source
  plans and the Maratona reference implementation's documented endpoints

For what the animator actually does today, see
[docs/ARCHITECTURE.md](../ARCHITECTURE.md),
[animator/docs/ROUTES.md](../../animator/docs/ROUTES.md), and
[animator/docs/SERVICES.md](../../animator/docs/SERVICES.md).
