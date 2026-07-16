#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Render interactive-review context into an inline AI prompt section.

Interactive problems have no fixed expected output, so what tells the model why a
submission failed is the *conversation* it had with the validator. This module
turns the :class:`aiassistant.db.queries.InteractiveContext` for a submission into
a bounded, untrusted-wrapped text section appended to the AI review user prompt.
The transcript is inlined (not uploaded as a file) because it is already bounded
by the judge and small once head/tail-capped, avoiding extra OpenAI file uploads
and their per-batch cleanup bookkeeping.
"""

from __future__ import annotations

from aiassistant.db.interactive_queries import InteractiveContext
from aiassistant.guardrails import wrap_untrusted_review_artifact

# Head/tail line caps applied on top of the judge's own 256 KB transcript cap, so
# a long conversation still fits a small, predictable input-token budget. The tail
# is kept because for a TLE/WA it is where the interaction actually goes wrong.
_TRANSCRIPT_HEAD_LINES = 60
_TRANSCRIPT_TAIL_LINES = 60
_MAX_SAMPLES = 3
_SAMPLE_LINE_CAP = 40


def _cap_lines(text: str, head: int, tail: int) -> tuple[str, bool]:
    """Return *text* limited to its first *head* and last *tail* lines.

    Args:
        text: Multi-line text to cap.
        head: Number of leading lines to keep.
        tail: Number of trailing lines to keep.

    Returns:
        A ``(capped_text, was_capped)`` pair; the elided middle is marked with an
        ellipsis line naming how many lines were dropped.
    """
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text, False
    dropped = len(lines) - head - tail
    kept = lines[:head] + [f"... [{dropped} lines omitted] ..."] + lines[-tail:]
    return "\n".join(kept), True


def build_interactive_note(ctx: InteractiveContext) -> str:
    """Build the inline ``<interactive_context>`` prompt section for a review.

    Args:
        ctx: The interactive context fetched for the submission.

    Returns:
        A prompt section with a trusted framing lead-in plus untrusted-wrapped
        recorded and sample conversations. Empty string when there is nothing
        useful to show (no transcript and no samples).
    """
    if ctx.transcript_text is None and not ctx.sample_transcripts:
        return ""

    parts: list[str] = [
        "This is an INTERACTIVE problem judged by a custom validator: the program "
        "talks to the validator instead of reading a fixed input and printing a "
        'fixed output. In the conversations below, lines starting with "> " are the '
        'validator speaking to the program and lines starting with "< " are the '
        "program replying."
    ]

    if ctx.transcript_text is not None:
        verdict = ctx.verdict or "unknown"
        case = f" on secret test case #{ctx.failing_case_ordinal}" if ctx.failing_case_ordinal else ""
        lead = f"Verdict for this submission: {verdict}{case}."
        if ctx.crash_reason:
            lead += f" Judge crash reason: {ctx.crash_reason}."
        capped, was_capped = _cap_lines(ctx.transcript_text, _TRANSCRIPT_HEAD_LINES, _TRANSCRIPT_TAIL_LINES)
        if ctx.transcript_truncated or was_capped:
            lead += " (This recorded conversation is only a partial excerpt.)"
        body = capped
        if ctx.stderr_excerpt:
            body += f"\n\n[program stderr excerpt]\n{ctx.stderr_excerpt.strip()}"
        parts.append(f"{lead}\n{wrap_untrusted_review_artifact('recorded_interaction', body)}")

    if ctx.sample_transcripts:
        blocks: list[str] = ["Author-provided example conversations showing correct interaction:"]
        for index, (text, explanation) in enumerate(ctx.sample_transcripts[:_MAX_SAMPLES], start=1):
            capped, _ = _cap_lines(text, _SAMPLE_LINE_CAP, _SAMPLE_LINE_CAP)
            block = f"--- Example {index} ---\n{capped}"
            if explanation:
                block += f"\n(explanation: {explanation.strip()})"
            blocks.append(block)
        parts.append(wrap_untrusted_review_artifact("sample_interactions", "\n\n".join(blocks)))

    return "<interactive_context>\n" + "\n\n".join(parts) + "\n</interactive_context>"
