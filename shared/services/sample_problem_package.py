#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reference problem package offered for download from the import pages.

Built from code rather than committed as a binary so it cannot drift away from
the importer: the same ZIP is served by the Arena and Contest import pages, and
a round-trip test imports it through both importers.

The package deliberately carries fields from *both* domains (Arena's `source` /
`license`, the Contest's `color` / `language_limits`). Each importer reads
`problem.json` as a plain mapping and ignores keys it does not know, so one
package imports cleanly on either side.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any, Final

SAMPLE_PACKAGE_FILENAME: Final = "noca-sample-problem-a-plus-b.zip"

_STATEMENT: Final = """# A + B

Read two integers and print their sum.

## Input

A single line with two integers, `a` and `b`, separated by a space.

## Output

A single line with the value of `a + b`.
"""

#: (input, output, explanation) for each packaged test case.
_TEST_CASES: Final[tuple[tuple[str, str, str | None], ...]] = (
    ("1 2\n", "3\n", "The two numbers on the input line are added together."),
    ("2 5\n", "7\n", None),
    ("10 20\n", "30\n", None),
)

_METADATA: Final[dict[str, Any]] = {
    "title": "A + B",
    "author": "John Doe",
    "notes": "Sample problem",
    "license": "cc sa-by",
    "categories": ["sample", "math"],
    # Problem-level limits, used for any language without an override below.
    "time_limit_ms": 1000,
    "memory_limit_kb": 262144,
    "pids_limit": 64,
    "output_limit_in_bytes": 1048576,
    # Contest-only; ignored by the Arena importer.
    "color": "#4287f5",
    "language_limits": {
        "python3": {
            "time_limit_ms": 3000,
            "memory_limit_kb": 262144,
            "pids_limit": 64,
            "output_limit_in_bytes": 1048576,
            "repetitions": 3,
        },
        "rust": {
            "time_limit_ms": 1000,
            "memory_limit_kb": 131072,
            "pids_limit": 32,
            "output_limit_in_bytes": 1048576,
            "repetitions": 1,
        },
    },
}


def build_sample_problem_package() -> bytes:
    """Build the downloadable "A + B" reference package.

    Returns:
        The ZIP archive bytes, valid for both the Arena and Contest importers.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("problem.json", json.dumps(_METADATA, indent=2) + "\n")
        archive.writestr("statement.md", _STATEMENT)
        for ordinal, (tc_input, tc_output, explanation) in enumerate(_TEST_CASES, start=1):
            archive.writestr(f"in/{ordinal:03d}.in", tc_input)
            archive.writestr(f"out/{ordinal:03d}.out", tc_output)
            if explanation is not None:
                archive.writestr(f"explanation/{ordinal:03d}.txt", explanation)
    return buffer.getvalue()
