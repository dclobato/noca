#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for filesystem-backed email template overrides."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from shared.services.email_templates import (
    EmailTemplate,
    EmailTemplateDefinition,
    EmailTemplateError,
    EmailTemplateRegistry,
    FilesystemOverrideSource,
    build_module_registry,
    default_digest,
    ensure_valid_override_tree,
    inspect_email_templates,
    overrides,
    validate_override_tree,
)

_DEFAULT = 'subject = "Reset your password"\nbody = "Hello {name}, open {url}."\n'
_OVERRIDE = 'subject = "Password reset"\nbody = "Hi {name}! Use {url} within the hour."\n'


@pytest.fixture
def definition(tmp_path: Path) -> EmailTemplateDefinition:
    """Return a one-key catalogue whose packaged default is on disk."""
    packaged = tmp_path / "packaged" / "reset_password.toml"
    packaged.parent.mkdir(parents=True)
    packaged.write_text(_DEFAULT, encoding="utf-8")
    return EmailTemplateDefinition(
        key="reset_password",
        default_path=packaged,
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"name", "url"}),
        required_placeholders=frozenset({"url"}),
        sample_values={"name": "Ada", "url": "https://example.test/reset"},
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Return an override root holding both module namespaces."""
    root = tmp_path / "overrides"
    (root / "web").mkdir(parents=True)
    (root / "arena").mkdir(parents=True)
    return root


def _write(path: Path, content: str) -> None:
    """Publish a template the way an operator should: write, then rename."""
    sibling = path.with_suffix(".toml.new")
    sibling.write_text(content, encoding="utf-8")
    sibling.rename(path)


def _render(definition: EmailTemplateDefinition, root: Path | None, namespace: str = "web") -> str:
    """Render the fixture key's body through a freshly built registry."""
    registry = build_module_registry((definition,), namespace=namespace, override_root=root)
    return registry.render("reset_password", brand_name="NOCA", context=definition.sample_values).body


def test_no_configured_directory_renders_the_packaged_default(definition: EmailTemplateDefinition) -> None:
    """An install that never opts in behaves exactly as before overrides existed."""
    assert _render(definition, None).startswith("Hello Ada")


def test_published_override_replaces_the_packaged_default(definition: EmailTemplateDefinition, root: Path) -> None:
    """A valid file under the module's namespace wins over the shipped wording."""
    _write(root / "web" / "reset_password.toml", _OVERRIDE)

    assert _render(definition, root).startswith("Hi Ada!")


def test_override_in_another_namespace_is_ignored(definition: EmailTemplateDefinition, root: Path) -> None:
    """Web must not read Arena's namespace, and the reverse."""
    _write(root / "arena" / "reset_password.toml", _OVERRIDE)

    assert _render(definition, root, namespace="web").startswith("Hello Ada")


def test_deleted_override_falls_back_to_the_packaged_default(definition: EmailTemplateDefinition, root: Path) -> None:
    """Removing the file is how an operator reverts to shipped wording."""
    path = root / "web" / "reset_password.toml"
    _write(path, _OVERRIDE)
    registry = build_module_registry((definition,), namespace="web", override_root=root)
    context = definition.sample_values

    assert registry.render("reset_password", brand_name="NOCA", context=context).body.startswith("Hi Ada!")
    path.unlink()

    assert registry.render("reset_password", brand_name="NOCA", context=context).body.startswith("Hello Ada")


def test_a_changed_file_is_reread_on_the_next_render(definition: EmailTemplateDefinition, root: Path) -> None:
    """The stat signature must notice a rename over an existing file."""
    path = root / "web" / "reset_password.toml"
    _write(path, _OVERRIDE)
    registry = build_module_registry((definition,), namespace="web", override_root=root)
    context = definition.sample_values
    registry.render("reset_password", brand_name="NOCA", context=context)

    _write(path, 'subject = "Reset"\nbody = "Second version for {name}: {url}"\n')

    body = registry.render("reset_password", brand_name="NOCA", context=context).body
    assert body.startswith("Second version")


def test_unchanged_file_is_not_reparsed(
    definition: EmailTemplateDefinition, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A send stats the file; it parses it only when the signature changed.

    This is what keeps the source cheap enough to consult on every render, so it
    is asserted rather than left to inspection.
    """
    _write(root / "web" / "reset_password.toml", _OVERRIDE)
    loads: list[Path] = []
    original = overrides.load_email_template

    def counting_load(path: Path) -> EmailTemplate:
        loads.append(path)
        return original(path)

    monkeypatch.setattr(overrides, "load_email_template", counting_load)
    source = FilesystemOverrideSource(root, "web", (definition,))

    for _ in range(3):
        assert source.get(definition) is not None

    assert len(loads) == 1


def test_invalid_runtime_update_retains_the_last_valid_override(
    definition: EmailTemplateDefinition, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A typo published over a working override must not change what is sent."""
    path = root / "web" / "reset_password.toml"
    _write(path, _OVERRIDE)
    registry = build_module_registry((definition,), namespace="web", override_root=root)
    context = definition.sample_values
    registry.render("reset_password", brand_name="NOCA", context=context)

    with caplog.at_level(logging.ERROR):
        _write(path, 'subject = "Broken"\nbody = "Missing the required url and a stray {"\n')
        body = registry.render("reset_password", brand_name="NOCA", context=context).body

    assert body.startswith("Hi Ada!")
    assert "email template override rejected" in caplog.text
    assert "retaining last valid override" in caplog.text


def test_first_override_that_is_invalid_keeps_the_packaged_default(
    definition: EmailTemplateDefinition, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """With no previous valid version, the shipped wording must still send."""
    registry = build_module_registry((definition,), namespace="web", override_root=root)

    with caplog.at_level(logging.ERROR):
        _write(root / "web" / "reset_password.toml", 'subject = "Broken"\nbody = "No url here, {name}"\n')
        body = registry.render("reset_password", brand_name="NOCA", context=definition.sample_values).body

    assert body.startswith("Hello Ada")
    assert "using packaged default" in caplog.text


def test_startup_validation_reports_every_invalid_file(definition: EmailTemplateDefinition, root: Path) -> None:
    """Startup names each filename and reason rather than the first failure."""
    _write(root / "web" / "reset_password.toml", 'subject = "Broken"\nbody = "No url, {name}"\n')
    _write(root / "web" / "not_a_key.toml", _DEFAULT)

    with pytest.raises(EmailTemplateError) as raised:
        ensure_valid_override_tree((definition,), namespace="web", override_root=root)

    message = str(raised.value)
    assert "not_a_key.toml: unknown template key 'not_a_key'" in message
    assert "reset_password.toml" in message
    assert "missing required placeholders: url" in message


def test_startup_validation_passes_on_a_valid_tree(definition: EmailTemplateDefinition, root: Path) -> None:
    """A published override that honours its contract must not block a start."""
    _write(root / "web" / "reset_password.toml", _OVERRIDE)

    ensure_valid_override_tree((definition,), namespace="web", override_root=root)


def test_startup_validation_ignores_an_absent_directory(definition: EmailTemplateDefinition, tmp_path: Path) -> None:
    """A configured root without this module's namespace is not an error."""
    ensure_valid_override_tree((definition,), namespace="web", override_root=tmp_path / "empty")


def test_non_toml_files_are_not_treated_as_templates(definition: EmailTemplateDefinition, root: Path) -> None:
    """An editor backup or a README beside the templates must not fail a start."""
    (root / "web" / "README.md").write_text("Managed in Git.\n", encoding="utf-8")
    (root / "web" / "reset_password.toml.new").write_text("half-written", encoding="utf-8")

    assert validate_override_tree(root, "web", (definition,)) == ()


def test_based_on_records_the_default_it_was_written_from(definition: EmailTemplateDefinition, root: Path) -> None:
    """A matching digest is not drift, and does not warn."""
    digest = default_digest(definition)
    _write(root / "web" / "reset_password.toml", f'{_OVERRIDE}based_on = "{digest}"\n')
    source = FilesystemOverrideSource(root, "web", (definition,))
    source.get(definition)

    state = {item.key: item for item in source.describe()}["reset_password"]
    assert state.active is True
    assert state.stale is False
    assert state.error is None
    assert state.based_on == digest


def test_stale_based_on_is_reported_without_refusing_the_override(
    definition: EmailTemplateDefinition, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Drift is advisory: the override still sends, and the operator is warned."""
    _write(root / "web" / "reset_password.toml", f'{_OVERRIDE}based_on = "0123456789abcdef"\n')
    source = FilesystemOverrideSource(root, "web", (definition,))

    with caplog.at_level(logging.WARNING):
        template = source.get(definition)

    assert template is not None
    assert template.subject == "Password reset"
    assert "email template override is stale" in caplog.text
    state = {item.key: item for item in source.describe()}["reset_password"]
    assert state.stale is True


def test_describe_reports_the_retained_error(definition: EmailTemplateDefinition, root: Path) -> None:
    """Operator visibility must survive the render that rejected the file."""
    _write(root / "web" / "reset_password.toml", 'subject = "Broken"\nbody = "No url, {name}"\n')
    source = FilesystemOverrideSource(root, "web", (definition,))
    source.get(definition)

    state = {item.key: item for item in source.describe()}["reset_password"]
    assert state.active is False
    assert state.error is not None
    assert "missing required placeholders: url" in state.error


def test_visibility_reports_the_packaged_default_without_an_override(
    definition: EmailTemplateDefinition,
) -> None:
    """A default-only registry presents a safe sample without override metadata."""
    registry = build_module_registry((definition,), namespace="web", override_root=None)

    [template] = inspect_email_templates(registry, brand_name="NOCA")

    assert template.source == "default"
    assert template.baseline_status == "not_applicable"
    assert template.error is None
    assert template.body.startswith("Hello Ada")


def test_visibility_distinguishes_baseline_match_stale_and_absent_metadata(
    definition: EmailTemplateDefinition,
    root: Path,
) -> None:
    """An active override's baseline status must not collapse to one boolean."""
    path = root / "web" / "reset_password.toml"
    registry = build_module_registry((definition,), namespace="web", override_root=root)

    _write(path, _OVERRIDE)
    assert inspect_email_templates(registry, brand_name="NOCA")[0].baseline_status == "not_recorded"

    _write(path, f'{_OVERRIDE}based_on = "{default_digest(definition)}"\n')
    assert inspect_email_templates(registry, brand_name="NOCA")[0].baseline_status == "matches"

    _write(path, f'{_OVERRIDE}based_on = "0123456789abcdef"\n')
    assert inspect_email_templates(registry, brand_name="NOCA")[0].baseline_status == "stale"


def test_visibility_reports_a_rejected_update_and_retained_source(
    definition: EmailTemplateDefinition,
    root: Path,
) -> None:
    """A rejected update exposes the process-local fallback that still sends."""
    path = root / "web" / "reset_password.toml"
    registry = build_module_registry((definition,), namespace="web", override_root=root)
    _write(path, _OVERRIDE)
    inspect_email_templates(registry, brand_name="NOCA")

    _write(path, 'subject = "Broken"\nbody = "No url here, {name}"\n')
    [template] = inspect_email_templates(registry, brand_name="NOCA")

    assert template.source == "override"
    assert template.error is not None
    assert template.body.startswith("Hi Ada!")


def test_visibility_reports_a_rejected_first_override_using_the_default(
    definition: EmailTemplateDefinition,
    root: Path,
) -> None:
    """A rejected first override has no retained file, so the default remains effective."""
    registry = build_module_registry((definition,), namespace="web", override_root=root)
    _write(root / "web" / "reset_password.toml", 'subject = "Broken"\nbody = "No url here, {name}"\n')

    [template] = inspect_email_templates(registry, brand_name="NOCA")

    assert template.source == "default"
    assert template.error is not None
    assert template.body.startswith("Hello Ada")


def _fail_on(monkeypatch: pytest.MonkeyPatch, target: Path, method: str, error: OSError) -> None:
    """Make one `Path` operation fail for one path, leaving every other alone.

    The failure is injected rather than produced with `chmod`, because CI runs as
    root and root walks straight through directory permissions: a
    permission-based test would skip in exactly the environment that has to prove
    these branches work.
    """
    original = getattr(Path, method)

    def patched(self: Path, *args: object, **kwargs: object) -> object:
        if self == target:
            raise error
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, patched)


def test_unreadable_namespace_refuses_the_start(
    definition: EmailTemplateDefinition, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A namespace that cannot be listed is not an empty one.

    Reading it as empty would start the process on packaged defaults and give an
    operator no way to tell that their published wording never loaded.
    """
    namespace = root / "web"
    _write(namespace / "reset_password.toml", _OVERRIDE)
    _fail_on(monkeypatch, namespace, "iterdir", PermissionError(13, "Permission denied"))

    with pytest.raises(EmailTemplateError, match="cannot read override directory"):
        ensure_valid_override_tree((definition,), namespace="web", override_root=root)


def test_unreadable_file_retains_the_last_valid_override(
    definition: EmailTemplateDefinition, root: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permission failure is not an instruction to revert to the default."""
    path = root / "web" / "reset_password.toml"
    _write(path, _OVERRIDE)
    registry = build_module_registry((definition,), namespace="web", override_root=root)
    context = definition.sample_values
    assert registry.render("reset_password", brand_name="NOCA", context=context).body.startswith("Hi Ada!")

    _fail_on(monkeypatch, path, "stat", PermissionError(13, "Permission denied"))
    with caplog.at_level(logging.ERROR):
        body = registry.render("reset_password", brand_name="NOCA", context=context).body

    assert body.startswith("Hi Ada!")
    assert "email template override unreadable" in caplog.text
    assert "retaining last valid override" in caplog.text


def test_the_file_is_reread_once_it_is_readable_again(
    definition: EmailTemplateDefinition, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dropped signature must not pin the retained version forever."""
    path = root / "web" / "reset_password.toml"
    _write(path, _OVERRIDE)
    registry = build_module_registry((definition,), namespace="web", override_root=root)
    context = definition.sample_values
    registry.render("reset_password", brand_name="NOCA", context=context)

    _fail_on(monkeypatch, path, "stat", PermissionError(13, "Permission denied"))
    registry.render("reset_password", brand_name="NOCA", context=context)
    monkeypatch.undo()
    _write(path, 'subject = "Reset"\nbody = "Third version for {name}: {url}"\n')

    body = registry.render("reset_password", brand_name="NOCA", context=context).body
    assert body.startswith("Third version")


def test_visibility_keeps_an_unrenderable_template_to_its_own_row(
    definition: EmailTemplateDefinition, root: Path
) -> None:
    """One override that cannot render must not take the whole page down.

    Size limits are checked after substitution, so an override can pass startup
    validation and still fail to render -- and that is exactly the template an
    administrator opens this page to understand.
    """
    # The subject must be allowed to carry `{url}`: this is about a template that
    # passes validation and still overflows once the value is substituted.
    overflowing = EmailTemplateDefinition(
        key="reset_password",
        default_path=definition.default_path,
        subject_placeholders=frozenset({"url"}),
        body_placeholders=frozenset({"name", "url"}),
        required_placeholders=frozenset({"url"}),
        sample_values=definition.sample_values,
    )
    other = EmailTemplateDefinition(
        key="account_activated",
        default_path=definition.default_path,
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"name", "url"}),
        required_placeholders=frozenset({"url"}),
        sample_values=definition.sample_values,
    )
    registry = build_module_registry((overflowing, other), namespace="web", override_root=root)
    _write(
        root / "web" / "reset_password.toml",
        f'subject = "{"A" * 190} {{url}}"\nbody = "Hi {{name}}, {{url}}"\n',
    )

    views = {view.key: view for view in inspect_email_templates(registry, brand_name="NOCA")}

    assert views["reset_password"].preview_error is not None
    assert "subject exceeds" in views["reset_password"].preview_error
    assert views["reset_password"].subject == ""
    assert views["account_activated"].preview_error is None
    assert views["account_activated"].body.startswith("Hello Ada")


def test_visibility_reports_an_undescribable_backend_as_unknown(
    definition: EmailTemplateDefinition,
) -> None:
    """A backend that cannot say what it serves must not be read as the default.

    Claiming "shipped default" on the page built to be believed would state the
    one thing this code cannot know.
    """

    class OpaqueLookup:
        """A lookup implementing only the rendering contract."""

        def get(self, definition: EmailTemplateDefinition) -> EmailTemplate | None:
            """Serve the packaged default without describing anything."""
            return None

    registry = EmailTemplateRegistry((definition,), override_lookup=OpaqueLookup())

    [view] = inspect_email_templates(registry, brand_name="NOCA")

    assert view.source == "unknown"
    assert view.baseline_status == "not_applicable"


def test_retained_errors_name_the_file_not_its_place_on_the_host(
    definition: EmailTemplateDefinition, root: Path
) -> None:
    """Administrators see the filename; the host path stays in the log."""
    path = root / "web" / "reset_password.toml"
    _write(path, 'subject = \'unterminated\nbody = "x"\n')
    source = FilesystemOverrideSource(root, "web", (definition,))
    source.get(definition)

    state = {item.key: item for item in source.describe()}["reset_password"]
    assert state.error is not None
    assert "reset_password.toml" in state.error
    assert str(root) not in state.error
