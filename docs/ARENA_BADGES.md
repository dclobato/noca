# Arena badges

Arena badges are achievement markers awarded to Arena users. The `rating`
module computes them through the badge-assignment loop in `rating/loops.py`,
which calls `shared.services.arena_badges.compute_badge_awards()`. Each badge
records the submission that earned it; see [Which submission earned a
badge](#which-submission-earned-a-badge).

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
  in the top 5% for execution time and memory on a problem. This badge can be
  taken away again; see [Dynamic badges](#dynamic-badges).
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
  problem whose solve rate is below 20%. This badge can be taken away again;
  see [Dynamic badges](#dynamic-badges).
- **Almost Late** Image filename: `almostlate.png`. Description: Be the last
  on-time solver for a problem in a problem set after the deadline passes.
- **Trimmer** Image filename: `trimmer.png`. Description: Get an accepted
  submission immediately after a presentation error on the same problem.

## Which submission earned a badge

Every badge row records the submission that earned it, in
`arena_user_badges.submission_id`, so you can answer "which submission got me One
Shot?" and not only "when did I get it?" The problem is reachable from there
through `arena_submissions.problem_id`, so no separate problem column is needed.

### Profile visibility

Arena profile pages expose badge provenance according to the viewer's access.
Your own profile links an anchored badge to the awarding submission. The admin
user profile uses the same submission link because Arena administrators can
already inspect user submissions. A public profile links only to the enabled
problem and never exposes the submission or its source code.

A badge with a `NULL` `submission_id` has no provenance link. Public profiles
also omit the link when the awarding problem is disabled. In both cases, the
badge otherwise renders normally.

Badges fall into four shapes, and each records something different:

- **Event badges** fire on one specific accepted submission: One Shot, Bug
  Killer, Bit Scrubber, Trimmer, Night Worker, Weekend Worker, Never Give Up,
  First Solver, First to Hand In, Almost Late, and Hello, World! These store the
  accepted submission that qualified. Loco Coder is the one
  event badge whose submission is *not* accepted: it stores the third non-accepted
  verdict in the 90-second window, which is exactly what the badge records.
- **Aggregate badges** are earned by a set of submissions crossing a threshold:
  the Strike, Problems, and Languages tiers, plus Full Clear and This Is the Way.
  These store the submission that *crossed* the threshold — the accepted
  submission that completed the streak, that reached your 25th distinct problem,
  that finished the set. That's a convention rather than a fact, because no
  single submission earned the badge on its own. Full Clear is worth calling out:
  its anchor is your first accepted submission on whichever of the set's problems
  you solved last, which is usually not the submission that prompted the rating
  worker to notice. When you've cleared more than one set, the badge names the
  set you completed earliest.
- **Clean Code stores nothing.** It records a rank held across several problems
  at once, and its qualifying set is rewritten on every full reconciliation, so
  any single submission captured at award time would be wrong by the next pass.
  Its `submission_id` stays `NULL` by design.
- **Rock Cracker records its current award event.** Its eligibility is dynamic,
  but a non-`NULL` anchor is not rewritten while the badge row survives. If the
  anchoring problem stops qualifying while another problem keeps the holder
  eligible, the anchor still identifies the solve that awarded that badge row.
  If the row is revoked and later awarded again, the new row records the earliest
  solve among the problems that qualify at that time.

Deleting a submission clears the anchor rather than the badge (`ON DELETE SET
NULL`), so you never lose a badge because a submission went away. That `NULL` is
not permanent, though: the next full reconciliation treats it like any other
unanchored row and fills it with whichever submission would award the badge
under today's data, which is a *different* submission from the one you lost.
Nothing in the schema distinguishes an anchor cleared by a delete from one that
was never set, and nothing in production deletes submissions today — the only
`DELETE` against `arena_submissions` is in a smoke-test script, and
`arena_submissions.user_id` is `ON DELETE RESTRICT`.

Deleting a problem set does **not** clear the anchor. It clears
`arena_submissions.problem_set_id`, which leaves the submission itself in place,
so a First to Hand In, Almost Late, or Full Clear row that already names one
keeps naming it. What it does mean is that such a row can never be *filled* if it
is still `NULL`: the rules find no set to rank against, so there is no anchor to
derive.

### Badges awarded before this column existed

Rows written before `submission_id` was added start out `NULL` and fill in on
their own. The rating worker's full reconciliation pass re-derives every badge
from current submission and catalogue state, and a re-derivation fills a `NULL`
anchor without ever rewriting one that's already set, so existing rows acquire
their anchors within one reconciliation interval. There's no data migration and
no one-off script to run.

That fill is best-effort. It yields the earliest submission that *would* award
the badge under today's data, which isn't always the submission that historically
awarded it. The two diverge when:

- **A rejudge changed the active verdict.** Badge eligibility reads each
  submission's latest judgment, so a submission that fired One Shot may not be
  accepted today, or an earlier attempt may be accepted now. This is the
  realistic case, and it's the same mechanism described in [First to Hand In and
  Almost Late can have more than one
  holder](#first-to-hand-in-and-almost-late-can-have-more-than-one-holder).
- **The problem set was deleted.** First to Hand In, Almost Late, and Full Clear
  have no set left to rank against, so a row that is still `NULL` stays `NULL`.
- **Set membership or deadlines moved.** Full Clear, First to Hand In, Almost
  Late, and First Solver all rank against the catalogue as it is now.
- **The awarding submission was deleted.** The anchor is cleared and then refilled
  with a different submission, as described above.

`awarded_at` is never rewritten, because badge dates are visible to users. A
filled row can therefore point at a submission whose timestamp disagrees with its
award timestamp. That skew is accepted rather than corrected.

Removing a student from a class doesn't endanger a badge or its anchor.
`arena_class_memberships` is an append-only event log, so a removal appends a row
instead of deleting anything, and the student's submissions keep their
`problem_set_id`. The badge rules never consult membership at all.

## Dynamic badges

Most badges are append-only: once you've solved 25 problems or fixed a runtime
error on your next submission, that stays true forever. Clean Code and Rock
Cracker instead follow current catalogue state, so the rating worker can revoke
them.

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
reconciliation pass and both awards and revokes to match it. The cheaper
incremental pass skips Clean Code entirely: it loads only the problems touched
that cycle, so it can neither rank a full solver population nor safely revoke
against a partial view.

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
the whole catalogue, awards missing holders, and revokes anyone who no longer
qualifies through any problem. These queries read the authoritative tables, not
the batch-derived counters in `arena_problem_ratings`, so rating-cycle timing
doesn't affect the badge.

Revocation deletes the badge row. If the user qualifies again later, the rating
worker inserts a new row with a new `awarded_at` and an anchor derived from the
current qualifying problems. A holder who never loses the row keeps its original
non-`NULL` anchor, even if a different problem now supplies the qualification.

## First to Hand In and Almost Late can have more than one holder

Two other badges record a position among solvers rather than a personal fact,
and are append-only anyway. That has a visible consequence in the number of
holders.

Both badges name a *position* within one problem set: First to Hand In goes to
the earliest solver of a problem through a set, Almost Late to the last on-time
one. The rating worker picks each winner from the submissions that are Accepted
*right now* — it reads each submission's most recent judgment, not the verdict
it had when the badge was awarded.

A rejudge can change that. If the winning submission stops being Accepted
because a test case was added, a limit tightened, or a validator corrected, the
next cycle awards the badge to the solver who now holds the position. The
previous holder keeps theirs, so the problem can end up with two people holding
First to Hand In.

This is intentional for these position badges. A rejudge is nearly always
prompted by a defect in the problem rather than anything the student did, and
the badge is an honor for what they did at the time they did it, so it is never
taken back.

`arena_problem_solvers` follows the opposite rule, and the split is deliberate:
solver rows follow the verdict, while append-only badge rules preserve honors.
That table is the input to a measurement rather than an honor -- problem
difficulty and Rock Cracker eligibility are computed over its rows -- so a user
with no Accepted submission for a problem stops being counted as a solver of
it, and the judge reconciles the row on every Arena judgment that finishes with
a verdict or `FAILED`. An already-earned Problems 25 or Full Clear is still
never revoked, even once the solve behind it no longer counts.
