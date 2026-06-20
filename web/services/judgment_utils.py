#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from shared.enumerations import JudgmentStatus
from web.models.submission import Submission, SubmissionJudgment


def get_active_judgment(submission: Submission) -> SubmissionJudgment | None:
    """Return the latest non-superseded, non-failed judgment for a submission.

    Args:
        submission: Submission whose judgments should be evaluated.

    Returns:
        The newest active judgment, or ``None`` when no active judgment exists.
    """
    candidates = [
        judgment
        for judgment in submission.judgments
        if judgment.status not in {JudgmentStatus.SUPERSEDED, JudgmentStatus.FAILED}
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda judgment: judgment.created_at)
