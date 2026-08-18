#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reference problem package offered for download from the import pages.

Built from code rather than committed as a binary so it cannot drift away from
the reader: the same ZIP is served by the Arena and Contest import pages, and a
round-trip test imports it through both importers.

It is written through the shared writer, so it carries every version-2 key, an
optional editorial with an independent digest, a valid legacy ``sha256``
manifest, and at least one public test case — exactly what a
package produced by a real export looks like. The fields deliberately span
*both* domains (Arena's ``source`` / ``license`` / ``statement_language``, the
Contest's ``color`` / ``language_limits``), because the format is their union
and each importer keeps what its own schema can store.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Final

from shared.enumerations import ProblemValidatorType
from shared.services.problem_package.constants import FORMAT_VERSION
from shared.services.problem_package.model import (
    PackageLanguageLimit,
    PackageMetadata,
    PackageStatement,
    PackageTestCase,
    ProblemPackage,
)
from shared.services.problem_package.writer import build_package

SAMPLE_PACKAGE_FILENAME: Final = "noca-sample-problem-a-plus-b.zip"

_STATEMENT: Final = """# A + B

Read two integers and print their sum.

## Input

A single line with two integers, `a` and `b`, separated by a space.

## Output

A single line with the value of `a + b`.
"""

_EDITORIAL: Final = """# Editorial

Read the two integers, add them, and print the result. The algorithm runs in constant time and uses constant space.
"""

#: (input, output, explanation, is_sample) for each packaged test case.
_TEST_CASES: Final[tuple[tuple[str, str, str | None, bool], ...]] = (
    ("1 2\n", "3\n", "The two numbers on the input line are added together.", True),
    ("2 5\n", "7\n", None, False),
    ("10 20\n", "30\n", None, False),
)

_LANGUAGE_LIMITS: Final[dict[str, PackageLanguageLimit]] = {
    "python3": PackageLanguageLimit(
        time_limit_ms=3000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=1048576,
        repetitions=3,
    ),
    "rust": PackageLanguageLimit(
        time_limit_ms=1000,
        memory_limit_kb=131072,
        pids_limit=32,
        output_limit_in_bytes=1048576,
        repetitions=1,
    ),
}


def build_sample_problem_package(destination: Path) -> Path:
    """Write the downloadable "A + B" reference package to ``destination``.

    Args:
        destination: Path the ZIP is written to; the caller owns it, exactly as
            with a real export.

    Returns:
        The written path.
    """
    with tempfile.TemporaryDirectory(prefix="noca-pkg-sample-") as scratch:
        root = Path(scratch)
        cases: list[PackageTestCase] = []
        for ordinal, (tc_input, tc_output, explanation, is_sample) in enumerate(_TEST_CASES, start=1):
            input_path = root / f"{ordinal:03d}.in"
            output_path = root / f"{ordinal:03d}.out"
            input_path.write_text(tc_input, encoding="utf-8")
            output_path.write_text(tc_output, encoding="utf-8")
            cases.append(
                PackageTestCase(
                    ordinal=ordinal,
                    is_sample=is_sample,
                    input_path=input_path,
                    output_path=output_path,
                    explanation=explanation,
                )
            )
        return build_package(_sample_package(tuple(cases)), destination, profile="full")


def _sample_package(cases: tuple[PackageTestCase, ...]) -> ProblemPackage:
    """Build the reference package's frozen value."""
    metadata = PackageMetadata(
        format_version=FORMAT_VERSION,
        # The reference package is an ordinary token-compared problem.
        validator_type=ProblemValidatorType.STANDARD,
        title="A + B",
        author="John Doe",
        notes="Sample problem",
        source="NOCA documentation",
        license="cc sa-by",
        color="#4287f5",
        hide_author_show_source=False,
        statement_language="en",
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=1048576,
        categories=("sample", "math"),
        sample_testcases=tuple(case.ordinal for case in cases if case.is_sample),
        image=None,
        image_caption=None,
        language_limits=_LANGUAGE_LIMITS,
        custom_validator=None,
        sha256={},
        editorial=None,
    )
    return ProblemPackage(
        metadata=metadata,
        statement=PackageStatement(kind="md", path=None, text=_STATEMENT),
        test_cases=cases,
        image=None,
        validator=None,
        interactions=(),
        warnings=(),
        editorial=_EDITORIAL,
    )
