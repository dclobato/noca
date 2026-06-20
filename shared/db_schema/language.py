#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Float, Integer, String, Table

from ._base import _created_at_column, _updated_at_column, metadata

languages = Table(
    "languages",
    metadata,
    Column("id", String(64), primary_key=True, comment="Language unique identifier, e.g. 'python3'"),
    Column("name", String(255), nullable=False, comment="Language display name, e.g. 'Python 3'"),
    Column("icon", String(128), nullable=False, comment="Icon name on https://devicon.dev/, e.g. 'python'"),
    Column(
        "compile_image",
        String(255),
        nullable=False,
        comment="Docker image used for compilation, e.g. 'noca/judge-python3:compile'",
    ),
    Column(
        "run_image",
        String(255),
        nullable=False,
        comment="Docker image used for execution, e.g. 'noca/judge-python3:run'",
    ),
    Column(
        "compile_cmd",
        JSON,
        nullable=True,
        comment="Command to compile the source as a list of strings, e.g. ['python3', '-m', 'py_compile', '/src.py']",
    ),
    Column(
        "run_cmd",
        JSON,
        nullable=False,
        comment="Command to run the compiled code or script as a list of strings, e.g. ['python3', '-u', '/src.py']",
    ),
    Column(
        "source_filename",
        String(255),
        nullable=False,
        comment="Filename to use for the submitted source code when compiling/running, e.g. 'source.py'",
    ),
    Column(
        "artifact_path",
        String(255),
        nullable=False,
        comment="Path to the compiled artifact relative to the working directory, e.g. '/sandbox/source.py'",
    ),
    Column(
        "artifact_is_source",
        Boolean,
        nullable=False,
        default=False,
        comment="Whether the source file itself is the artifact to run (i.e. no separate compilation output)",
    ),
    Column("compile_timeout_s", Float, nullable=False, default=60.0, comment="Compilation timeout in seconds"),
    Column(
        "profiling_repetitions_default",
        Integer,
        nullable=False,
        default=3,
        comment="Default repetition count to use when auto-profiling this language",
    ),
    Column(
        "profiled_pids_floor",
        Integer,
        nullable=False,
        default=32,
        comment="Minimum PID limit to persist when auto-profiling this language",
    ),
    Column(
        "version",
        String(128),
        nullable=True,
        comment="version of compiler/interpreter used, e.g. 'gcc version 12.2.0 (Debian 12.2.0-14+deb12u1)'",
    ),
    Column("active", Boolean, nullable=False, default=True),
    _created_at_column(),
    _updated_at_column(),
)
