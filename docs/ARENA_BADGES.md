# Arena badges

Arena badges are achievement markers awarded to Arena users. The `rating`
module computes them through the badge-assignment loop in `rating/loops.py`,
which calls `shared.services.arena_badges.compute_badge_awards()`.

Every badge names the submission that earned it, and the badge set is derived
rather than accumulated: each full reconciliation re-derives every badge from
live data, awards what's newly earned, and revokes what isn't earned any more.
See [Which submission earned a badge](#which-submission-earned-a-badge) and
[Badges are reconciled, not accumulated](#badges-are-reconciled-not-accumulated).

The badge enum and display metadata live in `shared/enumerations.py`. Each
awardable badge image is stored under `arena/static/img/badges/`, and each
image filename matches the badge value with a `.png` extension. The UI uses
`missing_badge.png` only as the locked or fallback image; it isn't an awardable
badge.

## Awardable badges

The following list includes every Arena badge that can be awarded by the rating
worker.

- **Hello, World!** Image filename: `helloworld.png`. Description: Solve at
  least one problem.
- **One Shot** Image filename: `oneshot.png`. Description: Solve a problem on
  the first attempt.
- **Full Clear** Image filename: `fullclear.png`. Description: Solve all
  problems in at least one problem set.
- **Bug Killer** Image filename: `bugkiller.png`. Description: Get an accepted
  submission immediately after a runtime error on the same problem.
- **Clean Code** Image filename: `cleancode.png`. Description: Have a solution
  in the top 5% for execution time and memory on a problem. See [Dynamic
  badges](#dynamic-badges).
- **Bit Scrubber** Image filename: `bitscrubber.png`. Description: Get an
  accepted submission after a time-limit or memory-limit error on the same
  problem.
- **Night Worker** Image filename: `nightworker.png`. Description: Solve a
  problem between midnight and 5:00 AM in the user's local timezone.
- **Weekend Worker** Image filename: `weekendworker.png`. Description: Solve a
  problem on Saturday or Sunday in the user's local timezone.
- **Strike 3** Image filename: `strike3.png`. Description: Solve problems on 3
  consecutive days.
- **Strike 7** Image filename: `strike7.png`. Description: Solve problems on 7
  consecutive days.
- **Strike 30** Image filename: `strike30.png`. Description: Solve problems on
  30 consecutive days.
- **Never Give Up** Image filename: `nevergiveup.png`. Description: Solve a
  problem after at least 5 wrong-answer verdicts on that problem.
- **10 Problems** Image filename: `10problems.png`. Description: Solve 10
  different problems.
- **25 Problems** Image filename: `25problems.png`. Description: Solve 25
  different problems.
- **100 Problems** Image filename: `100problems.png`. Description: Solve 100
  different problems.
- **500 Problems** Image filename: `500problems.png`. Description: Solve 500
  different problems.
- **First to Hand In** Image filename: `firsttohandin.png`. Description: Be
  the first user to solve a problem through a problem set.
- **First Solver** Image filename: `firstsolver.png`. Description: Be the first
  user to solve a problem you do not own. Eligibility is gated by problem
  ownership, not role: the problem owner is excluded, and any other user earns
  the badge regardless of role.
- **3 Languages** Image filename: `3languages.png`. Description: Solve the
  same problem in 3 different languages.
- **5 Languages** Image filename: `5languages.png`. Description: Solve the
  same problem in 5 different languages.
- **10 Languages** Image filename: `10languages.png`. Description: Solve the
  same problem in 10 different languages.
- **Loco Coder** Image filename: `lococoder.png`. Description: Get 3
  non-accepted verdicts on the same problem within 90 seconds.
- **This Is the Way** Image filename: `thisistheway.png`. Description: Solve 15
  different problems in a row without a non-accepted submission.
- **Rock Cracker** Image filename: `rockcracker.png`. Description: Solve a
  problem whose solve rate is below 20%. See [Dynamic
  badges](#dynamic-badges).
- **Almost Late** Image filename: `almostlate.png`. Description: Be the last
  on-time solver for a problem in a problem set after the deadline passes.
- **Trimmer** Image filename: `trimmer.png`. Description: Get an accepted
  submission immediately after a presentation error on the same problem.

## Which submission earned a badge

A badge is a claim about a piece of work. If it can't name the submission that
earned it, it isn't a badge — it's an assertion with nothing behind it. So every
badge row records that submission in `arena_user_badges.submission_id`, and you
can answer "which submission got me One Shot?" and not only "when did I get it?"
The problem is reachable from there through `arena_submissions.problem_id`, so no
separate problem column is needed.

The column is `NOT NULL`. A rule that can't derive an anchor awards nothing
rather than awarding a claim with nothing behind it. The foreign key is
`ON DELETE CASCADE`, so deleting a submission deletes the badges that named it.

### Profile visibility

Arena profile pages expose badge provenance according to the viewer's access.
Your own profile links a badge to the awarding submission. The admin user profile
uses the same submission link, because Arena administrators can already inspect
user submissions. A public profile links only to the problem and never exposes
the submission or its source code.

The one earned badge without a provenance link is a public view of a badge whose
problem is disabled. The badge otherwise renders normally.

Badges fall into four shapes, and each names something different:

- **Event badges** fire on one specific accepted submission: One Shot, Bug
  Killer, Bit Scrubber, Trimmer, Night Worker, Weekend Worker, Never Give Up,
  First Solver, First to Hand In, Almost Late, and Hello, World! These name the
  accepted submission that qualified. Loco Coder is the one event badge whose
  submission is *not* accepted: it names the third non-accepted verdict in the
  90-second window, which is exactly what the badge records.
- **Aggregate badges** are earned by a set of submissions crossing a threshold:
  the Strike, Problems, and Languages tiers, plus Full Clear and This Is the Way.
  These name the submission that *crossed* the threshold — the accepted
  submission that completed the streak, that reached your 25th distinct problem,
  that finished the set. That's a convention rather than a fact, because no
  single submission earned the badge on its own. Full Clear is worth calling out:
  its anchor is your first accepted submission on whichever of the set's problems
  you solved last, which is usually not the submission that prompted the rating
  worker to notice. When you've cleared more than one set, the badge names the
  set you completed earliest.
- **Clean Code names a representative.** It records a rank rather than an event,
  and qualification takes your best time and your best memory on a problem
  independently, so the two can come from different submissions. The row
  therefore names the cleanest of them: among your accepted submissions on a
  qualifying problem, the one minimizing `(wall_time, memory, created_at, id)`,
  and the same comparison across several qualifying problems. It's deterministic
  and re-derives identically every pass.
- **Rock Cracker names current evidence.** Its anchor is your first accepted
  submission on the earliest-solved problem that qualifies *now*, so when the
  problem that used to keep you eligible stops qualifying, the row moves to the
  solve that keeps you eligible today.

### When several submissions could be the anchor

More than one submission can witness the same badge, and the row names only one.
The choice is always deterministic, so a reconciliation re-derives the same
anchor every pass instead of shuffling rows:

- Event and aggregate badges take the **earliest** qualifying submission, in
  canonical `(created_at, id)` order.
- First to Hand In and Almost Late take the earliest of the pairs you won, by the
  same order.
- First Solver takes the earliest of the problems you solved first, by
  `(solved_at, problem_id)`.
- Clean Code takes the cleanest, as described above.

## Badges are reconciled, not accumulated

The badge set reflects the situation as of the last full reconciliation. Each
full pass re-derives every badge from live data and writes the difference: a
badge that's newly earned is awarded, one whose criterion no longer holds is
revoked, and one whose canonical submission moved is re-anchored in place.

Revocation has exactly three triggers:

- The criterion no longer holds under live data.
- The awarding submission was deleted, which cascades.
- A rejudge moved the awarding submission off Accepted, and no other submission
  earns the badge.

A **disabled problem revokes nothing.** No rule filters on `problem.enabled` and
none should start. Disabling is routinely temporary — pulling a problem to fix a
test case — and revoking on it would strip badges from everyone who earned one
there and hand them back on re-enable, churn caused by an administrative act the
user had no part in. The public profile already declines to *link* a disabled
problem, and that stays.

A re-anchored row keeps its `awarded_at` and its row identity, because
eligibility was never interrupted. A revoked and later re-earned badge is a new
row with a new `awarded_at`.

Only the **full** pass reconciles. The cheaper incremental pass is bounded by a
watermark and sees a subset of history, so it may only insert; revoking from a
partial view would delete every badge it didn't happen to look at. The deploy
that ships a rule change is itself the trigger when the worker runs with
`NOCA_RATING_COMPUTE_ON_STARTUP=true`: its startup cycle is a full
reconciliation, so new rules apply as the worker comes up rather than up to
`NOCA_RATING_BADGE_RECONCILE_INTERVAL` later.

A pass over an unchanged database writes nothing at all — no insert, no update,
no delete. That's a requirement rather than an observation: the table is rewritten
every cycle, and an unconditional write would leave a dead tuple per badge per
cycle. The table carries per-table autovacuum tuning sized for real movement in
the badge set, applied by migration `202609100002`.

Removing a student from a class doesn't endanger a badge or its anchor.
`arena_class_memberships` is an append-only event log, so a removal appends a row
instead of deleting anything, and the student's submissions keep their
`problem_set_id`. The badge rules never consult membership at all.

Deleting a problem set does clear `arena_submissions.problem_set_id`, which
leaves the submissions themselves in place. First to Hand In, Almost Late, and
Full Clear then have no set left to rank against, so the next full pass revokes
them.

## Dynamic badges

Every badge follows current state, but Clean Code and Rock Cracker are the two
whose criteria are explicitly a *ranking* against a moving population, so they're
worth describing on their own.

### Clean Code

Clean Code records a rank, and that rank moves as other people submit faster
solutions.

Three rules keep the badge close to the 5% it advertises:

- **A problem needs at least 20 distinct solvers to rank anyone.** Below that, a
  5% band can't hold even one user without being a large share of the
  population, and being the only person to solve an unpopular problem isn't
  evidence of an efficient solution.
- **You must place in the top 5% on both axes.** Your best accepted submission
  must be in the band by execution time *and* by memory. Either axis on its own
  is easy to hit by accident, because a small program sits near the memory floor
  whatever algorithm it uses.
- **A tie that overflows the band qualifies nobody.** Ranking includes ties, but
  never past the size of the band. When more users share the leading value than
  the band can hold, none of them qualify. This matters most for memory, where
  many solutions report the same interpreter baseline down to the kilobyte.

The rating worker re-derives the complete set of holders on each full
reconciliation pass. The cheaper incremental pass skips Clean Code entirely: it
loads only the problems touched that cycle, so it can neither rank a full solver
population nor safely revoke against a partial view.

A qualifier with no accepted submission carrying *both* measurements has no
representative to name, and so gets no badge. A submission missing one of the two
hasn't been shown to sit inside that band at all.

### Rock Cracker

Rock Cracker requires a solver to currently solve at least one problem whose
participant-only solve rate is strictly below 20%. Attempted users are distinct
submitters from raw `arena_submissions`, regardless of verdict or whether a
judgment exists. Solved users come from the current `arena_problem_solvers`
rows. Both populations exclude the problem owner through the same policy used
for problem difficulty. The rule uses the exact comparison
`solved_users * 5 < attempted_users`, so exactly 20% doesn't qualify.

An incremental badge pass evaluates the complete current populations of every
problem touched by either an accepted or non-accepted event. It may award every
current solver of a newly qualifying problem, but it never revokes against that
partial problem set. A full pass evaluates grouped attempt and solver counts for
the whole catalogue and derives the complete holder set. These queries read the
authoritative tables, not the batch-derived counters in `arena_problem_ratings`,
so rating-cycle timing doesn't affect the badge.

## First to Hand In and Almost Late follow the live ranking

Two badges record a position among solvers rather than a personal fact: First to
Hand In goes to the earliest solver of a problem through a set, Almost Late to
the last on-time one. The rating worker picks each winner from the submissions
that are Accepted *right now* — it reads each submission's most recent judgment,
not the verdict it had when the badge was awarded.

A rejudge therefore moves the badge. If the winning submission stops being
Accepted because a test case was added, a limit tightened, or a validator
corrected, the next full pass awards the badge to the solver who now holds the
position and revokes the previous holder. A pair has one holder, which is what
"first" and "last" mean.

`arena_problem_solvers` follows the same rule, and always has: solver rows follow
the verdict, so a user with no Accepted submission for a problem stops being
counted as a solver of it, and the judge reconciles the row on every Arena
judgment that finishes with a verdict or `FAILED`. That table is the input to a
measurement — problem difficulty and Rock Cracker eligibility are computed over
its rows — and the badge rules now read live data the same way.
