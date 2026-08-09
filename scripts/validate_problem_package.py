#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Offline structural validation of a problem package.

Runs the same disk-backed reader both importers use, so a package this script
accepts will not be refused for a *format* reason. What it deliberately cannot
check is anything that depends on the target install: whether the categories
exist, whether the validator's language is enabled, and whether the contest
allows the languages named in ``language_limits``. Those are decided at import.

Usage::

    uv run python scripts/validate_problem_package.py PATH

Exit codes: 0 when the package is structurally valid (warnings are printed and
do not fail the run), 1 when it is not.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shared.services.problem_package import PackageError, ProblemPackage, read_problem_package


def main(argv: list[str] | None = None) -> int:
    """Validate one package and report what it carries.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="Path to the problem package ZIP.")
    args = parser.parse_args(argv)

    if not args.path.is_file():
        print(f"error: {args.path} is not a file.", file=sys.stderr)
        return 1

    try:
        with read_problem_package(args.path) as staged:
            _report(staged.package)
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _report(package: ProblemPackage) -> None:
    """Print a summary of an accepted package."""
    metadata = package.metadata
    cases = package.test_cases
    samples = [case.ordinal for case in cases if case.is_sample]
    print(f"OK  format_version={metadata.format_version}  title={metadata.title!r}")
    print(f"    statement: {package.statement.kind}")
    print(f"    test cases: {len(cases)} ({len(samples)} public: {samples or '-'})")
    print(f"    categories: {', '.join(metadata.categories) or '-'}")
    print(f"    limits: {metadata.time_limit_ms} ms / {metadata.memory_limit_kb} KB / ", end="")
    print(f"{metadata.pids_limit} pids / {metadata.output_limit_in_bytes} bytes")
    print(f"    language limits: {', '.join(sorted(metadata.language_limits)) or '-'}")
    validator = package.validator
    print(f"    custom validator: {validator.language_id if validator else '-'}")
    print(f"    sample interactions: {len(package.interactions)}")
    print(f"    integrity manifest: {'present' if metadata.sha256 else 'absent'}")
    for warning in package.warnings:
        print(f"warning [{warning.code}]: {warning.message}")
    print()
    print("Note: category names, validator language, and contest-allowed languages")
    print("are only checked at import time, against the target install.")


if __name__ == "__main__":
    raise SystemExit(main())
