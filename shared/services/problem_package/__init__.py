#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The single, strictly-validated problem-package subsystem.

Both problem domains consume packages exclusively through this package. Every
format decision — integer coercion, null semantics, string lengths, UTF-8,
image paths, archive safety, export field sets — is made here exactly once, so
the Arena and Contest sides cannot drift apart on any of them.
"""

from shared.services.problem_package.constants import (
    DEFAULT_MEMORY_LIMIT_KB,
    DEFAULT_OUTPUT_LIMIT_BYTES,
    DEFAULT_PIDS_LIMIT,
    DEFAULT_TIME_LIMIT_MS,
    FORMAT_VERSION,
    MAX_AUTHOR_CHARS,
    MAX_IMAGE_CAPTION_CHARS,
    MAX_LICENSE_CHARS,
    MAX_NOTES_CHARS,
    MAX_SOURCE_CHARS,
    MAX_TITLE_CHARS,
    MAX_UPLOAD_BYTES,
)
from shared.services.problem_package.errors import PackageError, PackageWarning
from shared.services.problem_package.journal import (
    ImportJournal,
    PromotionState,
    build_journal,
    clear_journal,
    reconcile_journals,
    write_journal,
)
from shared.services.problem_package.model import (
    PackageImage,
    PackageLanguageLimit,
    PackageMetadata,
    PackageStatement,
    PackageTestCase,
    ProblemPackage,
    StagedPackage,
    ValidatorSpec,
)
from shared.services.problem_package.reader import open_problem_package, read_problem_package
from shared.services.problem_package.staging import (
    ArtifactPromotion,
    PackageStagingArea,
    PackageStagingError,
    hidden_sibling,
    new_token,
)
from shared.services.problem_package.writer import PackageProfile, build_package

__all__ = [
    "DEFAULT_MEMORY_LIMIT_KB",
    "DEFAULT_OUTPUT_LIMIT_BYTES",
    "DEFAULT_PIDS_LIMIT",
    "DEFAULT_TIME_LIMIT_MS",
    "FORMAT_VERSION",
    "MAX_AUTHOR_CHARS",
    "MAX_IMAGE_CAPTION_CHARS",
    "MAX_LICENSE_CHARS",
    "MAX_NOTES_CHARS",
    "MAX_SOURCE_CHARS",
    "MAX_TITLE_CHARS",
    "MAX_UPLOAD_BYTES",
    "ArtifactPromotion",
    "ImportJournal",
    "PackageError",
    "PackageImage",
    "PackageLanguageLimit",
    "PackageMetadata",
    "PackageProfile",
    "PackageStagingArea",
    "PackageStagingError",
    "PackageStatement",
    "PackageTestCase",
    "PackageWarning",
    "ProblemPackage",
    "PromotionState",
    "StagedPackage",
    "ValidatorSpec",
    "build_journal",
    "build_package",
    "clear_journal",
    "hidden_sibling",
    "new_token",
    "open_problem_package",
    "read_problem_package",
    "reconcile_journals",
    "write_journal",
]
