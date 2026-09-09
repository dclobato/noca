#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Single source of truth for the problem-package format's fixed values.

Every ceiling, field width, default, and member pattern the shared package
subsystem enforces lives here so the Arena and Contest domains cannot drift
apart on any of them.
"""

from __future__ import annotations

import re

from shared.services.sample_interactions import (
    INTERACTION_EXPLAIN_RE as SAMPLE_INTERACTION_EXPLAIN_RE,
)
from shared.services.sample_interactions import (
    INTERACTION_FILE_RE,
    MAX_INTERACTION_MEMBER_BYTES,
)

FORMAT_VERSION = 4
"""The package format version this build writes.

Version 4 adds the optional ``collection`` field: the slug of the Arena
collection (an event or a class) the problem is filed under. A problem belongs
to at most one. The field is absent in every earlier version, which reads as
"unfiled", so a version 3 package still imports unchanged.

Version 3 redefines ``language_limits[].time_limit_ms`` as the limit for *one*
repetition of a test case; before it, the same field was the budget shared by
all of them. Nothing in the file's shape changed, only what the number means, so
an older package is read and its value divided by the repetition count that
applies to it -- see ``web.services.problem_service.importing``, which is the
layer that knows what an omitted ``repetitions`` resolves to.

Version 2 added the explicit ``validator_type`` discriminator. Version 1 -- and
a package with no ``format_version`` key at all, which predates the key -- is
still read, with the strategy derived from ``custom_validator`` presence.
"""

LEGACY_FORMAT_VERSION = 1
"""The oldest package format version this build still reads."""

PER_RUN_TIME_LIMIT_VERSION = 3
"""First version whose ``language_limits[].time_limit_ms`` is a per-run limit.

An import of anything older has to divide that field by the repetition count in
effect for it; at or above this version the stored value is used as written.
"""

SUPPORTED_FORMAT_VERSIONS = (LEGACY_FORMAT_VERSION, 2, 3, FORMAT_VERSION)
"""Every package format version this build accepts on import."""

# --- Field-length caps (the larger of each historical pair, both domains) -----

MAX_TITLE_CHARS = 256
MAX_AUTHOR_CHARS = 256
MAX_NOTES_CHARS = 512
MAX_SOURCE_CHARS = 256
MAX_LICENSE_CHARS = 256
MAX_COLLECTION_CHARS = 128
MAX_IMAGE_CAPTION_CHARS = 512

# --- Limit defaults ----------------------------------------------------------

DEFAULT_TIME_LIMIT_MS = 1000
DEFAULT_MEMORY_LIMIT_KB = 262144
DEFAULT_PIDS_LIMIT = 64
DEFAULT_OUTPUT_LIMIT_BYTES = 65536

# --- Archive ceilings --------------------------------------------------------

MAX_UPLOAD_BYTES = 256 * 1024 * 1024
"""Compressed upload ceiling, enforced while the upload is streamed to disk."""

MAX_ARCHIVE_MEMBERS = 10_000
MAX_MEMBER_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_PDF_BYTES = 32 * 1024 * 1024
MAX_MARKDOWN_BYTES = 512 * 1024
MAX_EDITORIAL_BYTES = MAX_MARKDOWN_BYTES
MAX_EXPLANATION_BYTES = 512 * 1024
# Owned by sample_interactions, which is what actually enforces it; re-exported
# here so the ceilings table has one place to read.
MAX_INTERACTION_BYTES = MAX_INTERACTION_MEMBER_BYTES

# There is deliberately no compression-ratio ceiling: the per-member and
# aggregate uncompressed caps already bound extraction, and legitimate test-case
# data is often repetitive enough to exceed any ratio a bomb would.

MAX_TEST_CASES = 1000
MIN_TEST_CASE_ORDINAL = 1
MAX_TEST_CASE_ORDINAL = 1000

# --- Recognized members ------------------------------------------------------

PROBLEM_JSON_MEMBER = "problem.json"
STATEMENT_MD_MEMBER = "statement.md"
STATEMENT_PDF_MEMBER = "statement.pdf"
EDITORIAL_MD_MEMBER = "editorial.md"

TESTCASE_DIR_RE = re.compile(r"^(in|out)/0*([1-9]\d{0,3})(\.in|\.out|\.sol)?$", re.IGNORECASE)
TESTCASE_FLAT_RE = re.compile(r"^0*([1-9]\d{0,3})\.(in|out|sol)$", re.IGNORECASE)
EXPLANATION_RE = re.compile(r"^explanation/0*([1-9]\d{0,3})\.txt$", re.IGNORECASE)
INTERACTION_RE = INTERACTION_FILE_RE
INTERACTION_EXPLAIN_RE = SAMPLE_INTERACTION_EXPLAIN_RE

MACOS_JUNK_PREFIXES = ("__MACOSX/",)
MACOS_JUNK_NAMES = (".DS_Store",)

PDF_MAGIC = b"%PDF-"
