#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Language runtime configuration: LanguageConfig DTO, path constants, and seed data.

This module is imported by language_registry (which re-exports everything) and by
language_configs.py itself. Do not import language_registry from here — that would
create a circular dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

SANDBOX_DIR = "/sandbox"
BINARY_PATH = f"{SANDBOX_DIR}/solution"
JAR_PATH = f"{SANDBOX_DIR}/solution.jar"
JS_PATH = f"{SANDBOX_DIR}/solution.js"
INPUT_PATH = f"{SANDBOX_DIR}/input"
STDOUT_PATH = f"{SANDBOX_DIR}/stdout"
STDERR_PATH = f"{SANDBOX_DIR}/stderr"
PYTHON3_PATH = "/usr/local/bin/python3"
JAVA_PATH = "/opt/java/openjdk/bin/java"
NODE_PATH = "/usr/local/bin/node"
DOTNET_PATH = "/usr/bin/dotnet"
LUA_PATH = "/usr/local/bin/lua"
LUAC_PATH = "/usr/local/bin/luac"
SWIPL_PATH = "/usr/bin/swipl"
RUBY_PATH = "/usr/local/bin/ruby"
PERL_PATH = "/usr/local/bin/perl"
BASH_PATH = "/bin/bash"


@dataclass(frozen=True)
class LanguageConfig:
    id: str
    name: str
    icon: str
    highlightjs_language: str
    ace_mode: str
    compile_image: str
    run_image: str
    compile_cmd: list[str] | None
    run_cmd: list[str]
    source_filename: str
    default_extension: str
    artifact_path: str
    artifact_is_source: bool = False
    compile_timeout_s: float = 60.0
    profiling_repetitions_default: int = 3
    profiled_pids_floor: int = 32
    active: bool = True
    version: str | None = None


def default_language_configs() -> list[LanguageConfig]:
    return [
        LanguageConfig(
            id="gcc-c17",
            name="C (GCC, C17)",
            icon="devicon-c-original",
            highlightjs_language="c",
            ace_mode="c_cpp",
            compile_image="noca/judge-gcc-c17:compile",
            run_image="noca/judge-gcc-c17:run",
            compile_cmd=[
                "gcc",
                "-std=c17",
                "-O2",
                "-D_GNU_SOURCE",
                "-o",
                BINARY_PATH,
                f"{SANDBOX_DIR}/source.c",
                "-lm",
            ],
            run_cmd=[BINARY_PATH],
            source_filename="source.c",
            default_extension=".c",
            artifact_path=BINARY_PATH,
            compile_timeout_s=30.0,
            profiling_repetitions_default=10,
            profiled_pids_floor=32,
            version="gcc version 12.2.0 (Debian 12.2.0-14+deb12u1)",
        ),
        LanguageConfig(
            id="gcc-cpp23",
            name="C++ (G++, C++23)",
            icon="devicon-cplusplus-plain",
            highlightjs_language="cpp",
            ace_mode="c_cpp",
            compile_image="noca/judge-gcc-cpp23:compile",
            run_image="noca/judge-gcc-cpp23:run",
            compile_cmd=[
                "g++",
                "-std=c++23",
                "-O2",
                "-o",
                BINARY_PATH,
                f"{SANDBOX_DIR}/source.cpp",
            ],
            run_cmd=[BINARY_PATH],
            source_filename="source.cpp",
            default_extension=".cpp",
            artifact_path=BINARY_PATH,
            compile_timeout_s=30.0,
            profiling_repetitions_default=10,
            profiled_pids_floor=32,
            version="gcc version 12.2.0 (Debian 12.2.0-14+deb12u1)",
        ),
        LanguageConfig(
            id="python3",
            name="Python 3.14",
            icon="devicon-python-plain-wordmark",
            highlightjs_language="python",
            ace_mode="python",
            compile_image="noca/judge-python3:compile",
            run_image="noca/judge-python3:run",
            compile_cmd=[
                PYTHON3_PATH,
                "-m",
                "py_compile",
                f"{SANDBOX_DIR}/source.py",
            ],
            run_cmd=[
                PYTHON3_PATH,
                "-u",
                f"{SANDBOX_DIR}/source.py",
            ],
            source_filename="source.py",
            default_extension=".py",
            artifact_path=f"{SANDBOX_DIR}/source.py",
            artifact_is_source=True,
            compile_timeout_s=10.0,
            profiling_repetitions_default=3,
            profiled_pids_floor=32,
            version="Python 3.14.4",
        ),
        LanguageConfig(
            id="java",
            name="Java",
            icon="devicon-java-plain-wordmark",
            highlightjs_language="java",
            ace_mode="java",
            compile_image="noca/judge-java:compile",
            run_image="noca/judge-java:run",
            compile_cmd=[
                "sh",
                "-c",
                (
                    "javac -encoding UTF-8 -d /sandbox /sandbox/Main.java && "
                    "jar cfe /sandbox/solution.jar Main -C /sandbox ."
                ),
            ],
            run_cmd=[
                JAVA_PATH,
                "-Xss64m",
                "-Xmx256m",
                # Determinism flags (found in 2017): remove major sources of non-deterministic
                # JVM scheduling so that identical submissions produce consistent timings.
                "-Xbatch",
                "-XX:+UseSerialGC",
                "-XX:-TieredCompilation",
                "-XX:CICompilerCount=1",
                "-jar",
                JAR_PATH,
            ],
            source_filename="Main.java",
            default_extension=".java",
            artifact_path=JAR_PATH,
            version="OpenJDK 25.0.3+9 (Eclipse Temurin)",
        ),
        LanguageConfig(
            id="javascript",
            name="JavaScript (Node.js)",
            icon="devicon-javascript-plain",
            highlightjs_language="javascript",
            ace_mode="javascript",
            compile_image="noca/judge-javascript:compile",
            run_image="noca/judge-javascript:run",
            compile_cmd=[
                "node",
                "--check",
                f"{SANDBOX_DIR}/source.js",
            ],
            run_cmd=[
                NODE_PATH,
                f"{SANDBOX_DIR}/source.js",
            ],
            source_filename="source.js",
            default_extension=".js",
            artifact_path=f"{SANDBOX_DIR}/source.js",
            artifact_is_source=True,
            compile_timeout_s=10.0,
            profiling_repetitions_default=3,
            profiled_pids_floor=32,
            version="node v24.18.0",
        ),
        LanguageConfig(
            id="kotlin",
            name="Kotlin",
            icon="devicon-kotlin-plain",
            highlightjs_language="kotlin",
            ace_mode="kotlin",
            compile_image="noca/judge-kotlin:compile",
            run_image="noca/judge-kotlin:run",
            compile_cmd=[
                "kotlinc",
                f"{SANDBOX_DIR}/Main.kt",
                "-include-runtime",
                "-d",
                JAR_PATH,
            ],
            run_cmd=[
                JAVA_PATH,
                "-Xss64m",
                "-Xmx256m",
                "-jar",
                JAR_PATH,
            ],
            source_filename="Main.kt",
            default_extension=".kt",
            artifact_path=JAR_PATH,
            compile_timeout_s=120.0,
            profiling_repetitions_default=10,
            profiled_pids_floor=32,
            version="kotlinc-jvm 2.3.0 (JRE 25.0.3+9-LTS)",
        ),
        LanguageConfig(
            id="fpc-pascal",
            name="Pascal (FPC)",
            icon="devicon-delphi-plain",
            highlightjs_language="delphi",
            ace_mode="pascal",
            compile_image="noca/judge-fpc-pascal:compile",
            run_image="noca/judge-fpc-pascal:run",
            compile_cmd=[
                "fpc",
                "-O2",
                f"-o{BINARY_PATH}",
                f"{SANDBOX_DIR}/source.pas",
            ],
            run_cmd=[BINARY_PATH],
            source_filename="source.pas",
            default_extension=".pas",
            artifact_path=BINARY_PATH,
            version="Free Pascal Compiler 3.2.2",
        ),
        LanguageConfig(
            id="go",
            name="Go",
            icon="devicon-go-original-wordmark",
            highlightjs_language="go",
            ace_mode="golang",
            compile_image="noca/judge-go:compile",
            run_image="noca/judge-go:run",
            compile_cmd=[
                "go",
                "build",
                "-trimpath",
                "-ldflags=-s -w",
                "-o",
                BINARY_PATH,
                f"{SANDBOX_DIR}/source.go",
            ],
            run_cmd=[BINARY_PATH],
            source_filename="source.go",
            default_extension=".go",
            artifact_path=BINARY_PATH,
            compile_timeout_s=30.0,
            profiling_repetitions_default=10,
            profiled_pids_floor=32,
            version="go version go1.26.2 linux/amd64",
        ),
        LanguageConfig(
            id="rust",
            name="Rust",
            icon="devicon-rust-original",
            highlightjs_language="rust",
            ace_mode="rust",
            compile_image="noca/judge-rust:compile",
            run_image="noca/judge-rust:run",
            compile_cmd=[
                "rustc",
                "--edition=2024",
                "-O",
                "-C",
                "debuginfo=0",
                "-C",
                "panic=abort",
                "-o",
                BINARY_PATH,
                f"{SANDBOX_DIR}/source.rs",
            ],
            run_cmd=[BINARY_PATH],
            source_filename="source.rs",
            default_extension=".rs",
            artifact_path=BINARY_PATH,
            compile_timeout_s=60.0,
            profiling_repetitions_default=10,
            profiled_pids_floor=32,
            version="rustc 1.96.1",
        ),
        LanguageConfig(
            id="c-sharp",
            name="C# (.NET)",
            icon="devicon-csharp-plain-wordmark",
            highlightjs_language="csharp",
            ace_mode="csharp",
            compile_image="noca/judge-c-sharp:compile",
            run_image="noca/judge-c-sharp:run",
            compile_cmd=[
                "sh",
                "-c",
                (
                    # File-based apps (.NET 10) let a single .cs file carry #:package,
                    # #:sdk, #:project, and #:property directives that would let a
                    # submission pull NuGet packages, switch SDKs (e.g. ASP.NET Core),
                    # or reference paths outside the sandbox. None of that is part of
                    # the single-file, no-dependencies contract, so any such directive
                    # is rejected up front.
                    "if grep -Eq '^[[:space:]]*#:' /sandbox/source.cs; then "
                    "echo 'File-based app directives are not allowed.' >&2; "
                    "exit 1; "
                    "fi && "
                    "printf '%s\\n' "
                    '\'<?xml version="1.0" encoding="utf-8"?>\' '
                    "'<configuration>' "
                    "'  <packageSources><clear /></packageSources>' "
                    "'</configuration>' "
                    "> /sandbox/NuGet.Config && "
                    "dotnet publish /sandbox/source.cs "
                    "-c Release "
                    "--self-contained false "
                    "-p:PublishAot=false "
                    "-p:UseAppHost=false "
                    "-p:AssemblyName=solution "
                    "-p:DebugType=None "
                    "-p:DebugSymbols=false "
                    "--configfile /sandbox/NuGet.Config "
                    "--ignore-failed-sources "
                    "--nologo "
                    "-o /sandbox/out && "
                    "tar -cf /sandbox/solution.tar -C /sandbox/out ."
                ),
            ],
            run_cmd=[
                "/bin/sh",
                "-c",
                (
                    "rm -rf /sandbox/csharp-run && "
                    "mkdir -p /sandbox/csharp-run && "
                    "tar -xf /sandbox/solution.tar -C /sandbox/csharp-run && "
                    "export DOTNET_EnableWriteXorExecute=0 && "
                    f"exec {DOTNET_PATH} /sandbox/csharp-run/solution.dll"
                ),
            ],
            source_filename="source.cs",
            default_extension=".cs",
            artifact_path=f"{SANDBOX_DIR}/solution.tar",
            compile_timeout_s=120.0,
            profiling_repetitions_default=10,
            profiled_pids_floor=32,
            version="dotnet 10.0.301",
        ),
        LanguageConfig(
            id="haskell",
            name="Haskell (GHC)",
            icon="devicon-haskell-plain",
            highlightjs_language="haskell",
            ace_mode="haskell",
            compile_image="noca/judge-haskell:compile",
            run_image="noca/judge-haskell:run",
            compile_cmd=[
                "ghc",
                "-O2",
                "-o",
                BINARY_PATH,
                f"{SANDBOX_DIR}/source.hs",
            ],
            run_cmd=[BINARY_PATH],
            source_filename="source.hs",
            default_extension=".hs",
            artifact_path=BINARY_PATH,
            compile_timeout_s=60.0,
            profiling_repetitions_default=10,
            profiled_pids_floor=32,
            version="The Glorious Glasgow Haskell Compilation System, version 9.0.2",
        ),
        LanguageConfig(
            id="lua",
            name="Lua",
            icon="devicon-lua-plain-wordmark",
            highlightjs_language="lua",
            ace_mode="lua",
            compile_image="noca/judge-lua:compile",
            run_image="noca/judge-lua:run",
            compile_cmd=[
                LUAC_PATH,
                "-p",
                f"{SANDBOX_DIR}/source.lua",
            ],
            run_cmd=[
                LUA_PATH,
                f"{SANDBOX_DIR}/source.lua",
            ],
            source_filename="source.lua",
            default_extension=".lua",
            artifact_path=f"{SANDBOX_DIR}/source.lua",
            artifact_is_source=True,
            compile_timeout_s=10.0,
            profiling_repetitions_default=3,
            version="Lua 5.5.0",
        ),
        LanguageConfig(
            id="prolog",
            name="Prolog (SWI)",
            icon="devicon-prolog-plain",
            highlightjs_language="prolog",
            ace_mode="prolog",
            compile_image="noca/judge-prolog:compile",
            run_image="noca/judge-prolog:run",
            compile_cmd=[
                SWIPL_PATH,
                "-q",
                "--on-error=status",
                "-g",
                "halt",
                "-t",
                "halt",
                f"{SANDBOX_DIR}/source.pl",
            ],
            run_cmd=[
                SWIPL_PATH,
                "-q",
                "-O",
                f"{SANDBOX_DIR}/source.pl",
            ],
            source_filename="source.pl",
            default_extension=".pl",
            artifact_path=f"{SANDBOX_DIR}/source.pl",
            artifact_is_source=True,
            compile_timeout_s=15.0,
            profiling_repetitions_default=3,
            version="SWI-Prolog version 9.0.4",
        ),
        LanguageConfig(
            id="fortran",
            name="Fortran (gfortran)",
            icon="devicon-fortran-original",
            highlightjs_language="fortran",
            ace_mode="fortran",
            compile_image="noca/judge-fortran:compile",
            run_image="noca/judge-fortran:run",
            compile_cmd=[
                "gfortran",
                "-O2",
                "-std=f2018",
                "-o",
                BINARY_PATH,
                f"{SANDBOX_DIR}/source.f90",
            ],
            run_cmd=[BINARY_PATH],
            source_filename="source.f90",
            default_extension=".f90",
            artifact_path=BINARY_PATH,
            compile_timeout_s=30.0,
            profiling_repetitions_default=10,
            profiled_pids_floor=32,
            version="GNU Fortran (Debian 12.2.0-3) 12.2.0",
        ),
        LanguageConfig(
            id="swift",
            name="Swift",
            icon="devicon-swift-plain",
            highlightjs_language="swift",
            ace_mode="swift",
            compile_image="noca/judge-swift:compile",
            run_image="noca/judge-swift:run",
            # The Static Linux SDK (musl) produces a fully self-contained binary, so the
            # run image needs no Swift runtime and the sandbox needs no directory binds.
            # `--swift-sdk` is a SwiftPM-only selector (swiftc does not accept it), so the
            # single source file is wrapped in a throwaway package and built with
            # `swift build`. The musl target triple is arch-dependent; pick it at runtime
            # via uname so the one shared command works on both amd64 and arm64.
            compile_cmd=[
                "sh",
                "-c",
                (
                    f"mkdir -p {SANDBOX_DIR}/pkg/Sources/solution && "
                    f"cp {SANDBOX_DIR}/source.swift {SANDBOX_DIR}/pkg/Sources/solution/main.swift && "
                    "printf '%s\\n' "
                    "'// swift-tools-version:6.0' "
                    "'import PackageDescription' "
                    '\'let package = Package(name: "solution", '
                    'targets: [.executableTarget(name: "solution", path: "Sources/solution")])\' '
                    f"> {SANDBOX_DIR}/pkg/Package.swift && "
                    f"cd {SANDBOX_DIR}/pkg && "
                    'swift build -c release --swift-sdk "$(uname -m)-swift-linux-musl" '
                    "--product solution && "
                    'cp "$(swift build -c release --swift-sdk "$(uname -m)-swift-linux-musl" '
                    f'--show-bin-path)/solution" {BINARY_PATH}'
                ),
            ],
            run_cmd=[BINARY_PATH],
            source_filename="source.swift",
            default_extension=".swift",
            artifact_path=BINARY_PATH,
            compile_timeout_s=120.0,
            profiling_repetitions_default=10,
            profiled_pids_floor=32,
            version="Swift version 6.3.2 (swift-6.3.2-RELEASE)",
        ),
        LanguageConfig(
            id="ruby",
            name="Ruby",
            icon="devicon-ruby-plain",
            highlightjs_language="ruby",
            ace_mode="ruby",
            compile_image="noca/judge-ruby:compile",
            run_image="noca/judge-ruby:run",
            compile_cmd=[
                RUBY_PATH,
                "-c",
                f"{SANDBOX_DIR}/source.rb",
            ],
            run_cmd=[
                RUBY_PATH,
                f"{SANDBOX_DIR}/source.rb",
            ],
            source_filename="source.rb",
            default_extension=".rb",
            artifact_path=f"{SANDBOX_DIR}/source.rb",
            artifact_is_source=True,
            compile_timeout_s=10.0,
            profiling_repetitions_default=3,
            version="ruby 4.0.5",
        ),
        LanguageConfig(
            id="bash",
            name="Bash",
            icon="devicon-bash-plain",
            highlightjs_language="bash",
            ace_mode="sh",
            compile_image="noca/judge-bash:compile",
            run_image="noca/judge-bash:run",
            compile_cmd=[
                BASH_PATH,
                "-n",
                f"{SANDBOX_DIR}/source.sh",
            ],
            run_cmd=[
                BASH_PATH,
                f"{SANDBOX_DIR}/source.sh",
            ],
            source_filename="source.sh",
            default_extension=".sh",
            artifact_path=f"{SANDBOX_DIR}/source.sh",
            artifact_is_source=True,
            compile_timeout_s=10.0,
            profiling_repetitions_default=3,
            version="GNU bash, version 5.2.15(1)-release",
        ),
        LanguageConfig(
            id="perl",
            name="Perl",
            icon="devicon-perl-plain",
            highlightjs_language="perl",
            ace_mode="perl",
            compile_image="noca/judge-perl:compile",
            run_image="noca/judge-perl:run",
            compile_cmd=[
                PERL_PATH,
                "-c",
                f"{SANDBOX_DIR}/source.pl",
            ],
            run_cmd=[
                PERL_PATH,
                f"{SANDBOX_DIR}/source.pl",
            ],
            source_filename="source.pl",
            default_extension=".pl",
            artifact_path=f"{SANDBOX_DIR}/source.pl",
            artifact_is_source=True,
            compile_timeout_s=10.0,
            profiling_repetitions_default=3,
            version="This is perl 5, version 42, subversion 2 (v5.42.2)",
        ),
    ]


def default_language_registry() -> dict[str, LanguageConfig]:
    return {language.id: language for language in default_language_configs()}


def default_language_seed_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for language in default_language_configs():
        rows.append(
            {
                "id": language.id,
                "name": language.name,
                "icon": language.icon,
                "compile_image": language.compile_image,
                "run_image": language.run_image,
                "compile_cmd": list(language.compile_cmd) if language.compile_cmd else None,
                "run_cmd": list(language.run_cmd),
                "source_filename": language.source_filename,
                "artifact_path": language.artifact_path,
                "artifact_is_source": language.artifact_is_source,
                "compile_timeout_s": language.compile_timeout_s,
                "profiling_repetitions_default": language.profiling_repetitions_default,
                "profiled_pids_floor": language.profiled_pids_floor,
                "version": language.version,
                "active": True,
            }
        )
    return rows
