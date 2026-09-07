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

Most of this theme has landed. The contestant-facing problem exports have
generation-keyed on-disk caches; every heavy export and report on both surfaces
now carries a per-actor budget; the contest reports aggregate is cached and the
large exports stream from a temp file instead of being built in memory; and the
deterministic A+B sample package is memoized on both import pages. The Valkey
entries all of that relies on are catalogued.

What remains is the anonymous post-contest problem-set archive cache and its
review follow-ups: it re-hashes the whole cached file on every request instead
of a cheap staleness check, its build lock serializes only within one process so
a multi-replica deployment can still run one full build per replica, and the
lock dictionary is never pruned. Replacing the local-disk cache with an
S3-compatible endpoint would give a persistent, replica-shared one and could
subsume the cross-process locking gap.

[Open `web` issues](https://git.lobato.org:10880/dclobato/noca/issues?labels=50&state=open)

## Arena badges

Gamification awards badges but does not record what earned them. Two paired
items would change that: storing the submission that awarded each badge in the
ledger, and then showing it on the Arena profiles — the problem on the public
profile, the submission itself on the owner's private one.

[Open `rating` issues](https://git.lobato.org:10880/dclobato/noca/issues?labels=60&state=open)

## Editable email templates

Every outbound message is a Jinja template compiled into the image, so changing
a word in a password-reset mail needs a deployment. The theme has since been
settled into three phases, and the shape moved: overrides live on the
deployment's filesystem under the operator's own Git history, not in the
database, and NOCA never seeds that directory.

The first phase replaces Jinja with one constrained format — a placeholder
grammar with no expressions, single-line subjects and size limits — behind a
shared renderer and per-module catalogues, converting every packaged template
and pulling hardcoded subjects and Python-side display text into it. The second
adds an optional override directory mounted read-only into Web and Arena,
validated at startup so a bad file refuses the boot, re-read on change at send
time with the last valid version retained, plus a validation CLI and the Compose
and backup integration. The third is optional and read-only: admin pages that
show which template is in force, whether an override has drifted from the
shipped default, and previews rendered from sample values — with no save path.

[Open `shared`](https://git.lobato.org:10880/dclobato/noca/issues?labels=49&state=open) ·
[`web`](https://git.lobato.org:10880/dclobato/noca/issues?labels=50&state=open) ·
[`arena`](https://git.lobato.org:10880/dclobato/noca/issues?labels=51&state=open)

## Valkey payload authentication

Every value NOCA writes to Valkey is unauthenticated today, and one of them is
load-bearing: the autojudge reads a job hash to decide which pipeline to run, so
a routing decision comes from unsigned data, and a mail job carries its whole
rendered message with no database row behind it. The open item adds an HMAC
envelope over queue-job hashes and cache entries, with deliberately opposite
failure modes — caches fail open and recompute, queues fail closed and refuse —
secrets split by authority so a compromised worker cannot mint another module's
jobs, and a staged rollout because enforcing signatures ahead of the producers
would silently drop real work. It is explicit that signing buys integrity and
authenticity but *not* replay protection, which needs PostgreSQL-anchored state
and is recorded as follow-on work.

[Open issues](https://git.lobato.org:10880/dclobato/noca/issues?state=open)

## Web

Two ideas, neither an accepted contract. Reference implementations
(`good`/`wrong`/`slow`/`pass`) carried in the problem package and stored with the
problem, so limits can be *validated* — does the test data reject a wrong
solution, does the time limit reject a slow one — rather than only derived from a
single correct one; Arena is deliberately excluded. And an evaluation of whether
the single-session policy flag belongs on the contest rather than on each user:
the machinery that exists to manage a per-user flag is largely there to express
one contest-wide decision, and the argument is to decide it before the session
binding work is considered finished, since unwinding a released per-user column
costs more later.

[Open `web` issues](https://git.lobato.org:10880/dclobato/noca/issues?labels=50&state=open)

## Shared problem data and packages

Two ideas outside the checker and token themes. Reaping derived objects on S3
backends without lifecycle rules, which follows from the portability decision
that NOCA rely only on features every S3-compatible provider has. And an
evaluation of replacing JSON with TOML across the project, which concludes
against it: TOML has no null, and the package format deliberately writes every
key — as `null` where a producer cannot store it — so a consumer never has to
guess whether absence means unset or unsupported. The narrow additive option it
leaves open is a TOML *front end* for authoring a problem package, with JSON
staying canonical.

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
