#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""NOCA rating worker: single-replica Arena background recomputation process.

Runs several independent ``asyncio`` loops (see ``rating.loops``):

- the problem → user → affiliation rating recomputation chain
  (``run_problem_rating_loop`` / ``run_user_rating_loop`` /
  ``run_affiliation_rating_loop``), with the user loop also rebuilding submission
  heatmaps;
- ``run_problem_stats_loop`` — precomputes per-problem statistics snapshots;
- ``run_badge_assignment_loop`` — awards Arena gamification badges from Accepted
  submissions (``shared.services.arena_badges``).

Single-replica by design so each cycle runs exactly once regardless of how many
Arena replicas are deployed.
"""
