# Arena badges

Arena badges are achievement markers awarded to Arena users. The `rating`
module computes them through the badge-assignment loop in `rating/loops.py`,
which calls `shared.services.arena_badges.compute_badge_awards()`.

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
  in the top 5% for execution time and memory on a problem. This is the only
  badge that can be taken away again; see [Clean Code is
  dynamic](#clean-code-is-dynamic).
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
  problem whose solve rate is below 20%.
- **Almost Late** Image filename: `almostlate.png`. Description: Be the last
  on-time solver for a problem in a problem set after the deadline passes.
- **Trimmer** Image filename: `trimmer.png`. Description: Get an accepted
  submission immediately after a presentation error on the same problem.

## Clean Code is dynamic

Every badge except Clean Code records something that happened, so the ledger is
append-only: once you've solved 25 problems or fixed a runtime error on your
next submission, that stays true forever. Clean Code records a *rank* instead,
and a rank moves as other people submit faster solutions. It's therefore the one
badge the rating worker can revoke.

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
