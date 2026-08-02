#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Detection and validation of a problem statement's natural language.

This module is the single owner of three concerns:

  - **Detection**: ``lingua`` restricted to the three supported languages, run
    over a cleaned copy of the Markdown statement so code samples and formulas
    do not skew the result. It reports the *most probable* of the three (lingua's
    default relative distance), so a statement long enough to look at always gets
    an answer.
  - **Parsing**: turning the untrusted ``statement_language`` string that arrives
    from forms and problem packages into a ``StatementLanguage`` member, or a
    ``ValueError`` carrying a user-facing message.
  - **Resolution**: the rule deciding whether an author's explicit choice can be
    committed or must first be confirmed against what detection found.

Event-loop callers (routes, package import) must use
``detect_statement_language_async``; the synchronous entry point exists for the
one-off backfill script, which has no event loop to protect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import anyio.to_thread
from lingua import Language, LanguageDetector, LanguageDetectorBuilder

from shared.enumerations import StatementLanguage

MIN_DETECTION_CHARS = 40
"""Below this many cleaned characters, detection is not attempted at all.

Detection itself has no confidence threshold, so this length floor is the only
thing standing between a two-word statement and a coin-flip answer.
"""

MAX_DETECTION_CHARS = 20_000
"""Statements are truncated to this length before detection; more text adds no accuracy."""

_LINGUA_TO_STATEMENT_LANGUAGE: dict[Language, StatementLanguage] = {
    Language.PORTUGUESE: StatementLanguage.PT,
    Language.ENGLISH: StatementLanguage.EN,
    Language.SPANISH: StatementLanguage.ES,
}

_FENCED_CODE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_MATH_RE = re.compile(r"\$\$.*?\$\$|\$[^$\n]*\$", re.DOTALL)
_URL_RE = re.compile(r"https?://\S+")
_IMAGE_OR_LINK_TARGET_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_NOISE_RE = re.compile(r"[#>*_|~\-]+")


@dataclass(frozen=True)
class LanguageResolved:
    """The statement language may be committed as-is."""

    language: StatementLanguage | None


@dataclass(frozen=True)
class LanguageConflict:
    """The author's choice disagrees with detection and was not acknowledged."""

    chosen: StatementLanguage
    detected: StatementLanguage


def confirmation_token(chosen: StatementLanguage, detected: StatementLanguage) -> str:
    """Return the token that acknowledges one specific choice-versus-detection pair.

    Binding the acknowledgement to both values is what keeps a stale hidden form
    field from authorizing a *different* choice on a later submit.

    Args:
        chosen: The language the author selected.
        detected: The language detection found.

    Returns:
        str: The opaque confirmation token.
    """
    return f"{chosen.value}:{detected.value}"


def conflict_context(conflict: LanguageConflict) -> dict[str, str]:
    """Build the template context describing an unconfirmed language mismatch.

    Args:
        conflict: The unresolved choice-versus-detection pair.

    Returns:
        dict: Values and labels for the confirmation modal, plus the token that
        the "keep my choice" button must send back.
    """
    return {
        "chosen": conflict.chosen.value,
        "chosen_label": conflict.chosen.label,
        "detected": conflict.detected.value,
        "detected_label": conflict.detected.label,
        "token": confirmation_token(conflict.chosen, conflict.detected),
    }


def parse_statement_language(raw: str | None) -> StatementLanguage | None:
    """Parse an untrusted statement-language string.

    Args:
        raw: The value received from a form field or a problem package. Empty and
            ``None`` mean "not stated".

    Returns:
        StatementLanguage | None: The parsed member, or ``None`` when not stated.

    Raises:
        ValueError: When the value is neither empty nor a supported language code.
    """
    if raw is None:
        return None
    normalized = raw.strip()
    if not normalized:
        return None
    try:
        return StatementLanguage(normalized)
    except ValueError as exc:
        supported = ", ".join(member.value for member in StatementLanguage)
        raise ValueError(f"Statement language must be one of: {supported}.") from exc


def safe_statement_language(raw: str | None) -> StatementLanguage | None:
    """Parse a statement-language *filter* value, tolerating garbage.

    List pages must not break on a hand-typed or stale query string, so an
    unsupported value simply means "no filter".

    Args:
        raw: Raw ``language`` query value.

    Returns:
        StatementLanguage | None: The filter to apply, or ``None`` for all languages.
    """
    try:
        return parse_statement_language(raw)
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _detector() -> LanguageDetector:
    """Build the shared detector, restricted to the three supported languages.

    Restricting the language set is what keeps the loaded models small and the
    result accurate. Model loading happens on the first detection.
    """
    return LanguageDetectorBuilder.from_languages(
        Language.ENGLISH,
        Language.PORTUGUESE,
        Language.SPANISH,
    ).build()


def clean_statement(statement: str) -> str:
    """Strip Markdown noise that carries no language signal.

    Code blocks, inline code, math, URLs, and link targets are removed so that a
    statement whose prose is Portuguese is not dragged towards English by its
    sample code. The result is truncated to ``MAX_DETECTION_CHARS``.

    Args:
        statement: The raw Markdown statement.

    Returns:
        str: The cleaned, whitespace-collapsed text.
    """
    text = _FENCED_CODE_RE.sub(" ", statement)
    text = _MATH_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _IMAGE_OR_LINK_TARGET_RE.sub(r"\1", text)
    text = _MARKDOWN_NOISE_RE.sub(" ", text)
    return " ".join(text.split())[:MAX_DETECTION_CHARS]


def detect_statement_language(statement: str, *, title: str = "") -> StatementLanguage | None:
    """Detect the most probable language of a problem statement.

    Synchronous and CPU-bound; event-loop callers must use
    ``detect_statement_language_async`` instead. Too little text to judge yields
    ``None`` rather than an error, but a broken ``lingua`` installation is
    infrastructure failure and propagates.

    Detection is *not* thresholded: given enough text, lingua's most probable
    candidate is returned even when the margin is narrow, which is why an explicit
    author choice is confirmed rather than overridden.

    Args:
        statement: The raw Markdown statement.
        title: Optional problem title, prepended as extra signal.

    Returns:
        StatementLanguage | None: The detected language, or ``None`` when the
        cleaned text is shorter than ``MIN_DETECTION_CHARS`` or lingua recognized
        no language in it at all.
    """
    cleaned = clean_statement(f"{title}. {statement}" if title.strip() else statement)
    if len(cleaned) < MIN_DETECTION_CHARS:
        return None
    detected = _detector().detect_language_of(cleaned)
    if detected is None:
        return None
    return _LINGUA_TO_STATEMENT_LANGUAGE.get(detected)


async def detect_statement_language_async(statement: str, *, title: str = "") -> StatementLanguage | None:
    """Detect the statement language without blocking the event loop.

    Args:
        statement: The raw Markdown statement.
        title: Optional problem title, prepended as extra signal.

    Returns:
        StatementLanguage | None: Same as ``detect_statement_language``.
    """
    return await anyio.to_thread.run_sync(lambda: detect_statement_language(statement, title=title))


async def resolve_statement_language(
    *,
    chosen_raw: str | None,
    confirmed_raw: str | None,
    statement: str,
    title: str = "",
) -> LanguageResolved | LanguageConflict:
    """Decide which statement language to persist, or demand a confirmation.

    The rule is:

      1. nothing selected -> use whatever detection finds (possibly ``None``);
      2. selection with no detection, or detection agreeing -> use the selection;
      3. selection disagreeing with detection -> a conflict, unless the exact
         choice/detection pair was already acknowledged.

    Args:
        chosen_raw: The raw ``statement_language`` form value.
        confirmed_raw: The raw ``language_confirmed`` form value.
        statement: The raw Markdown statement being saved.
        title: The problem title being saved.

    Returns:
        LanguageResolved | LanguageConflict: The language to persist, or the pair
        the author still has to confirm.

    Raises:
        ValueError: When ``chosen_raw`` is not a supported language code.
    """
    chosen = parse_statement_language(chosen_raw)
    detected = await detect_statement_language_async(statement, title=title)
    if chosen is None:
        return LanguageResolved(detected)
    if detected is None or detected == chosen:
        return LanguageResolved(chosen)
    if (confirmed_raw or "").strip() == confirmation_token(chosen, detected):
        return LanguageResolved(chosen)
    return LanguageConflict(chosen=chosen, detected=detected)
