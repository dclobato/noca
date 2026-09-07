# NOCA Arena Module Architecture

This document describes the `arena/` module: the public-facing FastAPI
application for Arena users (default port 8001), with its own identity domain
separate from contest users and uberadmins. It covers what the module owns,
its default-deny access control, Google sign-in, the pseudonymous identity and
age shield that protect minors, parental consent, and the Arena-owned tables of
the shared schema. Read [ARCHITECTURE.md](ARCHITECTURE.md) first for the module
boundary, and [ARCHITECTURE_SHARED.md](ARCHITECTURE_SHARED.md) for the problem
model and security contracts Arena shares with Web.

Related references:
- [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview and the boundary between modules
- [arena/docs/ROUTES.md](../arena/docs/ROUTES.md) and [arena/docs/SERVICES.md](../arena/docs/SERVICES.md) for arena-layer responsibilities
- [ARENA_GOOGLE_OAUTH.md](ARENA_GOOGLE_OAUTH.md) for the Google sign-in configuration
- [ARENA_BADGES.md](ARENA_BADGES.md) and [ARCHITECTURE_RATING.md](ARCHITECTURE_RATING.md) for badges and rating cycles
- [AIREVIEW_FLOW.md](AIREVIEW_FLOW.md) and [ARCHITECTURE_AIASSISTANT.md](ARCHITECTURE_AIASSISTANT.md) for the AI review pipeline
- [FASTAPI_FLASH.md](FASTAPI_FLASH.md) for the flash-message pattern

## Responsibilities

The arena module is the public-facing FastAPI application for Arena users. It owns
Arena signup and login, OTP-protected accounts, login history, LGPD age-gate handling,
Arena submissions, classes (teacher-owned groups with dated membership history and a
self-service registration-request workflow), problem sets (teacher-owned, scheduled
problem collections within a class; students may opt a submission into a set at submit
time to make it visible to the teacher, and post-deadline rating snapshots freeze each
student's AC totals), and Arena-specific user identity separate from contest users and
uberadmins.

Arena also serves the platform announcement board described in [ARCHITECTURE_SHARED.md](ARCHITECTURE_SHARED.md): the
anonymous list and detail pages under `/announcements` (a sidebar entry for
everyone), and `ARENA_ADMIN`-only management under `/admin/announcements` that
publishes through the same shared service and editor Web uses, with one Arena-only
control -- the `required` checkbox, chosen once at publication and stored now, whose
acknowledgment pop-up is a separate feature.

When `NOCA_ARENA_GOOGLE_OAUTH_ENABLED` is set, Arena additionally serves Google
sign-in under `/auth/google` (`arena/routes/auth_google.py` for start, callback,
link and unlink; `arena/routes/auth_google_complete.py` for the step that collects
the date of birth and terms Google cannot supply). The trust model, the gate reuse,
and the placeholder-password decision are described below under [Google sign-in](#google-sign-in).

## Statement language

Every Arena problem records the natural language of its statement in
`arena_problems.statement_language` (`pt`, `en`, or `es`; nullable while unknown), which
both problem lists expose as a filter and the problem package carries as the optional
`statement_language` key. The value is normally derived rather than typed: the author may
leave the form on "detect automatically", and `arena.services.statement_language_service`
detects it with `lingua`. An explicit choice that disagrees with detection is refused until
the author confirms it, and an import that stated no language is flagged for the importer to
verify. Rows predating the column are filled by
`scripts/arena/backfill_statement_language.py`, which only ever writes rows whose language is
still NULL.

## Default-deny access control

Arena access is **default-deny**: a single global FastAPI dependency
(`arena.dependencies.access_control.enforce_arena_authentication`, registered on
the app in `arena/main.py`) requires a valid logged-in session for every route
except a small public allowlist — `/dashboard`, the `/problems` list (the problem
*detail* page and sub-resources stay protected), `/legal/*`, `/help/*`,
`/announcements/*` (the public announcement board; its `/admin/announcements`
management stays gated), the entire `/auth/*` namespace (login, signup, the
Google sign-in routes, and the other pre-login flows), `/`, `/health`, and the root
favicon assets. The gate reads only
`request.state.validated_token` (populated by `ArenaAuthMiddleware`, no database
I/O) and raises `HTTPException(401)`, which the Arena exception handler turns into
a login redirect (HTML), an `HX-Redirect` (HTMX), or a plain 401 (API). Per-route
`get_current_arena_user` / `require_arena_*` dependencies still apply full
database gating and role checks on top of the gate. New routes are therefore
protected automatically unless their path is added to the allowlist.

## Terms of Service reset

Publishing new Terms of Service or a new Privacy Policy retires every acceptance
already on file, so `ARENA_ADMIN` can clear them all from
`GET /admin/dashboard/terms` (`arena/services/admin_terms_service.py`). The reset
is one bulk `UPDATE` rather than a per-row loop because it must be atomic -- a
partial one would leave two populations bound to two different documents -- and
the acting admin's own acceptance is re-dated to now rather than cleared, so the
operation can neither end the session performing it nor strand that admin at an
acceptance screen Arena gives them no other way to reach. What makes it *effective* is optional and explicit: the acceptance
gate runs at **login**, so clearing the flag alone leaves a live session browsing
under the retired documents until its cookie expires, and a `sign_users_out`
checkbox therefore bumps `session_version` in the same statement to invalidate
every affected session at once. The choice is per reset because a terms change
that merely clarifies wording does not warrant signing an entire user base out
mid-submission, while one that changes what users consent to does.

## Reverse-geocoder relay

Arena's reverse-geocoder relay, the profile reverse-geocoder proxy
(`POST /user/profile/location/detect`), is governed by
`arena/services/geocode_service.py` rather than by the route. The provider's usage
policy is stated **per application** — Nominatim allows one request per second for the
whole deployment — so a per-user cap alone cannot honour it. A detection is capped per
user, then answered from a Valkey cache of each 0.001-degree cell (about 100 m), and
only a cache miss consults a deployment-wide gate whose per-second pacing and windowed
budget are decided in one atomic Lua step against *Valkey server time*, so replicas
cannot disagree and a request refused by pacing burns no budget. That gate is the one
limiter in NOCA that **fails closed** -- on any Valkey failure, not merely an
unreachable server -- because `request_rate_limit`'s fallback is process-local, so an
outage across N replicas would multiply the upstream budget by N, and a `503` that calls
nobody is the safe answer. It also has no off switch: `ARENA_REVERSE_GEOCODER_ENABLED`
disables the proxy itself, and headroom comes from raising the ceilings rather than from
skipping the gate. The provider call itself runs in a worker
thread, since the underlying `NetworkService` is synchronous and would otherwise stall
the event loop for a full upstream round trip.

## Google sign-in

**Google sign-in is Arena's first external identity provider**, and therefore its
first *trust* relationship with a third party rather than merely an outbound call:
an assertion made by Google decides who someone is. It is off by default
(`NOCA_ARENA_GOOGLE_OAUTH_ENABLED`), and while off every `/auth/google` route
answers the same `404` an unknown path does -- except `GET /auth/google/blocked`,
deliberately left reachable so a visitor already refused there for being under 13
can still see why even after the feature is later disabled -- so a deployment
that has not registered an OAuth client is otherwise indistinguishable from one
built before the feature existed. Nothing else in NOCA depends on it: the session
it produces is the same local HS256 JWT the password form produces, logout is
unchanged (Google's session
is not Arena's to end), and `ArenaAuthMiddleware` and the access-control gate see
no difference between the two doors.

The identity trust is deliberately narrow. Google is believed about exactly two things --
the stable `sub` claim and whether it verified the address. The optional name and picture
claims are presentation hints, never authorization evidence.
`arena_user_google_identities.google_sub` is the join key, never the email,
because a Google account's address can change while its subject identifier cannot;
an unverified address is refused outright; and a Google address that matches an
existing Arena account is **not** auto-linked, because possession of an address
would otherwise be enough to take over an account. Linking happens only from an
already-authenticated session, which is what makes "the email need not match" safe.

The matching-address case is the easy one: the callback refuses to create a
second account and tells the visitor to sign in and link from the profile. A
Google address that *differs* from the user's Arena address is indistinguishable,
at the callback, from a genuine new user, so it creates a Google-first account that
holds the subject -- and the real account can then never link it, because the
subject's `UNIQUE` constraint is doing exactly its job. The completion form
therefore offers an "I already have an account" exit
(`arena/routes/auth_google_existing.py`) that changes the rule's *shape* without
weakening it. Leaving the form parks the orphan's id in a time-bounded session
marker (`pending_google_existing_uid`, thirty minutes) and sends the visitor to
the password login, whose page defaults its `next` to a confirmation page while
that marker is live. The claims are not copied anywhere: the orphan's own identity
row holds them. Nothing is hooked into the post-login chain -- the chain is spread
over three routes and the terms gate drops `next`, so the confirmation is simply
where `next` lands once terms, 2FA and a forced password change have all run. It
is an authenticated page that names the Google address, and only its explicit
`POST` re-creates the identity under the signed-in account and deletes the orphan
in one transaction (`google_signup_service.transfer_pending_signup_identity`),
guarded by `describe_pending_google_signup(orphan) == "needs_completion"` so a
completed account is never removed by this path, and deleting with a Core
`DELETE` because the ORM mapping cascades through collections the async session
cannot lazily load. The identity is thus still bound only from an authenticated
session, on an explicit action, so possession of a Google address still takes over
nothing; a marker planted on a shared browser cannot link silently, and starting
Google sign-in again drops it as a statement of a different intent. Declining
leaves the orphan for the Google door to finish as a separate account later. For a
mistaken signup that was already *completed*, the administrator's unlink is the
support escape instead.

That "only from an authenticated session" rule is enforced at **both** ends of the
round trip, and the second half is what makes it hold. `POST /auth/google/link`
records the account to link in a `pending_google_link_uid` session marker, but the
callback never trusts that marker on its own: it re-resolves the request's current
authenticated user and requires an exact match before attaching anything. The
marker lives in the Starlette session, which outlives the JWT cookie that logout
clears, so without that check a session that started a link and then logged out --
or switched accounts -- could attach a Google account to whoever the stale marker
named. Logout deliberately leaves the marker *in place*: it is what makes such a
callback recognizable as a link, and therefore refusable. Clearing it there would
instead make the callback indistinguishable from an ordinary login and send an
unknown Google identity down the signup path, creating a stray second account in
place of a clean refusal. Starting a fresh `GET /auth/google/login` does clear it,
since that is an explicit statement of a different intent.

*When* the callback consumes the marker matters for the same reason. It is read
only after throttle admission and a successful authorization, immediately before
dispatch, because the marker belongs to one OAuth round trip and that round trip
is spent only once Authlib has redeemed its `state`. A callback refused with `429`
never reached Authlib, so its state and code are still valid and the user will
retry them; a marker consumed on entry would have made that retry
indistinguishable from an ordinary login -- the same stray-account path, and this
time for a still-authenticated linker too, since the current-user check only runs
once the callback knows it is a link. A forged callback that fails the state check
likewise spends nothing, so it cannot strip a genuine link attempt of its intent.

What Google cannot assert is what the LGPD age gate and the terms gate need. So an
unknown subject creates an account that is deliberately unusable -- inactive, no
date of birth, terms not accepted -- written in the same transaction as its identity
row, and a completion step collects the rest and applies the same age rules the
ordinary signup applies. An abandoned completion leaves an account that fails
`evaluate_account_access_gates` everywhere, which is the safe direction -- but it is
not a dead end. A visitor who returns through the Google door has just re-proved
control of the same Google account, so the callback resumes the flow where it
stopped rather than answering the generic "deactivated, contact support" message:
back to the completion form when no date of birth was collected, to a waiting page
when a guardian has not yet consented, or to the permanent under-13 refusal.
`google_signup_service.describe_pending_google_signup` decides which, keyed on
`password_is_placeholder` rather than on `ativo` alone, because an ordinary account an
administrator deactivated must still get the generic message and never be swept
into a flow it never went through. Resuming grants nothing the first visit would not
have, and each landing page trusts its own session marker (`pending_google_signup_uid`
to resume the form, `pending_google_consent_uid` for the waiting page) rather than
inferring the stage from account state. The 13-17 outcome and the under-13 outcome
each have a page of their own instead of a flash on a login form the visitor cannot
use: the waiting page renders the same dated consent chronology the guardian pages
render, seen from the student's side, with an IP-throttled resend of its own.

That helper is the reason a second door does not mean a second set of rules.
`arena_auth_service.evaluate_account_access_gates` is the single expression of the
account-state rule (active, date of birth known, LGPD age status) and is consulted
by `efetuar_login`, by the Google callback, and by the per-request dependency;
`auth_common.complete_arena_login` is the single expression of the post-authentication
chain (terms, 2FA, forced password change, token, cookie) and is called by the
password form and the Google callback. Both were extracted from the existing password
path before any Google code was written, so the existing suite proved the extraction
changed nothing. Google login on a 2FA account still requires TOTP -- Google proves
identity, TOTP still proves possession -- and the originating door is carried through
the pending-2FA token so login history records `google_2fa` rather than losing it.

The OIDC flow itself is delegated to **Authlib**, which owns `state`, `nonce`, PKCE,
and ID-token verification against Google's rotating JWKS. PKCE is opt-in there -- a
`code_challenge` is emitted only when the client declares a challenge method -- so the
client registration passes `code_challenge_method=S256` explicitly, and a test builds
the real client and reads the authorization URL rather than assuming the default. It deliberately does not go
through `shared/services/network_utils`: that helper is a synchronous, SSRF-validating
`requests` wrapper for arbitrary operator-supplied URLs, whereas Google's endpoints are
fixed and public and the flow needs async plus JWKS caching. The callback is anonymous
and publicly reachable -- `/auth` is already an allowlisted public prefix, so each
handler gates itself -- and is auth-throttled per client IP exactly as the password form
is, with every refusal answering one generic message so a failure cannot say which half
of the flow it reached.

The optional OIDC `picture` claim is refreshed on successful login only when Google is the
selected avatar source, and immediately when the user selects Google on the profile page.
`google_avatar_service` accepts HTTPS URLs only on Google's profile-image hosts, disables
redirects and environment proxies, enforces the configured upload-size limit while streaming,
then reuses `ImageProcessingService` to decode, validate, and resize the bytes. Arena stores
the processed image locally and the browser continues to request `/user/{id}/avatar`; a
network, status, size, or decode failure is non-fatal and preserves the previous cache and
source. A Google-first signup defaults to Arena until the user explicitly chooses otherwise,
and uploading an Arena photo selects Arena without discarding the Google cache.

A Google-created account holds a **real** Werkzeug hash of a random secret, so
`get_token_id()` and `session_version` are untouched; `arena_users.password_is_placeholder`
is what records that no password can match it. Setting a password later -- through the
ordinary reset flow -- clears the flag in the one setter that owns every password write,
after which both doors work and unlinking Google is permitted. Until then the last-method
guard refuses the unlink, since it would leave the account with no way in at all.

That guard is a *self-service* protection, and the administrator's unlink
(`POST /admin/users/{user_id}/unlink-google`) deliberately does not apply it. The
guard stops a user from locking themselves out by accident; an administrator
detaching a lost, compromised, or mis-attached Google account is doing it on
purpose, and refusing would leave the wrong credential attached with no operator
recourse. What makes the bypass acceptable is that the account keeps the recovery
path the guard was protecting -- the ordinary password reset still works for a
placeholder-password account and sets a real password over it -- so the route says
so in both directions: a warning flash for the administrator and, in the
notification email, the reset link for the user. That justification is also the
bypass's limit. It is false for every *unfinished* Google-first signup
`describe_pending_google_signup` names -- a never-completed row is inactive with no
date of birth, so a freshly set password logs in to the generic "deactivated"
refusal while the Google door can no longer resume it and a fresh signup with the
same address is refused as already registered; a 13-17 account held for consent,
whether an unfinished signup or a completed one whose consent was withdrawn,
reaches the consent flow only through the Google callback; and an under-13
refusal, enforced by the date of birth rather than by the identity row, is
*explained* only through it -- without the row the next Google sign-in ends at
the same dead-end "already registered" message -- so `admin_unlink_refusal`
refuses those
outright, with the reason, and the profile page shows it in place of the control.
The admin path is otherwise no
weaker than the user path: it is password-confirmed, signs the user out (a stripped
credential may be one an attacker holds), and is recorded twice, as an `admin_action`
row and as the same `google_account_unlinked` security event the self-service route
writes, marked `source=admin`. It also ignores `NOCA_ARENA_GOOGLE_OAUTH_ENABLED`,
because the identity row outlives the feature switch and an operator who turned
Google sign-in off must still be able to detach the identities it created.

## Bounded output diff

A failed Arena submission is explained with a **bounded side-by-side diff**, not the
expected output. The submission detail page and the teacher's batch-feedback page
used to render the failing case's whole expected output whether the case was a sample
or a secret, so a Wrong Answer on secret case 7 handed over secret case 7's answer and
repeated wrong submissions walked the secret set one case per submission. Hiding secret
outputs would have been the contest answer; Arena is an educational platform and a bare
"wrong on a secret case" teaches nothing. Instead `arena/services/output_diff.py` shows
the first six differing lines, with one unchanged line of context, between the
student's output and the expected output -- identically for sample and secret cases.
What bounds it is the judge, not the page: the student's side is the
`stdout_excerpt` the judge persisted (at most `NOCA_JUDGE_STDOUT_EXCERPT_BYTES`,
default 8 KB), and the expected side is a fixed 16 KB prefix read through
`read_testcase_output_prefix`, so no request reads an unbounded test file and the
comparison can never reach past the stored excerpt. **Accepted trade-off:** a
determined student can still reveal a secret case's expected output a few lines per
wrong submission, up to the excerpt window and no further. The read happens only for
`WA` and `PE`; every other verdict has no answer to contrast and skips the file
entirely.

## Required announcements

The announcement board itself is a shared table with a per-surface `domain`
column, documented in [ARCHITECTURE_SHARED.md](ARCHITECTURE_SHARED.md). Arena
is the only surface that sets its `required` flag, and Arena alone gives that
flag its meaning.

The `required` flag is given its meaning by `arena_announcement_acknowledgments`:
one row per Arena user per required announcement, composite primary key, absence
meaning pending -- the `clarification_reads` shape -- with both foreign keys
cascading, so deleting an announcement takes its acknowledgments with it (the
#138 decision) and so does deleting the user. Append-only and small, on the
server-wide autovacuum defaults. Existing users are not backfilled: a required
announcement published before they acknowledge it is exactly what they should
see. The pop-up itself is deliberately **not** part of the login redirect chain
(terms, 2FA, forced password change): those redirects fire once, while a
mandatory announcement must come back on every page until it is acknowledged.
It is instead a second app-level dependency beside the authentication gate that
runs only for authenticated HTML `GET` page loads outside `/auth`, reads the
user id from the validated token without loading the user, opens a session only
past that gate, and leaves the oldest pending announcement -- with the pending
count, for the modal's "1 of N" -- on the request for `_base.html` to render as a
static-backdrop modal whose Acknowledge button the checkbox enables. The server
never trusts the modal: an un-acknowledged announcement simply returns on the
next page. The lookup is one query bounded by the number of *required*
announcements (each a primary-key probe into the ledger), not by users or
acknowledgments, which is why no per-user Valkey marker sits in front of it: a
round trip is not cheaper than the query, and invalidating every user's marker
on publication would need a generation scheme for nothing. What *does* sit in
front of it is the user-independent half of the question. On almost every day
in almost every deployment no required announcement exists at all, so
`arena/services/required_announcement_cache.py` remembers that answer per
process for a short TTL (30 seconds, a module constant) over the shared
`SingleFlightCache`, and a page load that finds a live "none" opens no session
and runs no query. The admin publish and delete routes invalidate their own
process **after** commit -- an invalidation before it could be rebuilt from the
pre-commit state and pin the stale answer -- and the TTL bounds how late a
mandatory notice published on another replica reaches this replica's users.
When a required announcement does exist, the per-user query runs exactly as
before. A `POST` that re-renders
a page shows no pop-up for that render; the next `GET` restores it. Because the
`/announcements` prefix is public, the acknowledge `POST` requires the user
itself rather than relying on the global gate.

## Worker pause and resume tables

Authenticated worker pause/resume adds two Arena-owned tables to the shared schema:
`arena_worker_pause_state` (authoritative `paused`/`paused_by` plus a monotonic
per-worker, nonnegative `generation`) and `arena_worker_command_audit` (one row
per issued pause/resume attempt, including rejected and malformed worker-class
requests). The Arena route writes them; the autojudge and aiassistant workers
read pause state through
`shared/services/worker_pause_state.py`.

## Pseudonymous handles and parental consent

Arena user identity carries a pseudonymous handle of its own. `arena_users.username`
(`String(64)`, `UNIQUE`, NOT NULL) is a globally unique lowercase name assigned at signup
from the shared `animal-adjetivo-NNN` generator; `full_name_public` (adult opt-in to publish
the legal name instead), `dta_troca_username` (change cooldown), and `consent_generation`
(the parental-consent epoch counter, following the `session_version` /
`artifact_generation` monotonic-counter idiom) land with it. All four are low-churn and
stay on the server-wide autovacuum defaults.

**The consent epoch is what makes a guardian's withdrawal link safe to hand out.** LGPD
art. 8 §5 grants the parent or legal guardian withdrawal at any time, so Arena mails them a
signed `PARENTAL_CONSENT_REVOKE` token. A bearer token that never expires after use would be
wrong in two ways at once: after a re-grant the original link would revoke again, and after
the guardian address changed the **former** guardian would keep authority. So the token
carries a `gen` claim bound to `consent_generation` at minting time, and the route requires
an exact match. Every transition -- grant, revoke, guardian-email change, date-of-birth
change -- bumps the counter, which is why replay and former-guardian authority are closed by
one mechanism rather than by a used-token table. The route additionally refuses to act
unless `check_age(dob)` still returns `NEEDS_PARENTAL_CONSENT`, so the link goes inert on
its own the morning the child turns 18, needing no scheduler and no stored expiry -- the
same per-request evaluation the shield itself relies on.

Because the epoch invalidates anything minted before the transition it records, the
revocation link can only live in the mail sent **after** a grant commits, never in the
consent invitation: a link minted before the grant fails the consent check while consent is
pending and carries a stale epoch afterwards, so there is no window in which it works. That
makes the confirmation mail load-bearing, which is stated rather than hidden -- a failed
delivery is audited at warning severity, and an administrator is the interim recovery.

The withdrawal itself is one write path (`user_service.revoke_parental_consent`) that the
guardian link and the admin toggle both call, so an administrative revocation cannot be
weaker than the guardian's own -- the defect that motivated this work, where the admin
toggle flipped a flag and left a suspended minor holding a live JWT. It **suspends rather
than erases**, and deliberately does not touch `ranking_visible`: the public ranking already
drops the row through `ativo`, while the affiliation aggregation filters on
`ranking_visible` alone, so clearing it would move a third party's institutional rating as a
side effect of one family's consent decision. Both routes commit the state change and its
security events **before** queueing any mail, and isolate each recipient's delivery, so a
provider refusal can neither unwind a legally effective revocation nor let a queued message
describe one that failed to commit. The `POST` re-resolves the token under a row lock and
mutates inside it, because the epoch check is otherwise a read-then-write that two
simultaneous submissions could both pass.

**Neither consent direction ever mutates on a `GET`.** Both emailed links land on a
review page that renders the decision and writes nothing -- not even throttle
accounting -- and only an explicit guardian `POST` grants or revokes, re-validating its
token at submission time. Mail scanners, preview generators, and prefetchers follow
links in email, so a `GET` that consented (as the grant link originally did) let a robot
consent on a guardian's behalf. The grant `POST` carries the `token_redeem` throttle the
old `GET` held, and refuses an account that has since dropped into the under-13 blocked
band, so a stale link cannot restore consent that a date-of-birth change cleared.

## Canonical usernames

**The canonical form is the stored form.** `normalize_username()` (NFKC → strip → casefold)
canonicalizes at every write path, backed by a plain `UNIQUE` — no functional `lower()`
index and no second canonical column. A functional index reflects poorly through Alembic
autogenerate and adds SQLite test-path friction, while a `username_canonical` column would
be one more place for an invariant to drift. The accepted cost is that a handle has no
display casing. Uniqueness is guaranteed by the constraint, never by the availability
pre-check, which is a TOCTOU: signup redraws and retries inside a savepoint on a clash and
answers `409` only when the retries are exhausted.

The same migration installs `ck_arena_users_public_profile_requires_ranking`
(`NOT (public_profile AND NOT ranking_visible)`), collapsing an invariant the
`public_profile` column comment had always claimed but which lived only in three
application call sites. No equivalent constraint is possible for the *age* half of the
same policy, because age is time-varying and a CHECK is evaluated at write time — which is
precisely why that half needs both a write-path guard and a read-path mask rather than a
database rule.

That migration backfills every existing row **in one step, in Python, inside the
migration** rather than deferring to a script. `username` becomes a public display name the
moment it lands, so a placeholder cleaned up later would leave everyone's public identity as
`user-3f2b1c9d4e6a` between `alembic upgrade head` and an operator remembering to run the
script — permanently if they never do. The repo's usual migration-plus-script shape is for
backfills that are expensive or fallible; drawing a word pair is neither. It reads the word
lists through an **inlined** reader rather than importing
`shared.services.random_username_service`, because a frozen historical migration must not
couple to mutable application code — which obliges it to repeat that module's NFD folding,
since the shipped lists are Title-Case with diacritics and an unfolded read would write
handles that Arena's own validator rejects. An unreadable word list falls back to
id-derived handles rather than bricking the upgrade.

## The age shield

That handle is what Arena actually **publishes**. Every public read path resolves the name it
renders through `arena/services/user_visibility_service.py`, never from `arena_users.nome`:
the display name is the legal name only for an adult who set `full_name_public`, and the
username for everyone else — a 13-17 year-old, an account whose date of birth is unknown
(the shield **fails closed**), and any adult who never opted in. The stored `public_profile`
flag is masked the same way, so a row that predates the rule cannot publish a profile the
shield would refuse, and a shielded user's masked email is withheld entirely, since a masked
address beside an affiliation and a country re-identifies a minor.

The shield is layered on the age oracle in `shared/age_check.py`, which is evaluated per
request. That is what makes it need no scheduler and no stored expiry: a shielded account
stops being shielded on the morning of its eighteenth birthday, and turning 18 only
*unblocks* the opt-in rather than turning a flag on. It deliberately never touches
`ranking_visible` — a minor stays in the ranking, under their pseudonym — which is why
`_eligible_users_where()` carries no age predicate.

The per-request evaluation is exactly why the shield cannot be a read-path rule **alone**,
and the second half is easy to mistake for redundancy. A shielded row that merely *hides* a
stored `public_profile = true` is a row whose profile publishes itself on its owner's
eighteenth birthday: the mask lifts, the stored `True` is honoured, and nobody chose
anything. So the stored flags are refused on the way in — `400 age_shielded` on the user's
own route, and the same refusal in the admin service, since an admin path weaker than the
user path it mirrors is simply a way around the control — and cleared on every transition
into the shielded band. The invariant is that **no shielded row ever persists
`public_profile = true` or `full_name_public = true`**, which is what leaves the read-side
mask with only the cases it can actually cover: a row whose date of birth is unknown, and
whatever path someone adds later. Neither half is sufficient on its own.

The same reasoning is why `NOCA_ARENA_USERNAME_CHANGE_COOLDOWN_DAYS` exists at all. The
handle is the pseudonym the shield publishes *instead* of a name, so unlimited renaming
would let an observer watching the ranking correlate a shielded user's old and new handles
and reconstruct the identity the shield is protecting — the same confirmation-oracle shape
that moved the avatar seed off the email address. An administrator bypasses the cooldown,
because it protects a user from their own churn rather than from a rename made in response
to a report, and that bypass is password-confirmed and audited with both handles named.

Being one rule with two expressions, it follows the **one-shared-predicate** idiom this
codebase already uses for `is_announcement`: a single Python function (`is_shielded`) and a
single SQL mirror (`shielded_users_clause`), living in one module, with a table-driven test
that executes both over the same boundary rows and asserts they agree — so an archive, a
query, and a template cannot classify the same user three ways. The SQL half takes the table
**or an alias** as a parameter, because its one consumer builds candidate-ID branches over
`arena_users.alias(...)`, and binding the predicate to the base table would add an unjoined
FROM element: a cross join carrying an uncorrelated age test.

Search is shielded by a **separate function** rather than a flag.
`identity_search_service.prepare_public_user_search()` suppresses name matching for shielded
users and adds username matching for everyone; `prepare_user_search()` stays untouched for
the teacher-scoped class autocompletes, where looking a student up by the name on the roll
has a legitimate basis. Without the search half the shield would be self-defeating: a page
that renders a pseudonym while still answering "is this real name in the ranking?" is a
confirmation oracle for the very secret it is hiding. The username branches are backed by
`ix_arena_users_username_trgm` (migration `202608310002`), because the `UNIQUE` B-tree the
column already carries serves neither a leading-wildcard `ILIKE` nor the `%` operator. A
username *full-text* branch is deliberately absent — it would need a second expression index
beside the one on `nome`, whose DDL must stay byte-identical to `202608030001`.

One surface has to override stored data rather than pick a column: the problem-statistics
snapshot written by the rating worker freezes `arena_users.nome` as the first- and
last-solver name. Arena re-resolves it at read time in `problem_stats_service`, rather than
changing the snapshot, precisely because that snapshot is written in `shared/` and the shield
is Arena product policy. A solver whose account no longer exists cannot be aged at all, so
its name is withheld rather than taken from the snapshot — the same fail-closed direction as
an unknown date of birth.

## Avatars

Arena's deterministic fallback avatar is seeded on `username` rather than
`email_normalizado`, matching Web. The email seed was a confirmation oracle: the generator
is deterministic and the image is public, so a guessed address could be rendered and
compared against a user's avatar to test mailbox ownership. The reseed changes every
existing user's generated avatar exactly once, which is why it ships in the same release
as the NOT NULL column it depends on.

`arena_users.avatar_revision` is the cache key for the avatar a visitor can currently see.
It advances when an Arena photo is uploaded or removed, the username changes the fallback,
the Google/Arena source preference changes, or a selected Google picture is refreshed. The
canonical `/user/{id}/avatar` route serves the selected locally stored Google thumbnail when
available, otherwise the preserved Arena photo or deterministic fallback. **Every** template
that renders an avatar appends `?v=<avatar_revision>` -- list pages included, whose row DTOs
(`RankedUser`, `TopRatedUser`, `AdminSubmissionListRow`, `ClassMemberManagementRow`, the two
class-report rows) carry the column for exactly that purpose. This is not cosmetic: a
versioned URL gets the configured public cache lifetime, while an unversioned one is
answered `max-age=0, must-revalidate`. Until #199, the shared image helper emitted no
`ETag`, so "revalidate" meant "download again": a list page that omitted the version turned
the Arena ranking into up to a hundred full avatar fetches per view, each holding a pool
connection. Two fixes, deliberately both: every shipped template now appends the version,
and `tests/arena/test_arena_avatar_url_versioning.py` fails the suite if one stops; and the
helper now stamps a strong content-derived `ETag` on every image response and answers a
matching `If-None-Match` with a bodyless `304`, so the unversioned branch — still the safety
net for a caller that forgets — costs a header exchange rather than the image. The tag is
derived from the bytes rather than supplied by the caller because only the avatar route has
a revision to offer; affiliation logos have nothing to version by, and a content tag is
correct for every caller and cannot go stale when an avatar source switches.

## Signup reputation

Arena signup reputation adds the `arena_user_reputation` table to the shared schema:
one row per Arena user (unique `user_id` FK) holding the client IP captured at signup
plus the IPQualityScore IP and email reputation reports (fraud scores as columns and the
full signals as JSON). The signup IP is recorded for every account even when the
IPQualityScore integration is disabled, so `scripts/backfill_email_reputation.py` can
later score both the email and any recorded signup IP. The Arena HTTP process owns the
writes (a post-signup background task through `arena.services.signup_reputation_service`,
which also emails every `ARENA_ADMIN` a report); the Arena admin user profile reads the
snapshot on its Reputation tab.

## Google identity rows

Arena's Google sign-in adds `arena_user_google_identities`, a 1:1 satellite of
`arena_users` modelled on `arena_user_reputation`. Two UNIQUE constraints state the
whole invariant: an Arena account has at most one Google identity (`user_id`) and a
Google identity belongs to at most one Arena account (`google_sub`). `google_sub` --
Google's stable subject claim -- is the join key, never `google_email`, which is
stored for display only because a Google account's address can change while its
subject identifier cannot. The same row stores the latest optional `picture` claim,
a validated local avatar cache, its MIME type and refresh time, and the user's explicit
Arena/Google source preference. The Arena photo remains untouched while Google is selected,
so switching back or unlinking is lossless. Uniqueness is enforced by those constraints alone, never
by a lookup-then-insert, which would be a TOCTOU; the resulting `IntegrityError` is
presented as a stated conflict. There is deliberately **no** separate index on
`user_id`: the UNIQUE constraint already provides one over exactly that column. The
FK cascades, so deleting an account takes its identity and Google-derived cache with it.
Metadata is written on link, unlink, preference changes, and Google login; image bytes change
only after a bounded successful refresh. This remains low-churn account metadata on the
server-wide autovacuum defaults, so the migration deliberately adds no per-table tuning.

The same migration adds `arena_users.password_is_placeholder` (`Boolean`, NOT NULL,
server default `false`) and widens the `arena_login_history.mode` comment to name
`google`, `google_2fa`, and `google_backup_code`. The flag exists because the
unusable-random-password decision leaves nothing to *test*: a random Werkzeug hash is
indistinguishable from a real one, so the last-method guard and the skip-forced-password-change
branch would have no predicate. It is set only by a Google-first signup and cleared by
the `ArenaUser.password` setter -- the single path in the codebase that writes
`password_hash` -- so setting a real password self-corrects it with no caller obliged
to remember. `ArenaUser.has_usable_password` is simply its negation. Backfilling
`false` is correct rather than merely convenient: every account predating the column
was created by a path that wrote a real password.
