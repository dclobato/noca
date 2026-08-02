#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for statement-language detection, parsing, and conflict resolution."""

from __future__ import annotations

import pytest

from arena.services.statement_language_service import (
    LanguageConflict,
    LanguageResolved,
    clean_statement,
    confirmation_token,
    detect_statement_language,
    parse_statement_language,
    resolve_statement_language,
    safe_statement_language,
)
from shared.enumerations import StatementLanguage

_PT = """# Soma de dois números

Dado dois números inteiros, escreva um programa que calcule a soma deles e
imprima o resultado na saída padrão do seu programa.
"""

_EN = """# Sum of two numbers

Given two integers, write a program that computes their sum and prints the
result to the standard output of your program.
"""

_ES = """# Suma de dos números

Dados dos números enteros, escriba un programa que calcule su suma e imprima
el resultado en la salida estándar de su programa.
"""


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        (_PT, StatementLanguage.PT),
        (_EN, StatementLanguage.EN),
        (_ES, StatementLanguage.ES),
    ],
)
def test_detect_recognizes_each_supported_language(statement: str, expected: StatementLanguage) -> None:
    assert detect_statement_language(statement) == expected


def test_detect_ignores_code_blocks_and_urls() -> None:
    """Sample code is English-looking noise and must not drag detection along."""
    statement = (
        _PT
        + '\n```\nint main() { int a, b; scanf("%d %d", &a, &b); printf("%d", a + b); }\n```\n'
        + "See https://example.com/problems/sum for the original statement.\n"
    )
    assert detect_statement_language(statement) == StatementLanguage.PT


def test_clean_statement_strips_markdown_noise() -> None:
    cleaned = clean_statement("# Title\n\n`code`\n\n```\nblock\n```\n\n$x^2$\n\nReal prose here.")
    assert "block" not in cleaned
    assert "code" not in cleaned
    assert "Real prose here." in cleaned


@pytest.mark.parametrize("statement", ["", "   ", "Oi.", "# T\n\nSoma."])
def test_detect_returns_none_for_insufficient_text(statement: str) -> None:
    assert detect_statement_language(statement) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pt", StatementLanguage.PT),
        ("en", StatementLanguage.EN),
        ("es", StatementLanguage.ES),
        (" pt ", StatementLanguage.PT),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_parse_accepts_supported_values(raw: str | None, expected: StatementLanguage | None) -> None:
    assert parse_statement_language(raw) == expected


@pytest.mark.parametrize("raw", ["xx", "PT", "portuguese", "pt-BR", "1", "pt,en"])
def test_parse_rejects_unsupported_values(raw: str) -> None:
    with pytest.raises(ValueError, match="Statement language must be one of"):
        parse_statement_language(raw)


@pytest.mark.parametrize("raw", ["xx", "PT", "", None])
def test_safe_statement_language_never_raises(raw: str | None) -> None:
    """A list filter must survive a hand-typed query string."""
    assert safe_statement_language(raw) in (None, StatementLanguage.PT)


def test_confirmation_token_binds_both_values() -> None:
    token = confirmation_token(StatementLanguage.EN, StatementLanguage.PT)
    assert token == "en:pt"
    assert token != confirmation_token(StatementLanguage.ES, StatementLanguage.PT)


@pytest.mark.asyncio
async def test_resolve_uses_detection_when_nothing_selected() -> None:
    resolution = await resolve_statement_language(chosen_raw="", confirmed_raw="", statement=_PT)
    assert resolution == LanguageResolved(StatementLanguage.PT)


@pytest.mark.asyncio
async def test_resolve_accepts_a_choice_matching_detection() -> None:
    resolution = await resolve_statement_language(chosen_raw="pt", confirmed_raw="", statement=_PT)
    assert resolution == LanguageResolved(StatementLanguage.PT)


@pytest.mark.asyncio
async def test_resolve_accepts_a_choice_when_detection_is_empty() -> None:
    resolution = await resolve_statement_language(chosen_raw="es", confirmed_raw="", statement="Oi.")
    assert resolution == LanguageResolved(StatementLanguage.ES)


@pytest.mark.asyncio
async def test_resolve_reports_an_unconfirmed_mismatch() -> None:
    resolution = await resolve_statement_language(chosen_raw="en", confirmed_raw="", statement=_PT)
    assert resolution == LanguageConflict(chosen=StatementLanguage.EN, detected=StatementLanguage.PT)


@pytest.mark.asyncio
async def test_resolve_accepts_an_acknowledged_mismatch() -> None:
    resolution = await resolve_statement_language(chosen_raw="en", confirmed_raw="en:pt", statement=_PT)
    assert resolution == LanguageResolved(StatementLanguage.EN)


@pytest.mark.asyncio
async def test_resolve_rejects_a_token_issued_for_another_choice() -> None:
    """A stale acknowledgement must not authorize a different language."""
    resolution = await resolve_statement_language(chosen_raw="es", confirmed_raw="en:pt", statement=_PT)
    assert resolution == LanguageConflict(chosen=StatementLanguage.ES, detected=StatementLanguage.PT)


@pytest.mark.asyncio
async def test_resolve_rejects_an_unsupported_choice() -> None:
    with pytest.raises(ValueError, match="Statement language must be one of"):
        await resolve_statement_language(chosen_raw="klingon", confirmed_raw="", statement=_PT)
