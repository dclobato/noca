#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Random animal-adjective username generation.

Generates pseudonymous usernames such as ``tigre-astuto-074`` from the shared
animal and adjective word lists (``shared/animais.txt`` and
``shared/adjetivos.txt``). Every generated handle is normalized to lowercase
ASCII (diacritics stripped from the source words) so it is URL-safe and
free of transliteration surprises, matching the accent-stripping idiom used
by ``arena.services.admin_category_service.normalize_slug``.

This module is a pure word-list generator: it performs no I/O beyond reading
the two bundled word lists and has no notion of uniqueness. A caller needing
a globally unique handle (e.g. Arena's ``username_service``) is responsible
for retrying on collision and enforcing the database's ``UNIQUE`` constraint.
"""

from __future__ import annotations

import secrets
import unicodedata
from functools import lru_cache
from pathlib import Path

_ANIMALS_PATH = Path(__file__).resolve().parents[1] / "animais.txt"
_ADJECTIVES_PATH = Path(__file__).resolve().parents[1] / "adjetivos.txt"

_MIN_NUMBER = 0
_MAX_NUMBER = 999


def _strip_accents_lower(value: str) -> str:
    """Fold text to lowercase ASCII, dropping diacritics.

    Args:
        value: Raw text, possibly accented.

    Returns:
        The lowercase text with combining marks removed.
    """
    normalized = unicodedata.normalize("NFD", value.strip().lower())
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


@lru_cache(maxsize=2)
def _load_words(path: Path) -> tuple[str, ...]:
    """Load a one-word-per-line word list, normalized to lowercase ASCII.

    Args:
        path: Absolute path to the word list file.

    Returns:
        The normalized, non-empty words, in file order.

    Raises:
        ValueError: If the file contains no usable words.
    """
    words = tuple(_strip_accents_lower(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if not words:
        raise ValueError(f"Word list {path} contains no usable words.")
    return words


def generate_username() -> str:
    """Generate a random ``animal-adjetivo-NNN`` username.

    The animal and adjective are drawn independently from the shared
    ``animais.txt`` and ``adjetivos.txt`` word lists using a
    cryptographically secure random source (``secrets``), and suffixed with
    a zero-padded three-digit number between 000 and 999 drawn the same way.

    The three-digit suffix widens the key space to 50 x 50 x 1000 = 2 500 000
    handles. A two-digit suffix would give 250 000, which is thin enough that
    a caller retrying on collision would start needing many attempts well
    before the platform outgrew the word lists.

    Returns:
        A username in the form ``"<animal>-<adjetivo>-<NNN>"``, e.g.
        ``"tigre-astuto-074"``. Always lowercase ASCII.
    """
    animal = secrets.choice(_load_words(_ANIMALS_PATH))
    adjective = secrets.choice(_load_words(_ADJECTIVES_PATH))
    number = secrets.randbelow(_MAX_NUMBER - _MIN_NUMBER + 1) + _MIN_NUMBER
    return f"{animal}-{adjective}-{number:03d}"
