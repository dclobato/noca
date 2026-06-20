from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from shared import db_schema
from web.database import Base
from web.models.clarification import Clarification
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemCategory, ProblemLanguageLimit, ProblemTestCase
from web.models.submission import (
    HumanSubmissionConfirmation,
    Submission,
    SubmissionJudgment,
    SubmissionJudgmentAudit,
    SubmissionTestResult,
    VerdictOverride,
)
from web.models.users import Login_History, UberAdmin, User


def _assert_column_parity(shared_table, orm_table) -> None:
    shared_columns = {column.name: column for column in shared_table.columns}
    orm_columns = {column.name: column for column in orm_table.columns}

    assert set(shared_columns).issubset(set(orm_columns))

    for name, shared_column in shared_columns.items():
        orm_column = orm_columns[name]
        assert shared_column.nullable == orm_column.nullable, name
        assert shared_column.primary_key == orm_column.primary_key, name
        assert shared_column.type._type_affinity is orm_column.type._type_affinity, name


def test_web_models_map_directly_to_shared_tables() -> None:
    table_pairs = [
        (db_schema.clarifications, Clarification.__table__),
        (db_schema.contests, Contest.__table__),
        (db_schema.languages, Language.__table__),
        (db_schema.login_history, Login_History.__table__),
        (db_schema.problem_categories, ProblemCategory.__table__),
        (db_schema.problem_language_limits, ProblemLanguageLimit.__table__),
        (db_schema.problems, Problem.__table__),
        (db_schema.submission_judgment_audit, SubmissionJudgmentAudit.__table__),
        (db_schema.submission_judgments, SubmissionJudgment.__table__),
        (db_schema.submission_test_results, SubmissionTestResult.__table__),
        (db_schema.submissions, Submission.__table__),
        (db_schema.test_cases, ProblemTestCase.__table__),
        (db_schema.uber_admins, UberAdmin.__table__),
        (db_schema.users, User.__table__),
        (db_schema.verdict_overrides, VerdictOverride.__table__),
        (db_schema.human_submission_confirmations, HumanSubmissionConfirmation.__table__),
    ]

    for shared_table, orm_table in table_pairs:
        assert orm_table is shared_table


def test_web_base_metadata_uses_shared_metadata() -> None:
    assert Base.metadata is db_schema.metadata


def test_shared_schema_matches_web_orm_shape() -> None:
    table_pairs = [
        (db_schema.clarifications, Clarification.__table__),
        (db_schema.contests, Contest.__table__),
        (db_schema.human_submission_confirmations, HumanSubmissionConfirmation.__table__),
        (db_schema.languages, Language.__table__),
        (db_schema.login_history, Login_History.__table__),
        (db_schema.problem_categories, ProblemCategory.__table__),
        (db_schema.problem_language_limits, ProblemLanguageLimit.__table__),
        (db_schema.problems, Problem.__table__),
        (db_schema.submission_judgment_audit, SubmissionJudgmentAudit.__table__),
        (db_schema.submission_judgments, SubmissionJudgment.__table__),
        (db_schema.submission_test_results, SubmissionTestResult.__table__),
        (db_schema.submissions, Submission.__table__),
        (db_schema.test_cases, ProblemTestCase.__table__),
        (db_schema.uber_admins, UberAdmin.__table__),
        (db_schema.users, User.__table__),
        (db_schema.verdict_overrides, VerdictOverride.__table__),
    ]

    for shared_table, orm_table in table_pairs:
        _assert_column_parity(shared_table, orm_table)


def test_importing_autojudge_db_does_not_import_web_models(tmp_path: Path) -> None:
    statement_dir = tmp_path / "statements"
    testcase_dir = tmp_path / "testcases"
    statement_dir.mkdir(parents=True)
    testcase_dir.mkdir()

    pythonpath_entries = [str(Path.cwd())]
    existing_pythonpath = os.environ.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)

    env = os.environ.copy()
    env.update(
        {
            "NOCA_DB_USER": "test",
            "NOCA_DB_PASSWORD": "test",
            "NOCA_DB_SERVER": "localhost",
            "NOCA_DB_NAME": "test",
            "NOCA_WEB_PROBLEM_STATEMENT_DIR": str(statement_dir),
            "NOCA_WEB_PROBLEM_TESTCASE_DIR": str(testcase_dir),
            "NOCA_VALKEY_SERVER": "127.0.0.1",
            "NOCA_VALKEY_PORT": "6379",
            "NOCA_VALKEY_DB": "15",
            "PYTHONPATH": os.pathsep.join(pythonpath_entries),
        }
    )

    script = """
import json
import sys

import autojudge.db

print(json.dumps({
    "web.database": "web.database" in sys.modules,
    "web.models": "web.models" in sys.modules,
    "web.models.submission": "web.models.submission" in sys.modules,
}))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    loaded_modules = json.loads(result.stdout.strip())
    assert loaded_modules == {
        "web.database": False,
        "web.models": False,
        "web.models.submission": False,
    }
