from __future__ import annotations

from scripts.fetch_assets import highlight_asset_languages
from shared.language_registry import (
    default_language_registry,
    default_language_seed_rows,
    highlightjs_language_for_language_id,
    highlightjs_languages_for_registry,
)
from web.routes.contest_submissions import submission_highlight_assets


def test_default_language_registry_includes_javascript() -> None:
    registry = default_language_registry()

    cpp = registry["gcc-cpp23"]
    assert cpp.source_filename == "source.cpp"
    assert cpp.compile_image == "noca/judge-gcc-cpp23:compile"
    assert cpp.run_image == "noca/judge-gcc-cpp23:run"
    assert cpp.compile_cmd == ["g++", "-std=c++23", "-O2", "-o", "/sandbox/solution", "/sandbox/source.cpp"]
    assert cpp.run_cmd == ["/sandbox/solution"]
    assert cpp.artifact_path == "/sandbox/solution"
    assert cpp.artifact_is_source is False

    javascript = registry["javascript"]
    assert javascript.source_filename == "source.js"
    assert javascript.compile_image == "noca/judge-javascript:compile"
    assert javascript.run_image == "noca/judge-javascript:run"
    assert javascript.compile_cmd == ["node", "--check", "/sandbox/source.js"]
    assert javascript.run_cmd == ["/usr/local/bin/node", "/sandbox/source.js"]
    assert javascript.artifact_path == "/sandbox/source.js"
    assert javascript.artifact_is_source is True
    assert javascript.stdout_flush_hint is not None
    assert "process.stdout.write" in javascript.stdout_flush_hint

    go = registry["go"]
    assert go.source_filename == "source.go"
    assert go.compile_image == "noca/judge-go:compile"
    assert go.run_image == "noca/judge-go:run"
    assert go.compile_cmd == [
        "go",
        "build",
        "-trimpath",
        "-ldflags=-s -w",
        "-o",
        "/sandbox/solution",
        "/sandbox/source.go",
    ]
    assert go.run_cmd == ["/sandbox/solution"]
    assert go.artifact_path == "/sandbox/solution"
    assert go.artifact_is_source is False

    rust = registry["rust"]
    assert rust.source_filename == "source.rs"
    assert rust.compile_image == "noca/judge-rust:compile"
    assert rust.run_image == "noca/judge-rust:run"
    assert rust.compile_cmd == [
        "rustc",
        "--edition=2024",
        "-O",
        "-C",
        "debuginfo=0",
        "-C",
        "panic=abort",
        "-o",
        "/sandbox/solution",
        "/sandbox/source.rs",
    ]
    assert rust.run_cmd == ["/sandbox/solution"]
    assert rust.artifact_path == "/sandbox/solution"
    assert rust.artifact_is_source is False


def test_seed_rows_and_highlight_languages_include_javascript() -> None:
    seed_rows = default_language_seed_rows()
    seed_ids = {str(row["id"]) for row in seed_rows}
    seed_flush_hints = {str(row["id"]): row["stdout_flush_hint"] for row in seed_rows}

    assert "gcc-cpp23" in seed_ids
    assert "javascript" in seed_ids
    assert "go" in seed_ids
    assert "rust" in seed_ids
    assert "haskell" in seed_ids
    assert "lua" in seed_ids
    assert "prolog" in seed_ids
    assert "fortran" in seed_ids
    assert all(seed_flush_hints.values())

    assert highlightjs_language_for_language_id("gcc-cpp23") == "cpp"
    assert highlightjs_language_for_language_id("javascript") == "javascript"
    assert highlightjs_language_for_language_id("go") == "go"
    assert highlightjs_language_for_language_id("rust") == "rust"
    assert highlightjs_language_for_language_id("haskell") == "haskell"
    assert highlightjs_language_for_language_id("lua") == "lua"
    assert highlightjs_language_for_language_id("prolog") == "prolog"
    assert highlightjs_language_for_language_id("fortran") == "fortran"

    highlight_languages = highlightjs_languages_for_registry()
    assert "cpp" in highlight_languages
    assert "javascript" in highlight_languages
    assert "go" in highlight_languages
    assert "rust" in highlight_languages
    assert "haskell" in highlight_languages
    assert "lua" in highlight_languages
    assert "prolog" in highlight_languages
    assert "fortran" in highlight_languages

    asset_languages = highlight_asset_languages()
    assert "cpp" in asset_languages
    assert "javascript" in asset_languages
    assert "go" in asset_languages
    assert "rust" in asset_languages
    assert "haskell" in asset_languages
    assert "lua" in asset_languages
    assert "prolog" in asset_languages
    assert "fortran" in asset_languages


def test_new_language_configs_fortran_and_lua() -> None:
    registry = default_language_registry()

    fortran = registry["fortran"]
    assert fortran.source_filename == "source.f90"
    assert fortran.compile_image == "noca/judge-fortran:compile"
    assert fortran.run_image == "noca/judge-fortran:run"
    assert fortran.compile_cmd == ["gfortran", "-O2", "-std=f2018", "-o", "/sandbox/solution", "/sandbox/source.f90"]
    assert fortran.run_cmd == ["/sandbox/solution"]
    assert fortran.artifact_path == "/sandbox/solution"
    assert fortran.artifact_is_source is False

    lua = registry["lua"]
    assert lua.source_filename == "source.lua"
    assert lua.compile_image == "noca/judge-lua:compile"
    assert lua.run_image == "noca/judge-lua:run"
    assert lua.compile_cmd == ["/usr/local/bin/luac", "-p", "/sandbox/source.lua"]
    assert lua.run_cmd == ["/usr/local/bin/lua", "/sandbox/source.lua"]
    assert lua.artifact_path == "/sandbox/source.lua"
    assert lua.artifact_is_source is True


def test_new_language_config_perl() -> None:
    registry = default_language_registry()

    perl = registry["perl"]
    assert perl.source_filename == "source.pl"
    assert perl.compile_image == "noca/judge-perl:compile"
    assert perl.run_image == "noca/judge-perl:run"
    assert perl.compile_cmd == ["/usr/local/bin/perl", "-c", "/sandbox/source.pl"]
    assert perl.run_cmd == ["/usr/local/bin/perl", "/sandbox/source.pl"]
    assert perl.artifact_path == "/sandbox/source.pl"
    assert perl.artifact_is_source is True

    seed_ids = {str(row["id"]) for row in default_language_seed_rows()}
    assert "perl" in seed_ids

    assert highlightjs_language_for_language_id("perl") == "perl"
    assert "perl" in highlightjs_languages_for_registry()
    assert "perl" in highlight_asset_languages()


def test_new_language_config_scala() -> None:
    registry = default_language_registry()

    scala = registry["scala"]
    assert scala.source_filename == "Main.scala"
    assert scala.default_extension == ".scala"
    assert scala.compile_image == "noca/judge-scala:compile"
    assert scala.run_image == "noca/judge-scala:run"
    # Compiled to a self-contained fat jar, so the run command is a plain JRE
    # invocation identical to Kotlin's and the artifact is the jar, not the source.
    assert scala.run_cmd == ["/opt/java/openjdk/bin/java", "-Xss64m", "-Xmx256m", "-jar", "/sandbox/solution.jar"]
    assert scala.artifact_path == "/sandbox/solution.jar"
    assert scala.artifact_is_source is False

    assert scala.compile_cmd is not None
    assert scala.compile_cmd[:2] == ["sh", "-c"]
    compile_script = scala.compile_cmd[2]
    assert "scalac -d /sandbox/classes /sandbox/Main.scala" in compile_script
    # The Scala runtime jars must be folded in, or the artifact will not run on a plain JRE.
    assert "scala3-library_3-*.jar" in compile_script
    assert "jar cfe /sandbox/solution.jar Main" in compile_script

    seed_ids = {str(row["id"]) for row in default_language_seed_rows()}
    assert "scala" in seed_ids

    assert highlightjs_language_for_language_id("scala") == "scala"
    assert "scala" in highlightjs_languages_for_registry()
    assert "scala" in highlight_asset_languages()


def test_new_language_config_ocaml() -> None:
    registry = default_language_registry()

    ocaml = registry["ocaml"]
    assert ocaml.source_filename == "source.ml"
    assert ocaml.default_extension == ".ml"
    assert ocaml.compile_image == "noca/judge-ocaml:compile"
    assert ocaml.run_image == "noca/judge-ocaml:run"
    # ocamlopt (native), not ocamlc (bytecode): the artifact is a standalone binary.
    # Debian's ocaml-nox installs under /usr, unlike the former source build's /usr/local.
    assert ocaml.compile_cmd == ["/usr/bin/ocamlopt", "-o", "/sandbox/solution", "/sandbox/source.ml"]
    assert ocaml.run_cmd == ["/sandbox/solution"]
    assert ocaml.artifact_path == "/sandbox/solution"
    assert ocaml.artifact_is_source is False
    # "-O2" is flambda-only and Debian ships no flambda build; it must not creep back in.
    assert "-O2" not in ocaml.compile_cmd

    seed_ids = {str(row["id"]) for row in default_language_seed_rows()}
    assert "ocaml" in seed_ids

    assert highlightjs_language_for_language_id("ocaml") == "ocaml"
    assert "ocaml" in highlightjs_languages_for_registry()
    assert "ocaml" in highlight_asset_languages()


def test_new_language_config_php() -> None:
    registry = default_language_registry()

    php = registry["php"]
    assert php.source_filename == "source.php"
    assert php.default_extension == ".php"
    assert php.compile_image == "noca/judge-php:compile"
    assert php.run_image == "noca/judge-php:run"
    assert php.compile_cmd == ["/usr/local/bin/php", "-l", "/sandbox/source.php"]
    assert php.run_cmd == ["/usr/local/bin/php", "/sandbox/source.php"]
    assert php.artifact_path == "/sandbox/source.php"
    assert php.artifact_is_source is True

    seed_ids = {str(row["id"]) for row in default_language_seed_rows()}
    assert "php" in seed_ids

    assert highlightjs_language_for_language_id("php") == "php"
    assert "php" in highlightjs_languages_for_registry()
    assert "php" in highlight_asset_languages()


def test_stdout_flush_hints_have_balanced_code_spans() -> None:
    """Arena splits the hint on backticks to alternate code/prose spans.

    An odd count silently inverts which half renders as code, so every hint must
    carry an even number of backticks.
    """
    for language in default_language_registry().values():
        hint = language.stdout_flush_hint
        assert hint, f"{language.id} has no stdout flush hint"
        assert hint.count("`") % 2 == 0, f"{language.id} hint has unbalanced backticks: {hint!r}"


def test_submission_highlight_assets_support_javascript() -> None:
    assert submission_highlight_assets("javascript") == {
        "highlight_language_path": "highlight/languages/javascript.min.js",
        "highlight_language_class": "language-javascript",
    }


def test_submission_highlight_assets_support_go_and_rust() -> None:
    assert submission_highlight_assets("go") == {
        "highlight_language_path": "highlight/languages/go.min.js",
        "highlight_language_class": "language-go",
    }
    assert submission_highlight_assets("rust") == {
        "highlight_language_path": "highlight/languages/rust.min.js",
        "highlight_language_class": "language-rust",
    }
