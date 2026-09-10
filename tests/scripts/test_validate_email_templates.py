#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the email template override authoring and validation CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.validate_email_templates import main
from shared.services.email_templates import default_digest


def _definition(key: str) -> object:
    """Return one Arena catalogue entry by key."""
    from arena.email_templates import ARENA_EMAIL_TEMPLATE_DEFINITIONS

    return next(item for item in ARENA_EMAIL_TEMPLATE_DEFINITIONS if item.key == key)


def test_validate_reports_a_missing_namespace_as_defaults(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A root without the module's directory is a valid, override-free install."""
    assert main(["arena", str(tmp_path)]) == 0

    assert "renders from its packaged default" in capsys.readouterr().out


def test_export_writes_the_packaged_default_with_its_digest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The exported file is a valid starting point that records what it came from."""
    assert main(["arena", str(tmp_path), "--export", "reset_password"]) == 0

    written = (tmp_path / "arena" / "reset_password.toml").read_text(encoding="utf-8")
    assert f'based_on = "{default_digest(_definition("reset_password"))}"' in written
    assert "wrote" in capsys.readouterr().out
    assert main(["arena", str(tmp_path)]) == 0


def test_export_refuses_to_clobber_without_force(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An operator's edited file is not overwritten by a second export."""
    main(["arena", str(tmp_path), "--export", "reset_password"])
    edited = tmp_path / "arena" / "reset_password.toml"
    edited.write_text('subject = "Mine"\nbody = "Use {url}, {nome}."\n', encoding="utf-8")

    assert main(["arena", str(tmp_path), "--export", "reset_password"]) == 0
    assert edited.read_text(encoding="utf-8").startswith('subject = "Mine"')
    assert "skipped" in capsys.readouterr().out

    assert main(["arena", str(tmp_path), "--export", "reset_password", "--force"]) == 0
    assert "Reset your Arena password" in edited.read_text(encoding="utf-8")


def test_validation_errors_fail_the_run_and_name_each_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI reports what startup would refuse, before a deploy finds out."""
    namespace = tmp_path / "arena"
    namespace.mkdir()
    (namespace / "reset_password.toml").write_text('subject = "Nova"\nbody = "Sem link, {nome}"\n', encoding="utf-8")
    (namespace / "not_a_key.toml").write_text('subject = "X"\nbody = "Y"\n', encoding="utf-8")

    assert main(["arena", str(tmp_path)]) == 1

    errors = capsys.readouterr().err
    assert "not_a_key.toml: unknown template key 'not_a_key'" in errors
    assert "missing required placeholders: url" in errors
    assert "would refuse to start" in errors


def test_stale_based_on_is_a_warning_not_a_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Drift must be visible without blocking an otherwise valid tree."""
    main(["arena", str(tmp_path), "--export", "reset_password"])
    path = tmp_path / "arena" / "reset_password.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f'based_on = "{default_digest(_definition("reset_password"))}"',
            'based_on = "0123456789abcdef"',
        ),
        encoding="utf-8",
    )

    assert main(["arena", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert "was written from default 0123456789abcdef" in captured.err
    assert "is valid" in captured.out


def test_preview_renders_sample_values_and_names_the_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A preview shows the wording that would go out, and where it came from."""
    assert main(["arena", str(tmp_path), "--preview", "reset_password"]) == 0
    first = capsys.readouterr().out
    assert "[packaged default]" in first
    assert "Subject: Reset your Arena password" in first

    namespace = tmp_path / "arena"
    namespace.mkdir(exist_ok=True)
    (namespace / "reset_password.toml").write_text(
        'subject = "Nova senha"\nbody = "Ola {nome}, use {url}."\n', encoding="utf-8"
    )

    assert main(["arena", str(tmp_path), "--preview", "reset_password"]) == 0
    second = capsys.readouterr().out
    assert "[override]" in second
    assert "Subject: Nova senha" in second


def test_list_reports_each_key_and_its_placeholders(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The contract an override must honour is discoverable without the source."""
    assert main(["web", str(tmp_path), "--list"]) == 0

    out = capsys.readouterr().out
    assert "send_credentials" in out
    assert "required: contest_login_url, password, username" in out


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    """Naming a key that does not exist is an error, not an empty run."""
    with pytest.raises(Exception, match="unknown template keys: nope"):
        main(["arena", str(tmp_path), "--export", "nope"])


def test_preview_does_not_claim_a_rejected_file_is_in_use(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A file the renderer refused must not be labelled as the override in effect.

    That label is the whole point of the preview: calling a rejected file an
    override would confirm precisely the wording that is not being sent.
    """
    namespace = tmp_path / "arena"
    namespace.mkdir()
    (namespace / "reset_password.toml").write_text(
        'subject = "Nova senha"\nbody = "Sem link para {nome}."\n', encoding="utf-8"
    )

    assert main(["arena", str(tmp_path), "--preview", "reset_password"]) == 0

    out = capsys.readouterr().out
    assert "override rejected" in out
    assert "[override]" not in out
    assert "Subject: Reset your Arena password" in out


def test_preview_flags_a_stale_override_it_is_using(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Drift is visible in the preview that renders the drifted file."""
    namespace = tmp_path / "arena"
    namespace.mkdir()
    (namespace / "reset_password.toml").write_text(
        'subject = "Nova senha"\nbody = "Ola {nome}, use {url}."\nbased_on = "0123456789abcdef"\n',
        encoding="utf-8",
    )

    assert main(["arena", str(tmp_path), "--preview", "reset_password"]) == 0

    out = capsys.readouterr().out
    assert "[override, stale based_on]" in out
    assert "Subject: Nova senha" in out


def test_the_cli_runs_by_path_for_both_modules(tmp_path: Path) -> None:
    """`uv run scripts/validate_email_templates.py web ...` must resolve `web`.

    Running a file by path puts `scripts/` first on `sys.path`, where
    `scripts/web/` is a regular package that shadows the real top-level `web`.
    Every test in this module imports the CLI instead, so none of them can see
    that: this one runs the script the way the documentation tells an operator to.
    """
    script = Path(__file__).resolve().parents[2] / "scripts" / "validate_email_templates.py"

    for module in ("web", "arena"):
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(script), module, str(tmp_path), "--list"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "required:" in result.stdout


def test_misordered_arguments_explain_the_ordering_rule(capsys: pytest.CaptureFixture[str]) -> None:
    """The failure an operator actually hits must name its cause.

    Argparse's own message reports the positionals as missing, which is true and
    tells nobody that the option ahead of them ate both.
    """
    with pytest.raises(SystemExit):
        main(["--export", "reset_password", "arena", "/srv/overrides"])

    errors = capsys.readouterr().err
    assert "must come first" in errors
    assert "--export and --preview take an optional list of keys" in errors
