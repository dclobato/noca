# Product

<!-- impeccable:product-schema 1 -->

NOCA is a web-based competitive programming ecosystem. This record captures
durable product truth for future product and interface work across the complete
workspace.

## Platform

web

## Users

NOCA serves two related groups. Contest organizers, administrators, judges,
staff, and teams use Contest to prepare, operate, participate in, and audit
ICPC-style competitions. Learners and teachers use Arena to practice competitive
programming, track progress, organize classes, and manage scheduled problem
sets.

Operators also use the Health Monitor and Animator to observe deployed services
and present live scoreboards and post-freeze reveal ceremonies.

## Product purpose

NOCA makes competitive programming infrastructure available for both formal
competitions and ongoing training. It succeeds when organizers can run reliable,
auditable contests, participants can submit solutions and receive trustworthy
results, and learners and teachers can sustain structured practice.

## Positioning

NOCA combines an ICPC-style contest system and a free-to-use training
environment around one isolated, auditable AutoJudge infrastructure. Contest and
Arena share contracts and judging capabilities while retaining independent
identity domains and the ability to run together or as separate deployments.

## Operating context

Contest workflows include contest setup, role and team management, problem and
test-case preparation, submissions, automated or judge-confirmed verdicts,
clarifications, operational tasks, scoreboards, reports, and live presentation.
Events can span multiple sites and continue through scoreboard freeze and reveal
ceremonies.

Arena workflows include self-service registration, problem discovery, solution
submission, progress and rating review, classes, scheduled problem sets,
notifications, and optional AI-assisted submission feedback. Teachers manage
classes and assignments, while administrators maintain users, affiliations,
categories, problems, test cases, and worker state.

Deployments use PostgreSQL for durable state, Valkey for queues and
coordination, a shared filesystem for problem data, and Docker as the
untrusted-code execution boundary. OpenAI-backed review is optional, and Arena
remains usable without it.

## Capabilities and constraints

NOCA is a Python and FastAPI web platform with server-rendered Jinja templates,
Bootstrap, shared CSS design tokens, and vanilla JavaScript. It is organized as
independently deployable workspace modules that communicate through shared
infrastructure instead of direct application imports.

Durable constraints include:

- Keep Contest and Arena as distinct products with separate user identity and
  authorization domains.
- Keep submitted code outside user-facing application processes and execute it
  only through the isolated AutoJudge boundary.
- Treat PostgreSQL as authoritative durable state and Valkey as cache,
  synchronization, presence, and queue infrastructure.
- Preserve auditability for submissions, judgments, verdict changes, and
  operator actions.
- Preserve Arena's privacy and youth-safety requirements, including its age gate
  and guardian-consent rules.
- Keep optional workers and presentation runtimes from becoming prerequisites
  for core Contest or Arena workflows.
- Support independent and combined deployments of Contest and Arena.
- Treat `Contest`, `Arena`, `AutoJudge`, `Animator`, `Rating`, `AI Assistant`,
  and `Health Monitor` as established product terminology.
- Preserve the repository's non-commercial source license. Commercial use
  requires prior written authorization and attribution under `LICENSE`.

## Brand commitments

The product name is NOCA, expanded as “Next Online Contest Administrator.”
Its identity must preserve the distinction between `NOCA Contest` and
`NOCA Arena` while presenting them as parts of one ecosystem.

The product voice is direct, operational, and technically precise. Arena can
also be warm and playful where that supports learning; Capybara badges are an
established part of its personality. The project maintains a public-source,
community-oriented identity while its actual usage rights remain governed by
the non-commercial license.

Existing logos, favicons, shared type assets, and the shared NOCA identity
tokens are established assets. Future work must treat these as evidence of the
current identity unless the user explicitly requests a redesign.

## Evidence on hand

The repository contains working product surfaces, implementation contracts, and
assets that future work can use as evidence:

- `README.md` describes the product boundaries, supported workflows, and module
  capabilities.
- `docs/ARCHITECTURE.md` and related runtime documentation describe system
  boundaries, reliability mechanisms, and deployment behavior.
- `docs/PADROES_UI.md` records established cross-surface UI patterns.
- `shared/static/css/tokens.css` and `shared/static/css/common.css` define the
  shared visual identity and reusable browser behavior.
- `web/static/`, `arena/static/`, and `animator/static/` contain current visual
  assets and frontend implementations.
- `LICENSE` and `AUTHORS` provide the authoritative licensing and authorship
  record.

No customer testimonials, adoption numbers, performance benchmarks, awards,
pricing, or third-party endorsements have been confirmed. Future work must not
invent those claims.

## Product principles

- Make judging and contest operations trustworthy, inspectable, and resilient.
- Keep competition and training experiences distinct while sharing robust core
  infrastructure.
- Prioritize task clarity for learners, teachers, competitors, judges, and
  operators working under time pressure.
- Preserve privacy, youth safety, and explicit authorization boundaries.
- Let optional capabilities enrich the ecosystem without weakening core
  workflows or deployment independence.

## Accessibility and inclusion

NOCA serves learners, educators, competitors, and event staff across desktop and
mobile web contexts. Interfaces must remain responsive, keyboard-operable,
semantically structured, and usable in both light and dark themes. No specific
formal accessibility conformance level has been confirmed; selecting one remains
an open product decision.
