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
  in the top 5% for execution time or memory on a problem.
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
