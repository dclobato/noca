# Changelog

Todas as mudanças relevantes deste projeto são documentadas aqui.
O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e o projeto adota o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [19.0.0] - 2026-09-04

### ⚠ Breaking Changes

- **web:** Rate-limit the anonymous problem-set download and live feed snapshot

  in production (NOCA_ENVIRONMENT=production) GET
  /problem-set/{slug}.zip now answers 503 while
  NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH is unset, instead of rebuilding the
  archive per request. Set the cache directory before deploying if released
  problem sets are served; development keeps the per-request rebuild.
- **shared,web,arena:** Route every outbound email through an async, budgeted service with queue delivery

  NOCA_EMAIL_DELIVERY defaults to queue. A production install
  with NOCA_SEND_EMAIL=true and an SMTP provider must deploy the noca-mailer
  worker (next commit) or set NOCA_EMAIL_DELIVERY=direct; with queue and no
  worker, email accumulates in Valkey until the job TTL drops it unsent.
  EmailService.send_email is now a coroutine and every email wrapper service
  in web/ and arena/ is async; callers must await them.
  
  Part of #155
- **mailer,shared:** Make the mailer the only sender and settle the queue's failure semantics

  Web and Arena no longer read NOCA_SEND_EMAIL,
  NOCA_EMAIL_PROVIDER, NOCA_SMTP_*, NOCA_EMAIL_MBOX_LOG_DIR or
  NOCA_EMAIL_DELIVERY; move them to the noca-mailer worker, which every
  deployment -- including development -- must now run: Web and Arena fail
  to start without a live mailer. NOCA_EMAIL_DELIVERY is removed.
- **arena:** Throttle every admin password re-confirmation oracle

  five wrong passwords on any admin confirmation form now
  lock the acting account out of every password-confirmed Arena route for
  NOCA_ARENA_AUTH_RATE_LIMIT_LOCKOUT_SECONDS (default 15 min). No
  configuration change is required.
- **arena:** Close the anonymous token oracles on the reset, activation and consent links

  five rejected activation or parental-consent tokens for
  one account lock further bad tokens for that account for the auth
  lockout window; valid links are unaffected. An expired reset link now
  shows the new-password form and fails on submit instead of up front.
- **sse:** Size the connection leases for a venue and surface a refused stream

  NOCA_WEB_SSE_MAX_PER_IP and NOCA_ARENA_SSE_MAX_PER_IP
  default to 200 (was 10), NOCA_WEB_SSE_MAX_PER_USER and
  NOCA_ARENA_SSE_MAX_PER_USER to 10 (was 5), NOCA_ANIMATOR_SSE_MAX_PER_IP
  to 100 (was 10). Deployments that pinned the old values keep them;
  lower the new defaults only if the host cannot hold that many open
  connections.
- **arena:** Withhold a problem's difficulty until five people have attempted it

  Arena surfaces no longer display a difficulty for a
  problem with fewer than 5 unique attempters; they show a dash labelled
  "Not enough data yet" instead of 5.0. GET /help/rating/difficulty-distribution
  now counts measured problems only and carries two new keys,
  `unmeasured_problems` and `min_attempts`; the stored snapshot switches on
  the next rating cycle. No configuration change is required.
- **arena:** Seed a problem's difficulty from an author-declared estimate

  a new schema migration (202608300001) adds
  arena_problems.expected_difficulty; Web and Arena entrypoints apply it on
  start, and workers wait for it as usual. Once authors set estimates, the
  stored rating (and therefore user points) of a never-attempted problem is
  its declaration instead of 50, and its difficulty is displayed as "7.0?"
  rather than a dash. Exported problem packages carry one new key,
  `expected_difficulty`, which older readers ignore. No configuration change
  is required.
- **arena,web,shared:** Autosave problem-editor drafts so a bounced Save never loses work

  an unauthenticated request under /c/{slug}/ now
  redirects to /c/{slug}/login?next=<path> rather than the bare login URL,
  and a successful contest login redirects to that page instead of always
  to /c/{slug}. Clients asserting the exact Location of either redirect
  must be updated.
- **scoreboard:** Halve the row height across all three surfaces (#133)

  the animator no longer serves `star_base` to its templates or
  `data-star-base` to its clients, and `AnimatorRender` no longer exports
  `createSolvedImage` or `createStarImage` (replaced by `createFirstMark`). The
  `/assets/star/{color}` route itself is unchanged and still served. Deploy the
  animator's templates, routes and static JS together.
- **arena:** Give every Arena account a pseudonymous username (#176)

  every existing Arena user's generated fallback avatar changes
  once. It is now seeded on the username rather than the email address, closing a
  confirmation oracle -- the generator is deterministic and the image is public,
  so an email seed let anyone render a guessed address and compare it against a
  user's avatar to test whether that person holds that mailbox. Web has always
  seeded on its username. Uploaded photos are unaffected.
- **arena:** Pseudonymize every public read path (#177)

  Arena public surfaces no longer display a user's legal name.
  The dashboard leaderboard, both ranking pages, the public profile page, and the
  problem statistics solver credits now show the pseudonymous `username` for
  every user; the legal name appears only for an adult who has set
  `full_name_public`, and no user can set it until #178 ships the opt-in, so in
  practice every name on those pages changes to a handle on deploy. Public
  ranking search no longer matches an age-shielded user by their real name.
  Public profiles of users aged 13-17 and of users with no recorded date of birth
  now answer 404, including any that was reachable before; an adult's profile is
  unaffected and simply shows their handle. Teacher-scoped class searches and
  admin surfaces are unchanged.
- **arena:** Enforce the minor shield on every write path (#178)

  enabling a public profile for an age-shielded Arena account
  (under 18, or no date of birth on record) is now refused on both the user's own
  path and the admin path. `POST /user/profile/personal-data` gains a 400
  `age_shielded` rejection and three response fields (`full_name_public`,
  `age_shielded`, alongside the existing flags), and
  `POST /admin/users/{id}/toggle-public-profile` refuses to enable for such a
  target. Administrators can no longer publish a minor's profile page.
- **arena:** Make the username surfaces usable and honest (#178)

  a new Arena account aged 18 or over is created with
  `full_name_public = true`, publishing the member's real name rather than their
  handle, and the username migration backfills that flag for existing adults.
  Accounts aged 13-17, and any account with no recorded date of birth, are
  unaffected and remain pseudonymous. `POST /admin/users/{id}/change-username`
  accepts a new optional `allow_immediate_change` field.
- **arena:** Let a guardian withdraw parental consent (#179)

  POST /admin/users/{id}/toggle-parental-consent now requires a
  confirm_password field and refuses the request without it, and its revoke
  branch deactivates the account and invalidates its sessions rather than only
  clearing the consent flags. Any automation posting to that endpoint must be
  updated.
- **arena:** Require explicit POST confirmation for parental consent (#180)

  GET /auth/parental-consent no longer grants consent or
  activates an account; it renders a review page, and the grant happens only on
  the new POST /auth/parental-consent with the token in the form body. Any
  automation redeeming parental-consent tokens through the GET must be updated
  to submit the POST.
- **arena:** Rate-limit the anonymous problem search and require a real search term

  a non-blank search term shorter than 3 characters (or longer
  than 100, 64 on an autocomplete) now answers 422 on GET /problems, the ranking
  pages and every autocomplete, and Web's GET /categories/autocomplete requires
  q. Anonymous problem searches are refused with 429 after 30 per client IP per
  minute; a venue behind one NAT address can raise
  NOCA_ARENA_PUBLIC_RATE_LIMIT_PROBLEM_SEARCH_MAX_REQUESTS or list its egress
  range in NOCA_ARENA_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS.
- **web:** Key contest-login lockouts on the contest, not the bare username

  the web/contest-login throttle bucket is now keyed on
- **judge:** Migrate all judge images to Debian 13 (trixie)

  OCAMLOPT_PATH moves from /usr/local/bin/ocamlopt to
  /usr/bin/ocamlopt. languages.compile_cmd is a persisted column, so an existing
  install must run `uv run python scripts/bootstrap_languages.py` as part of the
  same deploy or every OCaml submission fails at exec. Contestant-visible compiler
  versions change for six languages; Auto-Limit profiling must be re-run.
- **app-base:** Move the service image base to Debian 13 (trixie)
- **config:** Split .env.full into per-service environment layers

  `.env.full` no longer exists. A container deployment copies the
  templates its services need and lists each stack in `env_file:` (see
  docs/ENV_LAYERS.md); a single-host development install can concatenate them into
  one `.env`, as docs/BOOTSTRAP.md now shows. NOCA_DATA_ROOT must stay in the
  project-root `.env`, since Compose reads that file to interpolate the Compose
  file itself and no env_file layer can satisfy it.
- **web:** Cache the team-reachable problem export and statement downloads

  Contest backup archives written before format version 5 can no
  longer be restored. Restore them with the release that wrote them, or re-export
  each contest from a server still running that release before upgrading. Web also
  now refuses to start in production when `NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH` is
- **arena:** Cache the public problem export and sample ZIP with #152's solution

  Arena refuses to start in production when
  `NOCA_ARENA_PUBLIC_PROBLEM_PACK_PATH` is unset, mirroring Web. A package build
  per request reachable by every logged-in user is never acceptable there, and
  failing the deploy beats a 503 discovered later. Set the variable and mount
  its directory before upgrading.

### Features

- **arena:** Extend the per-problem statistics snapshot
- **arena:** Match each suggestion term independently
- **arena:** Stamp last update and hint gated editorials
- **shared:** Generic per-IP fixed-window rate-limit dependency
- **healthmonitor:** Cache, pipeline, and rate-limit the public routes
- **animator:** Cache the public feeds and reveal dataset, rate-limit the anonymous routes
- **animator:** Decide team-media 304s from metadata, rate-limit the media routes
- **shared:** Cap concurrent SSE connections per IP and per user on every stream
- **[BREAKING]** **web:** Rate-limit the anonymous problem-set download and live feed snapshot
- **web:** Throttle per-team SOS, print and clarification writes
- **arena:** Throttle, cache and de-block the reverse-geocoder proxy
- **[BREAKING]** **shared,web,arena:** Route every outbound email through an async, budgeted service with queue delivery
- **mailer:** Add the outbound-email worker that drains the mail queue
- **mailer:** Record queue time and delivery attempt on the mbox audit copy
- **mailer:** State the delivery mode and SMTP relay in the startup banner
- **arena:** Show solvers, attempts and a yearly submission heatmap on the problem statistics page (#129)
- **[BREAKING]** **sse:** Size the connection leases for a venue and surface a refused stream
- **arena:** Illustrate the lockout page from a shared image mount
- **[BREAKING]** **arena:** Withhold a problem's difficulty until five people have attempted it
- **[BREAKING]** **arena:** Seed a problem's difficulty from an author-declared estimate
- **web:** Render PRINT tasks as a delivery sheet with the source listing
- **[BREAKING]** **arena,web,shared:** Autosave problem-editor drafts so a bounced Save never loses work
- **animator:** Cue a team's media onto the projectors from the remote (#161)
- **animator:** Report the projector count to the controller on every heartbeat
- **[BREAKING]** **scoreboard:** Halve the row height across all three surfaces (#133)
- **contest:** Show solved balloons on team dashboard
- **assets:** Add Brazilian national and state flags
- **arena:** Show Brazilian state flags across views
- **rating:** Precompute affiliation solve totals
- **arena:** Make affiliation ranking rows clickable
- **shared:** Add random pseudonymous username generator (#121)
- **[BREAKING]** **arena:** Give every Arena account a pseudonymous username (#176)
- **[BREAKING]** **arena:** Pseudonymize every public read path (#177)
- **[BREAKING]** **arena:** Enforce the minor shield on every write path (#178)
- **[BREAKING]** **arena:** Make the username surfaces usable and honest (#178)
- **[BREAKING]** **arena:** Let a guardian withdraw parental consent (#179)
- **[BREAKING]** **arena:** Require explicit POST confirmation for parental consent (#180)
- **arena:** Shape the parental-consent surfaces (#121)
- **arena:** Let an admin require everyone to accept the terms again
- **web:** Add the platform announcement board (shared foundation + Web)
- **arena:** Bring the announcement board to Arena
- **arena:** Make required announcements pop up until acknowledged
- **[BREAKING]** **arena:** Rate-limit the anonymous problem search and require a real search term
- **arena:** Add "Login with Google" as an alternative door (#24)
- **arena:** Finish the Google-first onboarding arc (#24)
- **arena:** Add Google avatar selection
- **arena:** Let an admin unlink a user's Google account
- **arena:** Let a mismatched-email Google signup fold into the existing account
- **web,arena:** Let an admin lift a sign-in lockout early (#188)
- **web,arena:** Loose per-actor ceilings on the polled reads
- **[BREAKING]** **web:** Cache the team-reachable problem export and statement downloads
- **web:** Add Uberadmin return action to contest navbar
- **shared:** Answer 304 on image routes with a content-derived ETag
- **[BREAKING]** **arena:** Cache the public problem export and sample ZIP with #152's solution
- **animator:** Seed the scoreboard activity rail from the snapshot
- **web:** Expand contest reports with Highlights, Performance, and Problem Race
- **web:** Rework the contest reports page layout and Performance chart

### Bug Fixes

- **badges:** Rank CLEAN_CODE honestly and revoke it when it no longer holds
- **shared:** Rank by last accepted solve and truncate ICPC minutes
- **shared:** Outrank EasyMDE's preview table styles
- **arena:** Give sample test-case blocks their own surface
- **arena:** Render problem-form suggestions in an own listbox
- **arena:** Require three word characters per suggestion term
- **arena:** Count every signup attempt per IP and never reset on success
- **arena:** Throttle password/TOTP verification and pending-flow writes
- **arena:** Stop re-enqueueing AI review jobs on repeat requests, cap requests per user
- **web:** Throttle the five password-confirmation oracles
- **animator:** Lock out an IP after repeated operator-token failures
- **arena:** Stop the geocoder proxy from describing its own configuration
- **web,arena:** Make the mass rejudge actions confirmed, idempotent, cooled down and audited
- **arena:** Stop relaying the Referer verbatim from the two rejudge redirects
- **[BREAKING]** **mailer,shared:** Make the mailer the only sender and settle the queue's failure semantics
- **arena:** Make the problem statistics charts accessible and selectable for every year (#129)
- **arena:** Derive the first-AC submission from judgments, not solved_at
- **[BREAKING]** **arena:** Throttle every admin password re-confirmation oracle
- **[BREAKING]** **arena:** Close the anonymous token oracles on the reset, activation and consent links
- **arena:** Show an estimated difficulty as plain text, sized like the bar label
- **animator:** Keep the projector viewport from scrolling into phantom overflow
- **animator:** Fence the media cue on ownership, and reset its label on movement
- **animator:** Stop pretending an ended contest is live
- **arena:** Remove login-time profile completeness gate
- **build:** Copy Brazilian flag asset helper
- **arena:** Type the username migration's cutoff bind (#176)
- **migrations:** Reconcile affiliation solve insertion
- **tests:** Seed the PostgreSQL ranking search for the username shield
- **arena:** Enable PKCE on the Google client, and make the suite hermetic (#24)
- **arena:** Close Google link session-hijack and stranded-guardian-email holes
- **arena:** Keep the Google link marker across logout so the callback can refuse it
- **arena:** Consume the Google link marker only once the OAuth round trip is redeemed
- **arena:** Refuse the admin Google unlink for an unfinished Google-first signup
- **arena:** State the admin Google unlink's consequences accurately
- **arena:** Spend the signup budget on work, and the 2FA IP lock on spraying
- **rate-limit:** Preserve writes and spray protections
- **auth:** Clear an address's spray evidence when its lockout is lifted
- **[BREAKING]** **web:** Key contest-login lockouts on the contest, not the bare username
- **schema:** Match the avatar_revision comment to the database
- **web:** Reuse request session for user media
- **web:** Show problem author on detail page
- **arena:** Version every per-row avatar URL so list pages hit the browser cache
- **web,arena:** Use the dependency's session on 22 admin routes instead of opening a second
- **arena:** Explain a failed case with a bounded side-by-side diff, not the secret answer
- **arena:** Match any selected problem category
- **editor:** Render Markdown syntax literally in the EasyMDE source pane

### UI & Design

- **tests:** Reformat the EasyMDE preview-style guard
- **mailer:** Render the startup banner in the Doom figlet font like every other module
- **arena:** Apply Ruff session formatting

### Performance

- **rating:** Rebuild Arena problem statistics in bounded per-problem batches (#136)
- **static:** Ship the four page illustrations as WebP
- **arena:** Skip the mandatory-announcement query when nothing is required

### Refactoring

- **arena:** Make the problem statistics page graphical only
- **arena:** Split ArenaUser behavior into mixins
- **arena:** Share the admin password re-confirmation helper
- **arena:** Move linked accounts to profile tab

### Documentation

- Drop the generated backlog index in favor of Gitea directly
- **config:** Explain how the *_TRUSTED_CIDRS lists behave
- **arena:** Stop calling login-gated routes public in ROUTES.md, and guard it with a test
- **arena:** Walk through the Google Cloud setup for Arena sign-in (#24)
- **arena:** Reconcile remaining "every /auth/google route 404s" claims
- Update terms of use and usage policy
- **web:** Bound the export cache generation, and correct a tamper claim

### Build & CI

- Key the uv cache on uv.lock and document the runner cache prerequisite
- Keep downloaded wheels in the uv cache (prune-cache: false)
- Mint a new uv cache key so the unpruned cache can be saved
- **[BREAKING]** **judge:** Migrate all judge images to Debian 13 (trixie)
- **ci:** Add a registry build cache to the Bake publish path
- **[BREAKING]** **app-base:** Move the service image base to Debian 13 (trixie)
- **[BREAKING]** **config:** Split .env.full into per-service environment layers



## [18.0.1] - 2026-08-26

### Bug Fixes

- **arena:** Slide every authenticated session, not just remember-me
- **web:** Keep an open Web page's session alive
- **config:** Refuse a keepalive cadence that would never rotate a session

### Refactoring

- **arena:** One definition of what templates may read

### Documentation

- **changelog:** Record the animator breaking change in v18.0.0



## [18.0.0] - 2026-08-25

### Breaking Changes

- **animator:** `jump_pending` widens the `RevealCommand` literal without bumping
  `state_version` (3) or `event_version` (1). Animator replicas must be deployed
  together: a stale replica drops an unknown event nudge and, after the first
  idempotency-keyed `jump_pending`, rejects the whole stored session because it
  cannot validate the receipt. Recover after a rollback with **Rebuild state**
  (`start-reveal` with `restart=true`), which replaces the payload without
  reading it.

### Features

- **web:** Move the theme control into the application chrome
- **web:** Colour the contest countdown by remaining-time urgency
- **animator:** Enforce one active reveal controller per ceremony scope
- **scripts:** Add Arena category bulk-edit and recolor tools
- **animator:** Show team photos on scoreboards
- **animator:** Add jump to next pending control
- **animator-remote:** Allow cleartext HTTP in release builds too

### Bug Fixes

- **valkey:** Isolate test pubsub channels
- **animator:** Remove vendor footer links
- **animator:** Withhold the problem set before the contest starts
- **animator-remote:** Copy sources into the Kotlin test container
- **animator:** Close controller-lease panel dead ends from review
- **animator:** Make lease renewal retries actually reach the server
- **animator:** Make the scoreboard live badge a solid, calmer dot
- **shared:** Shield LaTeX math from Marked's backslash-escape rule
- **web,arena:** Make data-confirm work on every page, not just judgment ones
- **web,shared:** Notify teams about judge/admin announcements
- **web:** Align announcement notifications with the list's visibility rule

### Documentation

- Record real_docker's CI exclusion as a choice, not a limit

### Build & CI

- **animator-remote:** Build the APK in a container, and sign the release



## [17.4.0] - 2026-08-22

### Features

- **web:** Decouple problem-set release from scoreboard release
- **web:** Offer the problem-set archive on the contest dashboard
- **web:** Let uberadmins arm the problem-set release at contest creation
- **packages:** Declare an editorial release policy in the sample package
- **web:** Audit the final-scoreboard release
- **web:** Redesign /contests as an animator-style gateway page
- **web:** Redesign contest login as a two-pane identity/form shell
- **web:** Redesign uberadmin dashboard and shared header/footer
- **web:** Warn-tone Administration card and shared back-to-top button
- **web:** Bolder scoreboard identity, two-line team names
- **web:** Redesign participant problem list as card grid with solving rate
- **web:** Confetti on problem list when a team's own solve lands live
- **web:** Add 'Accepted languages' eyebrow on contest login
- **web:** Bolder identity banner on contest dashboard
- **web:** Mirror dashboard role pill on navbar
- **web:** Overhaul contest reports page hierarchy, theming, and copy
- **web:** Streamline contest setup forms
- **web:** Notify teams when their clarification is answered
- **web:** Let chief judge and admins announce anytime
- **web:** Give the contest navbar real navigation and responsive behavior
- **web:** Surface contest phase in the clock and post the logout
- **web:** Secure animator disable workflow
- **web:** Pin navbar, contest nav and breadcrumb to the top
- **web:** Let teams read clarifications before the start
- **web:** Extract the role matrices into web/access_matrix
- **web:** Show the role reference on batch user import
- **web:** Show contest rules summary on the dashboard banner
- **web:** Show contest rules summary on the dashboard banner

### Bug Fixes

- **skills:** Keep Markdown headings in release tag bodies
- **docker:** Bound Valkey memory explicitly instead of relying on mem_limit
- **web:** Keep uberadmin contest creation working with the new release flag
- **web:** Show a single read-only balloon when problem editing is locked
- **web:** Address PR #34 review improvements and nitpicks
- **autojudge:** Align interactive solution-test diagnostics with submission attempts
- **web:** Suppress the validator's stale clean verdict on limit-enforced cases
- **web:** Harden UberAdmin login experience
- **web:** Harden and streamline login
- **web:** Redesign dashboard cards, dedupe admin breadcrumbs, fix link contrast
- **web:** Drop team photo link/modal from scoreboard
- **web:** Enable slashed-zero variant on remaining mono-family selectors
- **web:** Uniform row height on contest Runs table
- **web:** Monospace username row on scoreboard
- **web:** Make breadcrumb bar always span full viewport width
- **web:** Route UberAdmin contest 'Administration' tile to /c/{slug}
- **accessibility:** Hide decorative icon ligatures
- **accessibility:** Address review findings on icon ligature PR
- **shared:** Reconcile schema metadata drift
- **web:** Give the cropper preview a real placeholder src
- **web:** Make the breadcrumb bar's padding the same on every page
- **web:** Mark the current contest section by path, not endpoint name
- **web:** Fix contrast and naming defects in the contest navigation
- **web:** Restore the contest name's style rule in the navbar
- **web:** Complete accessible navbar account menu
- **web:** Avoid template permission global collisions
- **web:** Use aware UTC scoreboard timestamps
- **web:** Retain uberadmin profile return action
- **web:** Harden contest navigation state and assets
- **web:** Align user navigation with access matrix
- **web:** Redirect forbidden roles to dashboards
- **web:** Address sticky chrome review findings
- **web:** Address dashboard rules review findings
- **web:** Address PR review on the role matrices

### UI & Design

- **web:** Centre the clock in a taller navbar

### Refactoring

- **web:** Give the problem-set publication control its own section
- **web:** Extract a POST-action dashboard card macro
- **web:** Convert the problem-set release cards to the shared macro
- **web:** Remove redundant Detail button on clarifications row
- **web:** Address PR #74 review on chief-judge authority
- **shared:** Extract Arena's SSE+poll submission-status core
- **web:** Give role identity one source of truth
- **web:** Move contest navigation into its own band below the navbar
- **web:** Drop the breadcrumb where the navigation band already says it
- **web:** Centralize navbar permission checks
- **web:** Reuse permission predicates across templates
- **web:** Remove redundant profile back action
- **web:** Isolate accessible contest chrome styles

### Documentation

- **backlog:** Regenerate index
- **animator:** Move the implementation phases into Gitea issues
- **problem-editor:** Move the execution phases into Gitea issues
- **backlog:** Regenerate index
- **web:** Document contest navigation and POST logout
- **web:** Document contest clock phase boundaries
- **web:** Finalize navbar guidance
- **web:** Reword role reference notes for contest admins

### Other

- **web:** Shared tabular class, narrower mobile chrome, band scroll-into-view



## [17.3.0] - 2026-08-19

### Features

- **docker:** Add healthchecks for arena, rating, and aiassistant

### Bug Fixes

- **tooling:** Make `mypy .` check the project's typed surface
- **static:** Revalidate CSS and JS instead of caching them for five minutes
- **tables:** Stop responsive table wrappers from scrolling vertically
- **scripts:** Skip Markdown headings when summarising backlog issues

### Documentation

- **changelog:** Give UI work its own changelog section



## [17.2.1] - 2026-08-19

### Bug Fixes

- **ci:** Skip CI workflow on version tag pushes
- **arena:** Render LaTeX equations on problem editorial viewer
- **arena:** Remove stale not-enforced note on editorial release policy
- **arena:** Carry the editorial release policy through problem packages
- **ci:** Restore CI on branch pushes

### UI & Design

- **design-system:** Tokenize every hardcoded color outside the palette
- **design-system:** Add a shared spacing scale and widen Markdown block gaps
- **problem-editor:** Give both editor doors one shared header and shape

### Refactoring

- **shared:** Unify all Markdown and LaTeX rendering into one pipeline

### Documentation

- **backlog:** Record the `table-caption` Markdown directive idea

## [17.2.0] - 2026-08-18

### Features

- **arena:** Split problem-definition save into enable/disable actions
- **problems:** Add optional editorials to Arena and Web
- **arena:** Add editorial release policy to problem editor
- **arena:** Merge TC columns and add editorial column/filter to admin problem list
- **arena:** Gate editorial visibility on the problem detail page
- **web:** Add public post-contest problem-set archive download

### Bug Fixes

- **arena:** Gate the definition Save's enable on the target state
- **arena:** Fix scrollbar jitter and column alignment on problem set report
- **ci:** Drop host port bindings from CI service containers

### Documentation

- Add PDF and server-side math rendering evaluation to backlog
- Split immutable-validation-strategy backlog item into done/remaining
- Add ROADMAP.md and problem-set archive follow-ups to backlog



## [17.1.0] - 2026-08-15

### Features

- **problems:** [phase 2] store the validation strategy explicitly
- **packages:** [phase 3] adopt problem-package format version 2
- **backup:** [phase 3] adopt contest backup format version 2
- **problem-package:** [phase 4] add an edit-aware artifact swap with a generation fence
- **problems:** [phase 5] choose the validation strategy before creating
- **problems:** [phase 5] make the problem editor tabbed and strategy-aware
- **problems:** [phase 6] fold every editor mutation into one Save
- **problems:** Give every test-case row its own delete endpoint
- **problems:** Give Contest judgment data its own pages
- **problems:** Give Arena judgment data its own pages
- **problems:** Narrow both editors to the problem definition
- **problems:** Collect only the definition when creating a problem
- **shared:** Strengthen validator choice cards
- **arena:** Autocomplete problem licenses
- **arena:** Add enabled/disabled filter to admin problem list
- **arena:** Give the dashboard a real heading and leaderboard identity
- **arena:** Polish the public problem list and statistics pages
- **arena:** Show solved problem count on user ranking
- **arena:** Allow downloading a submission's source code
- **security-events:** Add a full-log CSV export to both viewers
- **arena:** Rebuild the help surface as a verdict board

### Bug Fixes

- **judging:** [phase 2] decide every behavior from the stored strategy
- **migrations:** [phase 2] restack the validator_type migration onto master
- **problem-package:** [phase 4] close two data-loss windows in the edit-aware swap
- **problems:** [phase 5] repair editor interactions the tab rewrite broke
- **problems:** [phase 5] render the statement and align the explanation column
- **problems:** Make every per-action test-case route durable
- **problems:** Restore the Arena chrome on the judgment-data pages
- **problems:** Close four durability holes in the editor's save path
- **problems:** Finish the split — races, redirects, warnings, and the dead pipeline
- **problem-editor:** Harden judgment actions and recovery
- **problem-editor:** Harden authoring workflows
- **arena:** Remove underline from dashboard card links on hover
- **arena:** Match dashboard latest-problems row links to problem list
- **arena:** Remove underline from remaining quiet shell links
- **security-events:** Record the actor's login, not just an opaque id
- **arena:** Preserve problem-list state across the definition/judgment editors

### Refactoring

- **problems:** Delete the client-side pending model

### Documentation

- **problem-editor:** [phase 1] add the tabbed problem editor plan
- **shared-services:** [phase 5] describe the shared validator status badge
- **problem-editor:** [phase 5] correct the claim that inline rows survive a failure
- **problem-editor:** Describe the two editors and what a data root must support



## [17.0.1] - 2026-08-12

### Bug Fixes

- **autojudge:** Attribute interactive watchdog stalls
- **ci:** Serialize and harden image publishing



## [17.0.0] - 2026-08-12

### ⚠ Breaking Changes

- **healthmonitor:** Redesign uptime dashboard

  The Health Monitor dashboard moves from /dashboard to /.
  The old /dashboard route and separate status page are removed.

### Features

- **deploy:** Reject methods outside GET/HEAD/POST at the Caddy edge
- **security:** Stop leaking the origin stack and cover every HTTP module
- **security:** Stop naming the stack in error bodies and bound integer inputs
- **[BREAKING]** **healthmonitor:** Redesign uptime dashboard
- **landingpage:** Build the environment overview page

### Bug Fixes

- **autojudge:** Validate lock TTL during settings load
- **ci:** Restore warning-free full test suite
- **landingpage:** Serve /favicon.ico at the site root

### Documentation

- **custom-validator:** Define output checker contract
- Sync bootstrap and contest report docs with codebase
- Sync documentation with current codebase behavior

### Ci

- Add manual single-app image publish workflow



## [16.0.0] - 2026-08-09

### ⚠ Breaking Changes

- **problem-package:** Unify import and export contracts

  Problem-level output limits are now required positive values and
  the problems.output_limit_in_bytes column is NOT NULL. Package imports reject
  explicit null limits and malformed or logically ambiguous members that older paths
  could accept. Apply migration 202608080001 before starting the updated services.

### Features

- **arena:** Polish live feed and standardize link styles
- **arena:** Configurable medal cutoffs on ranking pages
- **animator:** Contest-wide medal cutoffs
- **arena:** Migrate remaining autocompletes to indexed search
- **[BREAKING]** **problem-package:** Unify import and export contracts
- **typography:** Adopt IBM Plex Mono across NOCA
- **arena:** Pair slug and color on one row in category modals
- **arena:** Stream submission detail verdict updates

### Bug Fixes

- **tests:** Isolate xdist filesystem storage



## [15.2.0] - 2026-08-04

### Features

- **arena:** Overhaul UI and authentication flows
- **arena:** Back ranking and student search with FTS and trigram indexes
- **arena:** Refine class management UI
- **arena:** Refine submission detail result view
- **arena:** Polish report, ranking, and auth UI
- **arena:** Polish batch feedback review layout
- **arena:** Add class-wide problem-set report
- **arena:** Align profile completion with auth flow

### Bug Fixes

- **arena:** Preserve report context on submission details
- **arena:** Count each submission once in problem-set reports
- **arena:** Show problem link underlines on hover
- **arena:** Prevent class detail table overflow



## [15.1.0] - 2026-08-02

### Features

- **aiassistant:** Display model name on worker startup
- **arena:** Record and filter the problem statement language
- **scripts:** Add container image retention cleanup for Docker Hub and GHCR
- **ci:** Let the language publish target one registry or both
- **ci:** Retry publishes, verify released tags, and select registries
- **arena:** Replace problem search ILIKE with full-text search
- **arena:** Autocomplete problem authors and sources

### Bug Fixes

- **ci:** Publish one registry per pass and drop judge provenance

### Performance

- **arena:** Optimize problem list queries



## [15.0.1] - 2026-08-01

### Features

- **animator:** Add an Android reveal-ceremony operator remote
- **animator-remote:** Derive the APK version from the workspace version
- **presence:** Prune stale worker-presence records from Valkey

### Bug Fixes

- **animator-remote:** Build on any JDK instead of demanding a JDK 17
- **animator-remote:** Pin AndroidX to versions AGP 8.13.2 can build
- **animator-remote:** Keep the controls clear of the system bars
- **animator-remote:** Restore the saved token before prompting for one
- **animator-remote:** Make the manifests well-formed XML
- **clipboard:** Support copying over HTTP
- **logging:** Redact URL-embedded API keys from log output



## [15.0.0] - 2026-07-31

### ⚠ Breaking Changes

- **animator:** Serve routes at the root, dropping the /animator prefix
- **animator:** Rename the reveleitor projector to ceremony

  GET /c/{slug}/reveleitor is removed and now returns 404.
  Use GET /c/{slug}/ceremony instead. The route name animator_reveleitor_page
  becomes animator_ceremony_page.

### Features

- **markdown:** Add shared rendering directives
- **animator:** Phase-01 add animator gate and site medal/secret schema
- **animator:** Phase-02 site medal and operator-secret services
- **animator:** Phase-03 web contest-admin animator settings page
- **animator:** Phase-04 scaffold standalone noca-animator runtime
- **animator:** Redesign contest-admin animator settings page
- **animator:** Phase-05 contest metadata and scoreboard snapshot feeds
- **animator:** Phase-06 live scoreboard presentation page
- **animator:** Phase-07 live SSE event stream
- **animator:** Phase-08 animated live scoreboard
- **animator:** Render balloon/star artwork with letters on scoreboard
- **animator:** Live pending-submission list and cell flash
- **animator:** Animate live connection badge
- **animator:** Model reveal sessions and frozen projections
- **animator:** Implement the reveal state machine
- **animator:** Persist and publish reveal sessions in Valkey [Phase-11]
- **animator:** Show connection outage duration
- **animator:** Add live activity ticker
- **animator:** Add container deployment
- **animator:** Expose authenticated reveal control API (Phase 12)
- **web:** Show medal icons in animator settings
- **animator:** Add reveal spectator APIs and team photos (Phase 13)
- **animator:** Deliver reveal ceremony interfaces (Phase 14)
- **animator:** Add contest clock to launcher
- **animator:** Add footer to presentation pages
- **animator:** Clarify reveal control actions
- **animator:** Render reveal medals as team watermarks
- **animator:** Phase 18 — package and wire production deployment
- **scripts:** Freeze the InterIF 2026 seed scoreboard and close answers early
- **animator:** Show site name on smaller line below contest title
- **animator:** Phase 19 — harden reveal recovery and concurrency
- **config:** Make HOST/PORT configurable for web, arena and healthmonitor
- **animator:** Add contest presentation index

### Bug Fixes

- **audit:** Show usernames for admin actions
- **web:** Remove InterIF fixture language on cleanup
- **audit:** Label contest backup export actors
- **animator:** Recover SSE after terminal disconnects
- **migrations:** Merge animator and master revision heads
- **animator:** Recover reveal clients after disconnects
- **animator:** Unify FLIP motion timing
- **animator:** Improve reveleitor team media modal
- **animator:** Default the brand name to "NOCA Animator"
- Use BRAND_NAME for email sender, APP_NAME for arena JWT issuer
- **healthmonitor:** Repair the heatmap grid column template
- **email:** Encode only the display name in RFC 5322 addresses

### Refactoring

- **scoreboard:** Phase-00 extract shared projection service
- **animator:** Remove site style configuration
- **[BREAKING]** **animator:** Serve routes at the root, dropping the /animator prefix
- **[BREAKING]** **animator:** Rename the reveleitor projector to ceremony

### Documentation

- **animator:** Move team audio playback into phase 14
- **animator:** Move phases 15-17 to the optional backlog
- Update full test timeout guidance
- **animator:** Add controller lease backlog phase

## [14.3.0] - 2026-07-26

### Features

- **arena**: Assign problems to teacher problem sets — a judge-only accordion on
  the problem detail page groups current assignments, links each set to its
  management page, and offers eligible targets from owned ongoing classes through
  safely serialized dependent selectors. A dedicated assignment service and POST
  route revalidate exact role, ownership, class dates, deadline, problem
  existence and non-membership; problem-list return state is preserved, GET and
  POST eligibility use consistent UTC dates, and zero-row duplicate races are
  reported as stale-selection warnings rather than false success. The
  create-problem-set start field defaults to two minutes ahead in the teacher's
  timezone so minute-precision submissions do not immediately fail past-time
  validation
- **languages**: Add Scala 3.3.8 LTS, OCaml 4.14.4 and PHP 8.5.8 as judge
  languages, bringing the registry to 21. Scala compiles with `scalac` and folds
  the Scala runtime jars into a self-contained `solution.jar`, so its run image is
  the same plain Temurin JRE that Kotlin uses and the sandbox needs only the
  existing JVM binds. OCaml is built from source (Debian bookworm ships only
  4.14.1) and compiles natively with `ocamlopt`, so the run image carries no OCaml
  runtime and needs no sandbox binds, matching the Go/Rust model. PHP is
  interpreted, syntax-checked with `php -l`, and runs from source. Each language
  ships editor stubs, highlight.js/Ace modes, a devicon, a stdout flush hint, and
  sample solutions for both the token-compared and custom-validator problems

### Fixes

- **smoke-test**: Point `scripts/autojudge/smoke_test_judge.py` at
  `sample_question/token_validator/`. The sample tree had been reorganized into
  `token_validator/` and `custom_validator/` subdirectories, leaving the script
  resolving every source, input and expected-output path against a directory that
  no longer held them

## [14.2.0] - 2026-07-25

### Features

- **solution-tests**: Add non-scoring solution tests for judges and admins —
  JUDGE, ADMIN and UBERADMIN actors can run a candidate solution against any
  problem in an active contest through the real compiler, sandbox, limits, test
  cases and custom validator, with zero effect on the competition. Runs live in
  their own `solution_test_runs` / `solution_test_case_results` tables so
  leakage into standings, balloons, Runs, reports, feeds and exports is
  structurally impossible; the worker entrypoint takes no Valkey handle, so it
  cannot publish verdict events, invalidate the scoreboard cache, or create
  balloon tasks. Interactive attempts reach these tables through a new
  `attempt_target` axis independent of `domain`. Jobs share the contestant
  queues and the `priority=contest.is_running` rule, are retained for the life
  of the contest, and are excluded from contest backups. Rate limiting is an
  independent per-actor budget keyed on a namespaced advisory lock
- **web**: Add an UberAdmin contest backup export/import — export a finished or
  inactive contest to a portable ZIP (problems, users, submissions, full
  judgment history, clarifications, staff tasks, sites, and optional
  media/password hashes) and restore it under a new name and slug as a faithful
  historical replay, with verdicts, timings and timestamps written verbatim and
  no re-judging. Bounded archive parsing (ZIP-bomb and path-traversal guards,
  size ceilings), fail-closed reference-graph integrity checks, and a
  one-transaction restore with filesystem rollback. Password-hash export
  requires password reconfirmation, and each sensitive opt-in records its own
  admin-action audit event
- **web**: Permanently remove inactive contests — an UberAdmin-only workflow
  that deletes a contest across PostgreSQL, Valkey and the problem-artifact
  filesystem. A verified Valkey purge runs before any destructive change,
  problem artifacts move to a reversible same-root quarantine, and the contest
  graph is deleted in one transaction alongside a sanitized `contest_deleted`
  audit event; a pre-commit failure rolls back and restores the quarantine
- **web**: Add user photo and audio media storage — contest-user photo payloads
  move to a dedicated `users_media` table (public photo and avatar URLs
  unchanged), with validated MP3/OGG/WAV clip uploads, authenticated preview
  endpoints, admin management, staged previews, cache-safe media versions and
  past-contest mutation guards
- **web**: Allow general clarifications and announcements with no problem
  attached — `clarifications.problem_id` is now nullable, both contest forms
  default to a "General" option, and the UI labels such rows as General.
  Contest scoping for clarifications now goes through the author
  (`team_id` → `users.contest_id`) instead of through the problem, so the
  reaper, dashboards, counters, timeline export, backup export, and contest
  removal all cover general rows
- **scoreboard**: Add site filtering — a validated `site_id` query filter trims
  scoreboard snapshots on the backend while preserving global ranks and the
  shared contest-wide cache. Assigned users get "All sites" / "My site only"
  buttons, unassigned users and uberadmins keep the site combobox, and
  single-site contests hide filtering entirely
- **images**: Enforce upload type and size limits — JPEG, PNG and WebP are
  detected from file signatures with `puremagic` instead of client-provided
  MIME types, and a shared streaming multipart limiter stops oversized uploads
  before route processing across Web and Arena. Adds
  `NOCA_IMAGE_MAX_FILE_SIZE` (2 MiB default, 5 MiB hard ceiling)
- **problems**: Support portable GIF illustrations — a deployment-independent
  problem-image contract with fixed 2 MiB and 2048×2048 limits applied to both
  manual uploads and package imports, so an exported problem stays valid across
  installations with different general image settings. Animated GIFs are
  re-encoded with all frames and timing intact and round-trip through problem
  packages
- **web**: Enforce a configurable audio upload limit —
  `NOCA_AUDIO_MAX_FILE_SIZE` (2 MiB default, 5 MiB hard ceiling) with a
  dedicated streaming multipart rule returning HTTP 413 before route
  processing, independent of the photo limit and displayed on the forms
- **web**: Label balloon and star assets with optional ASCII letter path
  segments, rendering the first letter uppercase with black or white text
  chosen by WCAG contrast, and show the labeled assets in scoreboard headers,
  problem details and task modals
- **shared**: Add `shared/services/balloon_assets.py`, a framework-agnostic
  balloon/star SVG renderer (templates, hex/letter normalization, WCAG contrast
  text color, memoized rendering) as the cross-module source of truth
- **web**: Improve contest reports — merge the two time-window charts into one
  stacked AC vs non-AC bar, add client-side Team/Total/AC ordering to "Runs by
  Team and Problem" ranked by a sample-size-aware Wilson score, add captions to
  all nine reports, and add a reusable balloon-thumbnail class
- **web**: Make clarification and runs tables sortable by time and problem via
  a server-side `sort_by` query param, preserved across HTMX polling
- **web**: Make clarification rows full width and clickable, opening the detail
  modal on click or Enter/Space
- **web**: Highlight the viewer's own scoreboard row, auto-scroll it into view,
  and add a floating back-to-top button
- **web**: Allow editing contest user credentials (email and password) after
  the contest ends, via a credentials-only service that leaves profile fields
  frozen

### Bug Fixes

- **autojudge**: Fence worker writes on a per-attempt claim — the Valkey lock
  did not make a run single-writer, so a slow-but-alive worker crossing the
  reaper's stale threshold could execute concurrently with its replacement and
  bury a committed verdict behind a duplicate-key `FAILED`. Every
  `set_*_dispatched` now stamps an attempt-scoped `attempt_token` on all four
  worker-owned run tables, and each later write is fenced on the token it
  stamped; result inserts fold the ownership test into the INSERT's source. A
  lost claim raises `JudgmentOwnershipLost` and aborts dispatch quietly instead
  of persisting `FAILED`. Adds a nullable `attempt_token` column to
  `submission_judgments`, `arena_submission_judgments`, `profiling_runs` and
  `solution_test_runs`
- **autojudge**: Prevent duplicate reconciliation dispatch — atomically
  revalidate inflight membership, stale deadlines, tombstones and lock
  ownership before recovery mutates queue state, and use attempt-scoped lock
  tokens so an older worker cannot remove a replacement worker's claim
- **web**: Keep every Runs column in view — the auto table layout let unsized
  Problem and Team columns push the last columns past the viewport. The table
  now uses a fixed layout with explicit widths from the shared `.noca-col-*`
  scale, and J1/J2/My-verdict columns are hidden in autojudge-only contests
  where they can never hold a value
- **audit**: Record usernames for security events — admin-action producers must
  now snapshot a human-readable actor label alongside the opaque user ID (Web
  records usernames, Arena records normalized email logins)
- **web**: Dismiss queued submission alerts after verdict by tagging the
  redirect with the queued submission ID and including submission IDs only in
  live-visibility SSE payloads, keeping scoreboard-frozen team payloads fully
  redacted
- **web**: Scope the report language tables to the contest's registered
  languages instead of every globally active language
- **web**: Name the team in balloon timeline events so the recipient is
  identifiable in the users-per-site report
- **web**: Make the reports activity chart responsive with a reusable
  full-width layout class and an accessible container label
- **web**: Add `Cache-Control` headers to the balloon and star SVG asset routes
  so the navbar logo and scoreboard balloons stop re-fetching on navigation
- **ui**: Show the configured media upload limits on Arena and Web upload
  surfaces instead of duplicating stale defaults in markup
- **shared**: Add the `judge:submissions` channel definitions used by the web
  submit route, restoring the web runtime import
- **web**: Avoid a contest service import cycle
- **web**: Seed general InterIF clarifications with a null problem ID, and make
  the InterIF seed backup-compatible by generating Markdown statement stubs and
  using real registry languages

### Performance

- **web**: Filter contest runs on the server — problem, team,
  autojudge-verdict and final-verdict predicates are applied in the submission
  query while preserving role-based visibility and SQL sort modes; the obsolete
  client-side filtering script is removed
- **web**: Poll the contest clock instead of streaming it — the clock endpoint
  returns a finite JSON snapshot for every request, freeing a persistent
  connection per tab from the browser's small per-origin pool
- **db**: Tune autovacuum for high-churn tables with a per-table migration for
  the submission/judging pipeline and the append-then-bulk-delete log tables

### Build

- **containers**: Fix missing scripts per image and split schema stewardship —
  `web` and `arena` remain stewards running `run_migrations.py`, while
  `autojudge`, `rating` and `aiassistant` become pure consumers running the new
  `scripts/wait_for_migrations.py`, bounded by
  `NOCA_WAIT_FOR_MIGRATIONS_TIMEOUT` (default 300s)
- Ignore missing type stubs for the untyped `markdown_sanitize` dependency

### Documentation

- **animator**: Add a phased implementation roadmap splitting the unified
  design into 22 dependency-ordered plans, and align the unified plan with the
  codebase and reveal engine
- **bootstrap**: Correct the development setup commands — use the tracked
  environment template, create storage directories safely, restore the
  advisory-lock migration runner, and drive privileged account creation from
  configured credentials instead of hard-coded secrets

## [14.1.0] - 2026-07-18

### Features

- **web**: Add bulk language selection controls (select-all / clear-all) to the
  contest creation and metadata edit templates, backed by a shared static
  script instead of inline JavaScript
- **arena**: Record signup IP and email reputation and notify admins — a new
  `arena_user_reputation` table stores one snapshot per user (signup IP always
  recorded, plus IP/email fraud scores and full JSON reports), a post-signup
  background task emails every `ARENA_ADMIN` a report, and admins review it on a
  new Reputation tab; adds `scripts/backfill_email_reputation.py`
- **shared**: Add an IPQualityScore IP reputation service returning proxy, VPN,
  Tor, crawler, mobile, abuse, and fraud-score signals
- **shared**: Add an IPQualityScore email-reputation service returning
  validity, disposable, suspect, fraud-score, and sanitized-email signals,
  gated on `NOCA_IPQUALITYSCORE_APIKEY`

### Bug Fixes

- **arena**: Show the output diff for PE verdicts — the test-result partial
  gated the output-mismatch comparison on WA only, so PE verdicts rendered as
  if passing; PE is now treated like WA via a shared `is_output_mismatch` flag
- **ui**: Keep problem print and sample test-case blocks readable in dark mode,
  and always render the web problem print view in light mode
- **web**: Avoid the deprecated `datetime.utcnow` in the queue time helper by
  deriving naive UTC from the aware timestamp

## [14.0.1] - 2026-07-17

### Bug Fixes

- **deploy**: Stop the Caddy header delete from wiping `X-Request-ID`, so
  `security_events.request_id` stays correlatable with Caddy access logs

### Documentation

- **crypto**: Ship `scripts/secrets_config.py` in the arena and aiassistant
  images and document the in-container `.env.crypto` bootstrap/rotation
  procedure in `CONFIG.md`, with pointers from `BOOTSTRAP.md` and `BACKUP.md`

## [14.0.0] - 2026-07-16

### ⚠ BREAKING CHANGES

- **validators**: Custom validators are now parametrized by test-case input:
  one container pair judges a whole submission and the judge replays the
  conversation once per test case, writing that case's input to the
  validator's stdin before the two sides talk. Existing validators must be
  rewritten to read their test-case input from stdin first
- **validators**: Interactive problems' test cases now carry **input and
  explanation only** (no expected output) and every case is **secret**;
  problem packages, ZIPs, and downloads for validator problems ship `.in`
  files alone. Packages built for the previous format must be regenerated
- **validators**: A problem with a configured validator must have zero public
  test cases and at least one secret one; staging a validator demotes public
  cases and the sample toggle is refused while a validator is configured
- **validators**: Removing a validator now requires an explicit
  `keep_interactions=true|false` choice on the removal endpoints

### Features

- **problems**: Add sample interactions for interactive problems:
  author-written transcripts (up to five per problem) rendered on the problem
  page, carried in packages as `interaction/NNN.interaction` + `.explain`
- **problems**: Preview the first 10 lines of a sample interaction
- **problems**: Add a print-friendly problem view to Arena and Web
- **validator**: Expose the effective problem limits and submitted language to
  custom validators as environment variables (`PROBLEM_TIME_LIMIT`,
  `PROBLEM_OUTPUT_LIMIT`, `PROBLEM_MEMORY_LIMIT`, `PROBLEM_PID_LIMIT`,
  `USER_LANGUAGE`, plus `PER_LANGUAGE_LIMITS` for Web contests)
- **validator**: Add a highlighted validator source viewer and name exported
  validator source files by language
- **web**: Require a chief judge whenever a contest has judges, and let admins
  and the chief judge work tasks, clarifications, and verdicts
- **arena**: Add a statistics tab to self and admin user profiles
- **arena**: Add admin force-rejudgment on the submission detail page
- **arena**: Restructure the admin sidebar/dashboard and add a Help menu
- **aiassistant**: Include interactive context in AI reviews
- **aiassistant**: Add configurable OpenAI reasoning effort
- **healthmonitor**: Add the health-monitoring module with public status and
  30-day uptime dashboards (port 8002)
- **audit**: Record request correlation metadata (client IP, source port,
  request ID) on security events
- **ui**: Unify web/arena identity, add dark mode, and centralize the NOCA
  brand; replace the arena module icon

### Bug Fixes

- **autojudge**: Stop the interactive exit race from failing correct solutions
- **validator**: Show runtime-failed validators as configured
- **arena**: Load language help on the right tab
- **arena**: Stop highlighting the Problems nav on the dashboard
- **templates**: Fix the explanation of the validator flow
- **ui**: Make monochrome devicons visible in dark mode

### Refactoring

- **web,arena**: Share the row-href script and make run rows clickable
- **arena**: Drop the unused require_arena_judge_or_admin dependency

### Documentation

- **packages**: Add the problem package format specification
- **custom-validator**: Port the sample validator to every judge language
- **healthmonitor**: Add the module to reinstall docs, ops scripts, and module
  lists

### Tests

- **arena**: Lock in readiness checks surviving a removed validator

## [13.3.0] - 2026-07-13

### Features

- **validators**: Add custom interactive validators for Contest and Arena
  problems: staged candidate revisions compiled and validated by the Autojudge,
  interactive judging that pipes the contestant and the validator together, and
  the exit-code-to-verdict mapping
- **validators**: Support zero-test-case validator problems, render the
  interactive attempt transcript, and polish the surrounding UI
- **transcript**: Tell the two sides of an interactive attempt apart in the
  rendered transcript
- **arena**: Show custom validator markers on problem listings
- **arena**: Document per-language stdout flushing on the languages help page
- **problems**: Add illustration images to Contest problems
- **import**: Share the package-format documentation and offer a sample package

### Bug Fixes

- **arena**: Add the privacy link to the footer and resolve both legal links by
  route name
- **web**: Handle empty login form credentials

### Refactoring

- **arena**: Rebuild the problem editor around six cards and a single Save

### Documentation

- **arena**: Explain interactive problems and custom validators in the judgment
  verdicts help tab
- **web**: Document problem image exports

## [13.2.1] - 2026-07-10

### Features

- **login-history**: Record structured IP geolocation
- **arena**: Block mailbox-alias duplicate sign-ups

### Bug Fixes

- **security-events**: Record actor login on remaining auth events

## [13.2.0] - 2026-07-09

### Features

- **judge**: Add Perl language support with registry defaults, syntax-check
  compilation, runtime commands, editor highlighting, starter code, judge
  compile/run images, build targets, language reference docs, and smoke-test
  sample coverage

### Documentation

- **skills**: Document that the bump-version workflow runs mypy, lint, and
  formatting before proceeding with the release commit

## [13.1.6] - 2026-07-09

### Features

- **admin-submissions**: Add a status filter to the dashboard submissions list
- **security-events**: Record and display the actor login for authentication
  events
- **autojudge**: Persist exit signals and retry suspicious sandbox kills

### Bug Fixes

- **geolocation**: Deduplicate repeated location field names

## [13.1.5] - 2026-07-08

### Bug Fixes

- **autojudge**: Raise the isolate `num_boxes` limit to 1000 in run images

### CI

- **containers**: Publish images to both registries in a single build

## [13.1.4] - 2026-07-08

### Features

- **arena**: Enrich the admin user profile with earned badges, notifications and
  submissions
- **arena**: Show the submission owner on the submission detail page
- **security**: Audit authentication and email lifecycle events

### Bug Fixes

- **autojudge**: Strip NUL bytes from captured output before writing to the
  database
- **autojudge**: Allocate a unique isolate box-id per container

## [13.1.3] - 2026-07-07

### Features

- **arena**: Gate Arena rating, statistics, public solver counts and the
  first-solver badge by problem ownership instead of role — every user's
  submissions count for problems they do not own, regardless of role, and only
  the problem owner is excluded; drop the vestigial role argument, the now-dead
  `arena_users` joins, and the unused `only_users` flag
- **arena**: Replace the dashboard "Random Problems" card with a "Latest
  Problems" card listing the 10 most recently created or edited enabled problems
  (ordered by `updated_at`, showing relative edit time), rename the "Top Users"
  card to "Leaderboard" with the top 10 users, and let both cards grow to their
  content

### Documentation

- **arena**: Add the badge catalog documenting each Arena badge awarded by the
  rating worker, including image filename and achievement description

## [13.1.2] - 2026-07-03

### Bug Fixes

- **security**: Allow Cloudflare's Web Analytics beacon in the CSP
  (`static.cloudflareinsights.com` in `script-src`, `cloudflareinsights.com` in
  `connect-src`) so enforcing mode does not block the edge-injected script, and
  document the reverse-proxy trust failure mode (`NOCA_FORWARDED_ALLOW_IPS`) that
  breaks `request.url_for()` scheme and client-IP handling behind a containerized
  proxy; fetch the Bootstrap CSS source map to silence the DevTools 404
- **rating**: Exclude `ARENA_ADMIN`/`ARENA_JUDGE` from the first-solver badge

## [13.1.1] - 2026-07-03

### Bug Fixes

- **shared**: Type security-event row mappings with SQLAlchemy's `RowMapping`
  so the all-module mypy CI job passes

## [13.1.0] - 2026-07-03

### Features

- **security**: Add Valkey-backed auth throttling with a fail-open in-memory
  fallback for Web and Arena login, password reset, signup, 2FA, activation,
  and consent flows
- **security**: Add the `security_events` audit log, module-scoped admin
  viewers, retention reapers, and privileged admin-action recording for Web
  and Arena
- **security**: Add shared browser security headers, production secure-cookie
  validation, proxy-correct client IP handling, default-deny Web auth,
  testcase path guards, and AI review guardrails
- **arena**: Add 10 new gamification badges for language breadth, first-solver
  activity, hand-in timing, burst solving, trimming attempts, and related
  achievements
- **arena**: Add compile logs, failing testcase context, expected output,
  stderr, and existing AI review context to teacher batch-feedback reviews

### Bug Fixes

- **arena**: Hide signup account enumeration by returning the same success
  response for existing accounts and sending an out-of-band account-exists
  email
- **arena**: Unify needs-feedback logic across problem-list and student-report
  pages

### Documentation

- **aiassistant**: Update AI assistant and AI review flow documentation for the
  current worker behavior
- **config**: Document the new security-event retention settings and related
  Web/Arena service routes

## [13.0.0] - 2026-07-01

### BREAKING CHANGES

- **containers**: Upgrade judge runtimes (Node 24, Temurin 25, Ruby 4.0, Rust 1.96, .NET 10, Lua 5.5). Contestant-visible language runtimes change major/minor versions across the board (Node 22->24, JDK/Temurin 21->25, Ruby 3.3->4.0, Rust 1.94->1.96, .NET 8->10, Lua 5.4->5.5). Existing accepted solutions that rely on removed/changed standard-library behavior, deprecated APIs, or version-specific compiler/runtime quirks may need resubmission. Judge images must be rebuilt and `shared/language_configs.py` reseeded via `scripts/bootstrap_languages.py` before deploying.

### Features

- **arena**: Add teacher batch feedback page for problem-set problems
- **arena**: Redirect batch feedback to problem list, allow removing teacher feedback, show source line numbers

### Bug Fixes

- **arena**: Base needs-feedback badge on most recent non-AC submission
- **arena**: Render KaTeX CSS on batch feedback page and correct needs-feedback count

### Build & Infrastructure

- **ci**: Authenticate setup-uv GitHub API calls to avoid rate limits

## [12.4.1] - 2026-07-01

### Bug Fixes

- **static**: Shorten CSS/JS cache lifetime to bound rollout staleness
- **tests**: Fix contest admin template path tests

### Build & Infrastructure

- **ci**: Add validation and image publishing workflows
- **ci**: Add source labels to container images

## [12.4.0] - 2026-07-01

### Features

- **arena**: Lock down Arena pages with default-deny access control
- **health**: Rate limit public health endpoints
- **rating**: Gate problem-difficulty pivot by per-problem attempt count
- **rating**: Snapshot problem-difficulty distribution as a histogram

## [12.3.2] - 2026-06-28

### Bug Fixes

- **ui**: Stack sample test case blocks
- **autojudge**: Move `-lm` after source file and add `-D_GNU_SOURCE` for C
- **aiassistant**: Map unsupported file extensions before OpenAI upload

### Performance

- **judge**: Add JVM determinism flags to Java run command

## [12.3.1] - 2026-06-27

### Bug Fixes

- **arena**: Use standalone error template and silence DB-down log flood
- **arena**: Eagerly load affiliation on public profile to avoid async lazy-load error
- **aiassistant**: Refund platform credit on batch failure and add AI Usage status dashboard

### Refactoring

- **arena**: Rename AI Credits route and template to AI Usage

## [12.3.0] - 2026-06-22

### Features

- **arena**: Add public Arena user profile page with precomputed statistics (verdict distribution, language breakdown, recent activity)
- **arena**: Add `public_profile` opt-in flag for Arena users to control profile visibility
- **arena**: Add problem-count badges for 10, 25, 100, and 500 distinct solved problems
- **arena**: Show earned gamification badges on user profile pages
- **arena**: Link admin problem numbers to their detail pages
- **arena**: Add user badge persistence for gamification (append-only `arena_user_badges` ledger)
- **rating**: Award Arena gamification badges from a dedicated badge-assignment loop in the rating worker

## [12.2.0] - 2026-06-21

### Features

- **ops**: Add unattended backup and restore workflows for PostgreSQL, Valkey,
  deployment configuration, and bind-mounted data
- **ops**: Add local backup retention, Restic uploads, remote snapshot
  retention, and snapshot-based restores
- **arena**: Add Valkey-backed online presence indicators, heartbeat and status
  endpoints, and an authenticated footer counter
- **arena**: Stream owner-scoped profile submission status updates with polling
  fallback and accepted-submission celebrations

### Bug Fixes

- **ops**: Require Restic credentials and repository configuration through
  environment variables
- **ops**: Archive deployment configuration file contents instead of dangling
  relative symlinks

## [12.1.0] - 2026-06-20

### Features

#### Test Cases
- **tc**: Unify the test-case editing UI across the web and arena modules for a consistent experience
- **tc**: Accept `.sol` as an alternative output extension in test case ZIP uploads
- **web**: Add a quick-submit form to the problem detail page

### Bug Fixes

- **submissions**: Reject binary source uploads
- **arena**: Restore sample test-case content on the problem detail page
- **arena**: Read sample test case content from the filesystem in problem detail
- **arena**: Offload test case filesystem reads to a thread to avoid blocking the event loop
- **web**: Persist test case sizes during problem import

### Styles

- **web**: Update the navbar balloon brand color

## [12.0.2] - 2026-06-19

### Bug Fixes

- **arena**: Fix lifespan test mocks to cover `ensure_sem_afiliacao` — tests for JWT issuer and image service avatar size were failing because the new startup seed call was not mocked; mock added to `_configure_lifespan_mocks`

### Features

#### Arena — Affiliations
- **arena**: Add `exclude_from_ranking` flag to affiliations — admins can mark an affiliation so its members are excluded from the ranking; flag is editable on the affiliation admin page
- **arena**: Add "No affiliation" checkbox to affiliation change modal — users can opt out of any affiliation; backend stores this as a null affiliation reference
- **arena**: Upsert "Sem afiliação" affiliation on startup — the built-in no-affiliation entry is created or refreshed with `exclude_from_ranking=True` on every startup, ensuring it is always present and correctly configured

#### Arena — Profile
- **arena**: Prompt profile completion after login — users who have not set an affiliation are prompted to complete their profile on the next login

### Bug Fixes (continued)

- **arena**: Exclude inactive users from class members list and problem set report

## [12.0.1] - 2026-06-19

### Bug Fixes

#### AI Assistant
- **aiassistant**: Clean up terminal Valkey jobs — atomic terminal cleanup removes duplicate pending/inflight entries, dispatch timestamps, and `ai:job` metadata hashes; cleanup is buffered through `ValkeyRuntime` so it is replayed after recoverable outages; applied after successful online reviews, durable batch staging, idempotent exits, non-retryable failures, and retry-limit discards

### Features

#### AI Assistant
- **aiassistant**: Add flush-now and poll-now trigger commands — two one-shot admin dashboard commands wake the batch flusher and batch poller immediately instead of waiting for the next scheduled window; commands use the existing HMAC-signed Valkey transport; trigger events interrupt inter-cycle sleep via `interruptible_sleep`; an `arena_worker_command_audit` row is committed before publishing and updated with the transport outcome; buttons appear only on aiassistant worker cards when `pause_enabled`

### Fixes

- **docs**: Fix `MIGRATION.md` step 5 that omitted `rating` and `aiassistant` from the image pull/build command, causing those containers to restart-loop with "Can't locate revision" after a 12.0.0 deployment

## [12.0.0] - 2026-06-19

### Breaking Changes

- **arena**: Move Arena test-case storage from database to shared filesystem — `input_content`/`output_content` columns dropped; test-case content is now stored under `NOCA_PROBLEM_TESTCASE_DIR/arena/<problem_id>/`; inline editing is gated to ≤10 KB; larger cases use ZIP download/replace round-trip
- **arena**: Separate problem ownership from authorship — problems now have a distinct `owner` (who can manage it) and `author` (credit field); existing data migrated; `can_edit` flag added for granular editing control

### Breaking Changes

- **arena**: Move Arena test-case storage from database to shared filesystem — `input_content`/`output_content` columns dropped; test-case content is now stored under `NOCA_PROBLEM_TESTCASE_DIR/arena/<problem_id>/`; inline editing is gated to ≤10 KB; larger cases use ZIP download/replace round-trip
- **arena**: Separate problem ownership from authorship — problems now have a distinct `owner` (who can manage it) and `author` (credit field); existing data migrated; `can_edit` flag added for granular editing control

### Features

#### Arena — AI Review
- Show AI review turnaround time on submission detail
- Show AI batch turnaround statistics on admin dashboard
- Confirm AI review requests before submission
- Show pending batch job count on AI assistant worker card
- Adapt AI review turnaround display units (seconds/minutes/hours)

#### Arena — Classes & Problem Sets
- Enforce problem set dates to fall within the class period
- Inline problem sets on class detail page
- Combine description editing with problem-set schedule update
- Notify teachers when students request class registration
- Add best-effort email notifications for class membership events
- Add problem removal request and class membership notifications
- Improve class and problem-set page UI
- Improve problem set list and report pages

#### Arena — Problems
- Add optional license field to Arena problems
- Add prev/next problem navigation on problem detail page
- Add danger zone to problem edit page (delete and rejudge actions)
- Link category name to filtered problem list
- Add AC rate and solved count/status columns to problem list
- Sort problems by solver count
- Make table rows clickable in problem and class problem-set lists
- Improve problem detail editor UX
- Add resizable problem workspace

#### Arena — Users & Profiles
- Add ranking_visible flag to Arena users
- Allow profile date of birth updates
- Add submission heatmap to user profile; improve heatmap and add to admin user profile
- Show affiliation logos in user ranking pages
- Show avatars in class student tables
- Replace emoji country flags with SVG images
- Improve user management profiles

#### Arena — Admin Dashboard
- Add Admin Dashboard sub-nav with AI Credits Usage page
- Add global login history and submission list admin pages
- Add admin user login history
- Enhance admin dashboard submission, login and credit views
- Show user origin in live feed
- Show pagination controls above tables, not only below
- Include problem title in export filename

#### Arena — Workers & Infrastructure
- Add authenticated worker pause/resume control (HMAC-signed Valkey nudge, PostgreSQL authoritative)
- Add worker presence dashboard
- Add worker status page
- Add last-job column to worker dashboard
- Add HTMX OOB flash support, worker dashboard CSS, and queue metric helpers
- Show pending batch jobs count on AI assistant worker card
- Require admin password confirmation for sensitive user actions
- Redirect expired HTMX sessions
- Show max output size on submission detail
- Add security event email notifications
- Show supported languages card on dashboard
- Add teacher feedback on submissions
- Add can_edit flag for granular problem-base editing

#### AI Assistant
- Accumulate platform-key AI reviews into windowed OpenAI Batch API jobs
- Expire stale OpenAI batch reviews with automatic credit refund
- Publish batch turnaround statistics to Valkey for Arena dashboard

#### Languages
- Add Swift as a judged language
- Add Ruby and Bash as supported judge languages

#### Rating
- Bimodal contrast algorithm for Arena problem difficulty
- Drop solve-velocity age component; add contrast chart
- Exclude `ARENA_JUDGE` and `ARENA_ADMIN` roles from all rating calculations

#### Runtime & Infrastructure
- Centralize branded error handling across all modules
- Handle backend outages (DB/Valkey down) gracefully
- Add wait-for-db/Valkey readiness checks at startup across all modules

#### Assets & UI
- Replace devicon CSS icons with self-hosted SVGs (assets and live-feed)
- Unify Flatpickr date pickers via shared init script
- Make arena-table denser; restore density on worker table

#### Email
- Add mbox audit log of all delivered emails
- Report mbox logging configuration on startup

### Bug Fixes

- **arena**: Correct language icon sizing in submission detail and dashboard
- **arena**: Correct problem-set report visibility and student verdicts
- **arena**: Correct route name for class membership notifications
- **arena**: Distinguish problem difficulty from user rating
- **arena**: Exclude admin and problem author from rating, counting, and statistics
- **arena**: Exclude staff from public problem stats
- **arena**: Exclude teacher's own class from open-registration list; guard service endpoint
- **arena**: Hide stop-now button and guard route when problem set is closed
- **arena**: Improve class management UI
- **arena**: Record completed 2FA and backup-code logins in history
- **arena**: Remove duplicate CSS, fix ZIP extraction
- **arena**: Resolve two class-page bugs
- **arena**: Restore notification click navigation and set missing target URLs
- **arena**: Skip past-date validation when class dates are unchanged on edit
- **arena**: Suppress feedback badge when student has an AC submission
- **email**: Encode plain-text email parts as quoted-printable instead of base64
- **judge**: Normalize CRLF line endings in test-case content before comparison
- **rating**: Pin zero-attempt problems to display rating of 5.0
- **shared**: Add ace/highlightjs language mappings; fix devicon icons for Ruby and Bash
- **workers**: Silence reload shutdown tracebacks
- **db**: Cascade delete from test cases to result rows; warn on TC edit when submissions exist

### Refactoring

- **aiassistant**: Split `batch_poller` into three focused modules
- **arena**: Extract problem admin form presentation helpers
- **arena**: Remove redundant problem-set list route
- **arena**: Replace tabbed class list with dedicated sub-routes
- **arena**: Split `admin_users` routes and eliminate nav-state repetition
- **arena**: Split `arena_users` model and dedupe location helpers
- **autojudge**: Split `worker.py` into `dispatch`, `reconcile`, and `decode` modules
- **db**: Drop unused generic timestamp columns and reconcile schema drift
- **shared**: Split `language_registry` into three focused modules

### Performance

- **auth**: Use bigint IDs for login history table for improved scalability

### Build & Infrastructure

- **containers**: Upgrade judge isolate to 2.6
- **containers**: Introduce `assets-base` image to fetch vendor assets once per release
- **containers**: Drop PowerShell build script; make assets platform-configurable
- **containers**: Harden `uv sync` against PyPI timeouts
- **build**: Harden multi-platform push builds for reliability
- **swift**: Fetch static Swift SDK via curl with retry, then drop curl from image

## [11.8.0] - 2026-06-18

### Features

#### AI Assistant
- Accumulate platform-key AI reviews into windowed OpenAI Batch API jobs
- Expire stale OpenAI batch reviews with automatic credit refund
- Publish batch turnaround statistics to Valkey for Arena dashboard

#### Arena — AI Review
- Show AI review turnaround time on submission detail
- Show AI batch turnaround statistics on admin dashboard
- Adapt AI review turnaround display units (seconds/minutes/hours)
- Show pending batch job count on AI assistant worker card

#### Arena — Problems
- Improve problem detail editor UX
- Make table rows clickable in problem and class problem-set lists

#### Arena — Classes & Problem Sets
- Improve class and problem-set page UI

### Bug Fixes

- **arena**: Correct language icon sizing in submission detail and dashboard
- **arena**: Distinguish problem difficulty from user rating
- **arena**: Skip past-date validation when class dates are unchanged on edit
- **arena**: Resolve two class-page bugs

### Refactoring

- **arena**: Remove redundant problem-set list route

## [11.7.2] - 2026-06-17

### Features

#### Arena
- Replace CSS devicon icons with self-hosted SVGs in live feed and assets
- Add supported languages card on dashboard

#### Languages
- Add Ruby and Bash as supported judge languages

### Bug Fixes

- **arena**: Exclude admin and problem author from rating, counting, and statistics
- **arena**: Fix Swift icon
- **email**: Encode plain-text email parts as quoted-printable instead of base64

## [11.7.1] - 2026-06-16

### Build & Infrastructure

- **swift**: Fetch static Swift SDK via curl with retry, then drop curl from image
- **build**: Harden multi-platform push builds for reliability

## [11.7.0] - 2026-06-16

### Features

#### Languages
- Add Swift as a judged language

#### Arena
- Adjust activity heatmap cell size

## [11.6.2] - 2026-06-16

### Features

#### Arena
- Sort problems by solver count
- Add best-effort email notifications for class membership events

### Bug Fixes

- **arena**: Restore notification click navigation and set missing target URLs
- **arena**: Exclude staff from public problem stats

## [11.6.1] - 2026-06-15

### Features

#### Arena — Admin Dashboard
- Enhance admin dashboard submission, login and credit views

#### Arena
- Record completed 2FA and backup-code logins in history

#### UI
- Unify Flatpickr date pickers via shared init script

## [11.6.0] - 2026-06-15

### Features

#### Arena — Admin Dashboard
- Add global login history and submission list admin pages

#### Rating
- Exclude `ARENA_JUDGE` and `ARENA_ADMIN` roles from all rating calculations

### Bug Fixes

- **rating**: Pin zero-attempt problems to display rating of 5.0

## [11.5.2] - 2026-06-14

### Features

#### Arena — Admin Dashboard
- Add Admin Dashboard sub-nav with AI Credits Usage page
- Add admin user login history

#### Arena — AI Review
- Confirm AI review requests before submission

#### Arena — Classes & Problem Sets
- Improve problem set list and report pages
- Enforce problem set dates to fall within the class period
- Inline problem sets on class detail page
- Add prev/next problem navigation on problem detail page

### Bug Fixes

- **arena**: Exclude teacher's own class from open-registration list; guard service endpoint
- **arena**: Hide stop-now button and guard route when problem set is closed

### Performance

- **auth**: Use bigint IDs for login history table for improved scalability

## [11.5.1] - 2026-06-13

### Features

#### Arena — Problems
- Link category name to filtered problem list
- Add AC rate, solved count, and solved status columns to problem list
- Add ranking_visible flag to Arena users

#### Arena
- Improve submission heatmap and add to admin user profile

#### Build
- Harden `uv sync` against PyPI timeouts

#### UI
- Make arena-table denser; restore density on worker table

## [11.5.0] - 2026-06-12

### Features

#### Arena
- Add submission heatmap to user profile
- Add resizable problem workspace
- Add security event email notifications

#### Email
- Add mbox audit log of all delivered emails

### Bug Fixes

- **judge**: Normalize CRLF line endings in test-case content before comparison
- **email**: Use quoted-printable encoding for plain-text parts
- **arena**: Suppress feedback badge when student has an AC submission

## [11.4.0] - 2026-06-12

### Features

#### Arena — Workers & Infrastructure
- Add authenticated worker pause/resume control (HMAC-signed Valkey nudge, PostgreSQL authoritative)
- Add worker presence dashboard
- Add worker status page
- Add last-job column to worker dashboard
- Add HTMX OOB flash support, worker dashboard CSS, and queue metric helpers

#### Arena
- Show user origin in live feed

### Bug Fixes

- **arena**: Redirect expired HTMX sessions

### Refactoring

- **arena**: Replace tabbed class list with dedicated sub-routes
- **db**: Drop unused generic timestamp columns and reconcile schema drift

## [11.3.0] - 2026-06-11

### Features

#### Arena
- Add teacher feedback on submissions
- Show avatars in class student tables
- Show max output size on submission detail

#### Rating
- Bimodal contrast algorithm for Arena problem difficulty
- Drop solve-velocity age component; add contrast chart

#### Email
- Add mbox audit log of all delivered emails
- Report mbox logging configuration on startup

### Bug Fixes

- **arena**: Correct route name for class membership notifications
- **arena**: Correct problem-set report visibility and student verdicts

## [11.2.0] - 2026-06-08

### Features

#### Arena
- Include problem title in export filename
- Show pagination controls above tables, not only below

### Bug Fixes

- **db**: Cascade delete from test cases to result rows; warn on TC edit when submissions exist

### Build & Infrastructure

- **containers**: Upgrade judge isolate to 2.6
- **containers**: Introduce `assets-base` image to fetch vendor assets once per release
- **containers**: Drop PowerShell build script; make assets platform-configurable

## [11.1.0] - 2026-06-06

### Features

#### Arena — Workers & Infrastructure
- Centralize branded error handling across all modules
- Handle backend outages (DB/Valkey down) gracefully
- Add wait-for-db/Valkey readiness checks at startup across all modules

#### Arena — Users & Profiles
- Replace emoji country flags with SVG images
- Allow profile date of birth updates
- Improve user management profiles

#### Arena — Admin
- Require admin password confirmation for sensitive user actions
- Show affiliation logos in user ranking pages
- Add danger zone to problem edit page (delete and rejudge actions)

#### Arena — Classes & Problem Sets
- Notify teachers when students request class registration
- Combine description editing with problem-set schedule update
- Add problem removal request and class membership notifications

#### Arena
- Add can_edit flag for granular problem-base editing

### Bug Fixes

- **arena**: Remove duplicate CSS; fix ZIP extraction
- **arena**: Improve class management UI
- **workers**: Silence reload shutdown tracebacks

### Refactoring

- **arena**: Split admin_users routes and eliminate nav-state repetition
- **arena**: Split arena_users model and dedupe location helpers
- **arena**: Extract problem admin form presentation helpers
- **autojudge**: Split `worker.py` into `dispatch`, `reconcile`, and `decode` modules
- **aiassistant**: Split `batch_poller` into three focused modules
- **shared**: Split `language_registry` into three focused modules

## [11.0.0] - 2026-06-05

### Features

#### Arena — Classes & Problem Sets
- Add Class and Problem Set concepts
- Teacher drill-down into student submissions from problem-set report
- Manage problem-set schedule inline; add stop-now action

#### Arena
- Display datetimes in user timezone
- Replace native date/time pickers with Flatpickr v4
- Add public live submission feed
- Custom HTML 404 page with random illustration
- Make Arena live feed configurable and mask identity

#### Logging
- Log resolved settings at module startup

### Refactoring

- **css**: Split arena.css and contest.css into themed partials
- **static**: Extract shared CSS to common.css; rename app.css to contest.css
- **static**: Consolidate duplicated JS into shared module
- **arena**: Split services into cohesive service modules
- **live-feed**: Extract shared SSE and query helpers

## [10.3.0] - 2026-06-03

### Features

#### Arena
- Add per-team (web) and per-user (arena) submission rate limiting

#### Problems
- Auto-assign predefined balloon color on import; consolidate palette
- Add quick sample/secret toggle on test-case lists

#### UI
- Rebrand web UI copy from NOCA to Noca Contest
- Replace text action labels with icon btn-group on test-case lists

## [10.2.0] - 2026-06-02

### Features

#### Arena
- Add notes field to Arena problems; remap import `author` → `source`

#### Test Cases
- Add optional explanation field to test cases

### Bug Fixes

- **autojudge,rating**: Suppress verbose tracebacks on transient DB connection failures in background loops

## [10.1.0] - 2026-06-01

### Features

#### Arena
- Add navbar logo branding and favicon assets
- Add per-problem statistics page
- Show problem author link to admins on edit form
- Add Edit button on problem detail page for admins and authors
- Add problem ZIP import and export

#### Shared
- Update default language stubs to echo input

### Performance

- **web**: Offload ZIP export assembly to background thread

## [10.0.0] - 2026-05-31

### Breaking Changes

- **config**: Add `NOCA_LOG_LEVEL` support across all runtime modules — log-level configuration is now unified under this variable

### Features

#### Arena
- Replace solution textarea with Ace code editor
- Add favorite problems feature
- Redirect browsers to login on 401/403 with next-URL round-trip
- Add bootstrap script to create initial Arena admin user

### Bug Fixes

- **arena**: Inline pending test cases on problem edit; default sort by number

## [9.1.0] - 2026-05-30

### Features

#### Arena — AI Review
- Add platform-funded batch review path via OpenAI Batch API
- Notify users when AI reviews complete
- Include problem image in AI review context

#### Arena — Users & Profiles
- Add public ranking pages for users and affiliations
- Add user preferred language locale
- Merge Personal Data and Security into unified profile tab

#### Arena — Admin
- Add AI credit transactions ledger and admin top-up
- Add admin email confirmation and parental consent toggles
- Add category search filter; refactor admin list UI; improve slug generation
- Add mark-all-as-read for notifications

#### Arena
- Add submission detail view, submissions tab, AI review status, and edit-and-retry
- Add solve-velocity age factor to problem difficulty rating
- Add AI backend credits gate to AI review endpoint

#### Rating
- Publish scheduler metadata from worker

#### Build
- Centralize workspace version in root pyproject.toml
- Pin dependency versions

### Bug Fixes

- **config**: Rename `URL_BASE` to `WEB_URL_BASE` and `ARENA_URL_BASE` for module clarity
- **queue**: Recover AI review and judge jobs lost between DB commit and Valkey enqueue
- **arena**: Eagerly load affiliation on admin user fetch to prevent async lazy-load error
- **aiassistant**: Fix batch idempotency guard bypassed by terminal job rows

### Refactoring

- **config**: Namespace environment variables by module

## [9.0.0] - 2026-04-25

### Breaking Changes

- **license**: Change license from AGPL to NOCA NC License

### Features

#### Languages
- Add compiler/interpreter version field to language registry

### Build & Infrastructure

- Add copyright notice headers across script codebase
- Document multiplatform image building

## [8.2.0] - 2026-04-24

### Features

#### Auth
- Add Valkey-backed JWT revocation store on logout
- Clear invalid JWT cookies on next request

#### Uberadmin
- Add management interface with list, edit, and enable/disable actions
- Support inactive contest management

### Bug Fixes

- **autojudge**: Improve startup diagnostics for config errors and missing migrations

## [8.0.0] - 2026-04-22

### Features

#### Autojudge
- Add Prometheus telemetry exposition

### Bug Fixes

- **makefile**: Sync Makefile behavior on Linux and Windows

## [7.0.0] - 2026-04-20

### Features

#### Arena
- Add public Arena platform: signup flow, login/logout, 2FA, forced password reset, OTP-protected accounts
- Add Arena problem browsing, detail page, and sample test-case download
- Add admin CRUD for problems, test cases, and categories
- Add admin user management
- Add Arena submission judging via shared autojudge worker
- Add periodic problem difficulty and user rating computation
- Add user profile with location, affiliation, and rating history chart
- Add affiliation management with logo upload
- Add ToS/PP acceptance tracking, legal pages, and login gate
- Add LGPD parental consent age gate
- Add remember-me-aware session tokens and middleware refresh
- Add durable user notifications with Material Symbols icons
- Add AI code review infrastructure for Arena submissions
- Add photo crop on signup and profile photo change
- Add Arena rating history chart and split help pages
- Add Class and Problem Set scaffolding
- Add live feed with configurable identity masking
- Add rating help and languages help tabs

#### Languages
- Add Go, Rust, Lua, Haskell, Prolog, and Fortran language support

#### Autojudge
- Add startup banner; lazy-warm container pools on first submission per language
- Hot-reload worker on file changes in development

#### Rating
- Extract Arena rating into dedicated single-replica worker

#### Build
- Introduce shared base Docker images; reorganize Docker infrastructure
- Add Arena Docker compose service and aiassistant image
- Convert repo to uv workspace with per-module pyprojects

#### UI
- Add first balloon highlighting

### Bug Fixes

- **arena**: Password change fixes, flash rendering, JWT session refresh
- **arena**: Preflight crypto environment at startup
- **arena**: Fix dependencies for Fortran run container
- **valkey**: Make dequeue priority selection atomic
- **problem**: Avoid ordinal collisions on test-case removal
- **problem**: Warn before leaving unsaved problem edits
- **web**: Return to edited test case after save

### Refactoring

- **web**: Reorganize templates into role-based subdirectories
- **web**: Split oversized services and route modules into focused files
- **valkey**: Split Valkey service into focused modules
- **shared**: Split `db_schema` into package modules
- **autojudge**: Split large modules into focused single-responsibility files
- **static**: Organize utilities by module
- **arena**: Modularize dashboard cards

### License

- Update license to AGPL

## [6.7.0] - 2026-04-18

### Features

#### Auth
- Add sliding JWT session refresh

### Bug Fixes

- **auth**: Force full-page login redirect for HTMX requests
- **runs**: Repair team verdict refresh and AC confetti
- **autojudge**: Prevent lost jobs during worker recovery

## [6.6.0] - 2026-04-18

### Features

#### Runs
- Enrich final-verdict SSE updates and celebrate accepted runs with confetti

#### Dashboard
- Auto-refresh contest counters

#### Docs
- Add first version of the NOCA user manual

### Bug Fixes

- **types**: Replace mypy ignores with type-safe annotations

## [6.5.0] - 2026-04-13

### Features

#### Contest
- Allow editing contest languages before start
- Allow running contest limit edits

### Refactoring

- **logging**: Improve log messages for clarity and consistency

## [6.4.0] - 2026-04-13

### Features

#### Contest
- Sort live contests by remaining time on public dashboard
- Validate metadata duration doesn't set end time in the past
- Add end-now action with password confirmation

#### UI
- Add light/dark theme toggle
- Show balloon images in problem list and problem detail header

#### Dashboard
- Add pending-item counters to dashboard cards

### Bug Fixes

- **contest**: Fix uberadmin login link on contest login page
- **contest**: Simplify uberadmin contest card to single administration link
- **timeline**: Filter events past contest end and widen table columns
- **contest-timing-timeline**: Handle zero-percentage segments and relax boundary check

## [6.3.2] - 2026-04-12

### Refactoring

- **containers**: Extract service healthchecks

## [6.3.0] - 2026-04-11

### Features

#### Autojudge
- Support flat judge image naming

## [6.2.2] - 2026-04-11

### Features

#### Exports
- Add contest timeline export

## [6.2.1] - 2026-04-11

### Features

#### Contest
- Add contest timing timeline with dynamic progress visualization
- Enhance contest metadata forms with improved layout and radio options for settings

#### Admin
- Show profiling queue metrics on counters page

### Refactoring

- **web**: Extract contest tile macro and polish footer UI
- **web**: Extract flash message rendering macro

## [6.2.0] - 2026-04-10

### Features

#### Problem Editor
- Add LaTeX and Mermaid syntax support with help modal

## [6.1.1] - 2026-04-09

### Features

#### Problem
- Integrate KaTeX for rendering LaTeX equations in problem statements

### Bug Fixes

- **html**: Add extra_script block for JavaScript inclusion in multiple templates
- **migrations**: Fix downgrade revision

## [6.1.0] - 2026-04-09

### Bug Fixes

- **judge**: Move repetitions to per-language limits
- **autojudge**: Allow configuring run-container AppArmor profile

## [5.0.1] - 2026-04-09

### Bug Fixes

- **autojudge**: Sync canonical judge images at startup

## [5.0.0] - 2026-04-09

Initial public release of NOCA.
