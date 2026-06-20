#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Re-exports the sandbox path constants and LanguageConfig type from
shared.language_registry so that autojudge.runner and autojudge.pool can import them
without depending on the shared package directly.

Scope of this module
--------------------
- Sandbox path constants (SANDBOX_DIR, BINARY_PATH, …)
- LanguageConfig dataclass

Do NOT import language helpers (get_language, registry_from_rows, …) from here;
import them directly from shared.language_registry instead.
"""

from shared.language_registry import (
    BINARY_PATH,
    INPUT_PATH,
    JAR_PATH,
    SANDBOX_DIR,
    STDERR_PATH,
    STDOUT_PATH,
    LanguageConfig,
)

__all__ = [
    "SANDBOX_DIR",
    "BINARY_PATH",
    "JAR_PATH",
    "INPUT_PATH",
    "STDOUT_PATH",
    "STDERR_PATH",
    "LanguageConfig",
]
