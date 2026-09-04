# NOCA roadmap

This document gives a high-level, theme-by-theme summary of open work across
NOCA. It is a reading guide, not the source of truth: every item lives in a
[Gitea issue](https://git.lobato.org:10880/dclobato/noca/issues) holding its
full contract, remaining scope, and status. You must consult that issue before
starting implementation work.

Issues carry module labels (`autojudge`, `shared`, `web`, `arena`, `animator`,
`rating`, `aiassistant`, `mailer`, `custom-validator`, `problem-editor`,
`docs-rendering`, `docker`) and are grouped for release by
[milestone](https://git.lobato.org:10880/dclobato/noca/milestones).
An issue labelled `idea` is not yet an accepted contract; one labelled
`will-not-fix` was considered and declined.

Because the issues are authoritative, this file deliberately states no counts
and links to no individual issue. Both would be a second copy of what Gitea
already answers, and a copy is what goes stale.

## REST API and access tokens

The largest body of open work, spanning all three application modules. NOCA is
server-rendered throughout and exposes no programmatic interface today; this
would add one, gated by per-user access tokens rather than session cookies.

The shared layer models the tokens themselves — validity, revocation, and
per-token capabilities. Web and Arena each get a token tab on the user profile,
individual and administrative revocation, automatic replacement on revoke, and
IP/port auditing of every token operation through the existing
`security_events` table.

The endpoints follow: for Web, listing contest problems, fetching a competitor
package, submitting a solution, reading a verdict, the clarification round trip,
and the current scoreboard. For Arena, searching problems, fetching a package,
submitting, reading a verdict, ranking position and neighbourhood, a user's
badges, and the editorial subject to its release policy.

[Open `shared`](https://git.lobato.org:10880/dclobato/noca/issues?labels=49&state=open) ·
[`web`](https://git.lobato.org:10880/dclobato/noca/issues?labels=50&state=open) ·
[`arena`](https://git.lobato.org:10880/dclobato/noca/issues?labels=51&state=open)

## Custom validators and output checkers

Interactive validators are implemented; the output-checker strategy is not, and
it is the oldest open theme in the repository.

Autojudge must persist checker attempt diagnostics separately from ordinary
per-test-case results, stage coherent reference data per job, and stop passing
`PER_LANGUAGE_LIMITS` so a validator only ever sees the submitted language's own
limits. The shared package layer needs strategy-aware pairing, validation, and
normalization so an optional checker `.out` reference file round-trips through
ZIP uploads, problem packages, and contest backups without being interpreted.

Web and Arena share the rest: letting problem owners manage and publish checker
reference files, requiring an explicit rejudgment after a checker or interactive
test-case change, showing checker diagnostics only to authorized audiences, and
closing two remaining gaps in the validation-strategy immutability guarantee —
enabling the disabled `checker` creation option once checker support lands, and
rejecting a mismatched `validator_type` on a future package-based update-import
path.

[Open `custom-validator` issues](https://git.lobato.org:10880/dclobato/noca/issues?labels=55&state=open)

## Heavy downloads, exports, and their caches

The contestant-facing problem exports now have generation-keyed on-disk caches
and per-actor budgets on both surfaces, but the theme is not finished. What
remains is the administrative and editorial half of it: the deterministic A+B
sample package is still rebuilt in a worker thread on every hit of the import
sample route in both modules and wants an in-process memo with revalidation
rather than a long `max-age`; the admin, editor and teacher exports and reports
carry no per-actor limit and want one bucket per surface, keyed by actor domain
rather than a single module-wide bucket; the team submissions download and the
Animeitor export still build their complete ZIP in memory and should move to the
temp-file-and-stream pattern; and the contest reports aggregate is recomputed
per request, wanting a short Valkey cache with a stated fail-open and
versioning contract.

Separately, the anonymous post-contest problem-set archive cache has three
review follow-ups of its own: it re-hashes the whole cached file on every
request instead of a cheap staleness check, its build lock serializes only
within one process so a multi-replica deployment can still run one full build
per replica, and the lock dictionary is never pruned. Replacing the local-disk
cache with an S3-compatible endpoint would give a persistent, replica-shared one
and could subsume the cross-process locking gap.

[Open `web`](https://git.lobato.org:10880/dclobato/noca/issues?labels=50&state=open) ·
[`arena`](https://git.lobato.org:10880/dclobato/noca/issues?labels=51&state=open) ·
[`problem-editor`](https://git.lobato.org:10880/dclobato/noca/issues?labels=59&state=open)

## Arena badges

Gamification awards badges but does not record what earned them. Two paired
items would change that: storing the submission that awarded each badge in the
ledger, and then showing it on the Arena profiles — the problem on the public
profile, the submission itself on the owner's private one.

[Open `rating` issues](https://git.lobato.org:10880/dclobato/noca/issues?labels=60&state=open)

## Editable email templates

Every outbound message is a Jinja template compiled into the image, so changing
a word in a password-reset mail needs a deployment. The open item would move
template bodies into storage, give uberadmins and Arena admins a panel to view
and edit them, and seed anything missing from the in-repo defaults at startup so
a fresh install and an upgrade both come up with a complete set.

[Open `shared`](https://git.lobato.org:10880/dclobato/noca/issues?labels=49&state=open) ·
[`web`](https://git.lobato.org:10880/dclobato/noca/issues?labels=50&state=open) ·
[`arena`](https://git.lobato.org:10880/dclobato/noca/issues?labels=51&state=open)

## Web

An idea, not yet an accepted contract: reference implementations
(`good`/`wrong`/`slow`/`pass`) carried in the problem package and stored with the
problem, so limits can be *validated* — does the test data reject a wrong
solution, does the time limit reject a slow one — rather than only derived from a
single correct one. Arena is deliberately excluded.

[Open `web` issues](https://git.lobato.org:10880/dclobato/noca/issues?labels=50&state=open)

## Shared problem data and packages

One idea outside the checker and token themes: reaping derived objects on S3
backends without lifecycle rules, which follows from the portability decision
that NOCA rely only on features every S3-compatible provider has.

[Open `shared` issues](https://git.lobato.org:10880/dclobato/noca/issues?labels=49&state=open)

## Autojudge

Beyond the checker work above, one idea is recorded: adding APL as a judge
language.

[Open `autojudge` issues](https://git.lobato.org:10880/dclobato/noca/issues?labels=48&state=open)

## Document rendering

Two ideas, neither an accepted contract: server-side PDF export of Markdown
statements through a Playwright-based worker, and a `table-caption` directive
for the shared Markdown pipeline. The PDF item explains why an existing
open-source service (`pdfoid`) was evaluated and rejected in favor of a
NOCA-native approach.

[Open `docs-rendering` issues](https://git.lobato.org:10880/dclobato/noca/issues?labels=52&state=open)

## Arena ideas

Two recorded ideas, neither an accepted contract. A *collection* property on
Arena problems — one event or course per problem (ICPC, Maratona SBC, InterIF,
Iniciantes), alongside the N categories a problem already carries — with a
second browser grouped by collection and still filterable by category inside
one; the issue itself is openly unconvinced that a collection is meaningfully
different from a category. And a Telegram bot, both for pushing notifications
to users and as a second-factor device.

[Open `arena` issues](https://git.lobato.org:10880/dclobato/noca/issues?labels=51&state=open)

## What has landed

Implemented contracts stay as closed issues rather than being deleted, so the
issue that recorded what was built remains the reference for why it is built
that way. Browse [closed
issues](https://git.lobato.org:10880/dclobato/noca/issues?state=closed), or a
single release through its
[milestone](https://git.lobato.org:10880/dclobato/noca/milestones).
