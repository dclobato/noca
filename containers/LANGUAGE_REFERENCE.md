# Judge Language Reference

This document summarizes what contestant code can rely on inside the NOCA judge containers.

The source of truth is:
- the Dockerfiles under `containers/languages/`
- the compile/run commands in `shared/language_registry.py`

## General Rules

- Submissions are single-file only.
- Contestant code runs as the unprivileged `judge` user.
- There is no outbound package installation during judging.
- NOCA standardizes judge images on Debian-family or other mainstream glibc-based images.
  This replaces the earlier Alpine-based mix so runtime loader paths and `isolate`
  binds remain predictable across languages.

## Supported Languages

| Language ID | Source file | Compile image | Run image | What teams can use |
| --- | --- | --- | --- | --- |
| `gcc-c17` | `source.c` | `debian:bookworm-slim` + `gcc` + `libc6-dev` | `debian:bookworm-slim` | C17, GCC from Debian bookworm, glibc / C standard library, math library via `-lm` |
| `gcc-cpp23` | `source.cpp` | `debian:bookworm-slim` + `g++` + `libc6-dev` | `debian:bookworm-slim` + `libstdc++6` | C++23 with GCC from Debian bookworm / libstdc++ standard library |
| `python3` | `source.py` | `python:3.14-slim-bookworm` | `python:3.14-slim-bookworm` | Python 3.14 standard library only |
| `java` | `Main.java` | `eclipse-temurin:25.0.3+9-jdk` | `eclipse-temurin:25.0.3+9-jre` | Java 25 standard library only |
| `javascript` | `source.js` | `node:24-bookworm-slim` | `node:24-bookworm-slim` | Node.js 24 built-in modules only |
| `kotlin` | `Main.kt` | `eclipse-temurin:25.0.3+9-jdk` + `kotlin-compiler-2.3.0` (GitHub release) | `eclipse-temurin:25.0.3+9-jre` | Kotlin 2.3.0 / JVM standard library packaged into the produced jar |
| `fpc-pascal` | `source.pas` | `debian:bookworm-slim` + `fp-compiler=3.2.2+dfsg-20` | `debian:bookworm-slim` | Free Pascal 3.2.2 compiler/runtime |
| `go` | `source.go` | `golang:1.26.2-bookworm` | `debian:bookworm-slim` | Go 1.26 standard library only, single-file builds |
| `rust` | `source.rs` | `rust:1.96.1-bookworm` | `debian:bookworm-slim` + `libgcc-s1` | Rust 1.96 standard library only, single-file `rustc` builds |
| `c-sharp` | `source.cs` | `dotnet/sdk:10.0.301` | `dotnet/runtime:10.0.9` | .NET 10 base class library only |
| `haskell` | `source.hs` | `debian:bookworm-slim` + `ghc=9.0.2-4` | `debian:bookworm-slim` + `libgmp10` + `libffi8` | GHC 9.0.2, Haskell standard library (`base`, `Data.List`, `Data.Map`, etc.) |
| `lua` | `source.lua` | `debian:bookworm-slim` + `lua-5.5.0` (source build) | `debian:bookworm-slim` + `lua-5.5.0` (source build) | Lua 5.5 standard library only |
| `prolog` | `source.pl` | `debian:bookworm-slim` + `swi-prolog-nox=9.0.4+dfsg-2` | `debian:bookworm-slim` + `swi-prolog-nox=9.0.4+dfsg-2` | SWI-Prolog 9.0.4 standard library; entry point via `:- initialization(main, main).` |
| `fortran` | `source.f90` | `debian:bookworm-slim` + `gfortran=4:12.2.0-3` | `debian:bookworm-slim` + `libgfortran5` + `libquadmath0` + `libgcc-s1` | Fortran 2018 free-form, gfortran 12.2.0 |
| `swift` | `source.swift` | `swift:6.3.2` + Static Linux SDK (musl) | `debian:bookworm-slim` | Swift 6.3 standard library only, single-file builds; statically linked binary |
| `ruby` | `source.rb` | `ruby:4.0-slim-bookworm` | `ruby:4.0-slim-bookworm` | Ruby 4.0 standard library only |
| `bash` | `source.sh` | `debian:bookworm-slim` | `debian:bookworm-slim` | Bash 5.2 built-ins and standard POSIX utilities included in the base image |

## Pinned Image Families

| Image tag | Version family |
| --- | --- |
| `debian:bookworm-slim` | Debian 12 (bookworm) |
| `ruby:4.0-slim-bookworm` | Ruby 4.0 on Debian bookworm slim |
| `python:3.14-slim-bookworm` | Python 3.14 on Debian bookworm slim |
| `node:24-bookworm-slim` | Node.js 24 on Debian bookworm slim |
| `golang:1.26.2-bookworm` | Go 1.26 on Debian bookworm |
| `rust:1.96.1-bookworm` | Rust 1.96 on Debian bookworm |
| `eclipse-temurin:25-jdk` | Temurin JDK 25 |
| `eclipse-temurin:25-jre` | Temurin JRE 25 |
| `mcr.microsoft.com/dotnet/sdk:10.0` | .NET SDK 10 |
| `mcr.microsoft.com/dotnet/runtime:10.0` | .NET Runtime 10 |
| `ghc=9.0.2-4` (Debian bookworm) | GHC 9.0.2 |
| `lua-5.5.0` (source build from lua.org) | Lua 5.5.0 |
| `swi-prolog-nox=9.0.4+dfsg-2` (Debian bookworm) | SWI-Prolog 9.0.4 |
| `gfortran=4:12.2.0-3` (Debian bookworm) | gfortran 12.2.0 |
| `swift:6.3.2` | Swift 6.3.2 official Linux toolchain |
| `swift-6.3.2-RELEASE_static-linux-0.1.0` | Swift Static Linux SDK (musl) |

## Why Alpine Was Removed

NOCA previously used Alpine for several judge images. That kept image sizes low,
but it also introduced musl-specific runtime and filesystem differences that
made `isolate` execution less predictable, especially for interpreter-based
languages. Standardizing on Debian-family or other mainstream glibc-based
images reduces sandbox-specific path issues, keeps runtime loader behavior more
uniform, and simplifies the worker's runtime bind logic.

## Per-Language Notes

### C (`gcc-c17`)

- Compile command:
  - `gcc -std=c17 -O2 -lm -o /sandbox/solution /sandbox/source.c`
- Base image: `debian:bookworm-slim`
- Available:
  - GCC from Debian bookworm
  - glibc headers and runtime
  - standard C library headers
  - math library via `-lm`
- Not available:
  - C++
  - `make`, `cmake`, or other build systems
  - third-party libraries beyond what the image packages already provide

### C++ (`gcc-cpp23`)

- Standard:
  - C++23
- Compile command:
  - `g++ -std=c++23 -O2 -o /sandbox/solution /sandbox/source.cpp`
- Run command:
  - `/sandbox/solution`
- Base image: `debian:bookworm-slim`
- Available:
  - GCC from Debian bookworm (`g++`)
  - libstdc++ from Debian bookworm
  - glibc headers and runtime
  - standard C and C++ library headers
- Not available:
  - build systems such as `make` or `cmake`
  - third-party C++ libraries beyond what the image packages provide

### Python (`python3`)

- Compile step is only a syntax check:
  - `/usr/local/bin/python3 -m py_compile /sandbox/source.py`
- Run command:
  - `/usr/local/bin/python3 -u /sandbox/source.py`
- Base image: `python:3.14-slim-bookworm`
- Available:
  - Python 3.14 standard library
- Not available:
  - `pip`
  - `setuptools`
  - third-party PyPI packages

### Java (`java`)

- Compile command uses `javac` from Temurin JDK 25.0.3+9 and packages a runnable jar.
- Run command:
  - `/opt/java/openjdk/bin/java -Xss64m -Xmx256m -jar /sandbox/solution.jar`
- Base image: `eclipse-temurin:25-jdk` / `eclipse-temurin:25-jre`
- Available:
  - Java 25 standard library
- Not available:
  - Maven
  - Gradle
  - external jars or dependency downloads

### JavaScript (`javascript`)

- Compile step is a syntax check only:
  - `node --check /sandbox/source.js`
- Run command:
  - `/usr/local/bin/node /sandbox/source.js`
- Base image: `node:24-bookworm-slim`
- Available:
  - Node.js 24 built-in modules such as `fs`, `path`, `crypto`, `util`, `stream`
- Not available:
  - `npm install`
  - third-party npm packages

### Kotlin (`kotlin`)

- Compile command:
  - `kotlinc /sandbox/Main.kt -include-runtime -d /sandbox/solution.jar`
- Run command:
  - `/opt/java/openjdk/bin/java -Xss64m -Xmx256m -jar /sandbox/solution.jar`
- Pinned: `kotlin-compiler-2.3.0` installed from the official GitHub release tarball.
  The distro package (`kotlin=1.3.31`) is not used because it is dangerously outdated.
  2.3.0 is required for JDK 25 host-runtime compatibility.
- Available:
  - Kotlin 2.3.0 compiler (`kotlinc`)
  - Kotlin standard library bundled into the output jar by `-include-runtime`
  - Java 25 runtime
- Not available:
  - Gradle
  - Maven
  - external dependencies

### Pascal (`fpc-pascal`)

- Compile command:
  - `fpc -O2 -o/sandbox/solution /sandbox/source.pas`
- Run command:
  - `/sandbox/solution`
- Base image: `debian:bookworm-slim`
- Available:
  - Free Pascal 3.2.2 compiler/runtime
- Not available:
  - Lazarus IDE libraries
  - third-party units beyond the FPC standard library

### Go (`go`)

- Compile command:
  - `go build -trimpath -ldflags=-s -w -o /sandbox/solution /sandbox/source.go`
- Run command:
  - `/sandbox/solution`
- Base images:
  - compile: `golang:1.26.2-bookworm`
  - run: `debian:bookworm-slim`
- Available:
  - Go 1.26 compiler and standard library at compile time
  - static binaries by default because `CGO_ENABLED=0`
- Not available:
  - multi-file modules
  - `go get` or dependency downloads
  - third-party modules

### Rust (`rust`)

- Compile command:
  - `rustc --edition=2024 -O -C debuginfo=0 -C panic=abort -o /sandbox/solution /sandbox/source.rs`
- Run command:
  - `/sandbox/solution`
- Base images:
  - compile: `rust:1.96.1-bookworm`
  - run: `debian:bookworm-slim`
- Available:
  - Rust 1.96 compiler and standard library at compile time
  - Rust 2024 edition
- Not available:
  - Cargo projects
  - crates.io downloads
  - third-party crates

### C# (`c-sharp`)

- The judge publishes the submitted `source.cs` directly as a .NET 10 file-based app
  (no generated `.csproj`); the target framework, output type, and assembly name
  (`solution`) are all derived from the source file plus `-p:` overrides on the
  `dotnet publish` command line.
- Any file-based app directive (`#:package`, `#:sdk`, `#:project`, `#:property`) at
  the top of the source is rejected before compilation: those directives could pull
  NuGet packages, switch SDKs (e.g. ASP.NET Core), or reference paths outside the
  sandbox, none of which fit the single-file, no-dependencies contract.
- Build flow:
  - `dotnet publish` in Release mode with `-p:PublishAot=false` (file-based apps
    default to Native AOT publishing, which this judge does not use)
  - package sources are cleared with a generated `NuGet.Config`
- Run command:
  - `/bin/sh -c 'rm -rf /sandbox/csharp-run && mkdir -p /sandbox/csharp-run && tar -xf /sandbox/solution.tar -C /sandbox/csharp-run && export DOTNET_EnableWriteXorExecute=0 && exec /usr/bin/dotnet /sandbox/csharp-run/solution.dll'`
- Base images: `dotnet/sdk:10.0` / `dotnet/runtime:10.0`
- Available:
  - .NET 10 SDK at compile time
  - .NET 10 runtime at run time
  - .NET base class library
- Not available:
  - external NuGet package restore
  - file-based app directives (`#:package`, `#:sdk`, `#:project`, `#:property`)
  - custom project files from contestants

### Haskell (`haskell`)

- Compile command:
  - `ghc -O2 -o /sandbox/solution /sandbox/source.hs`
- Run command:
  - `/sandbox/solution`
- Base image: `debian:bookworm-slim`
- Available:
  - GHC 9.0.2 from Debian bookworm
  - `libgmp10` and `libffi8` runtime libraries
  - GHC base libraries (`Prelude`, `Data.List`, `Data.Map`, `Data.Set`, `System.IO`, etc.)
- Not available:
  - Cabal or Stack
  - third-party Hackage packages

### Lua (`lua`)

- Compile step is a parse-only syntax check:
  - `/usr/local/bin/luac -p /sandbox/source.lua`
- Run command:
  - `/usr/local/bin/lua /sandbox/source.lua`
- Base image: `debian:bookworm-slim`
- Pinned: `lua-5.5.0` built from the official lua.org source tarball with
  `make linux` (Debian bookworm's `lua5.4` apt package tops out at 5.4.4). The
  build only needs `libc`/`libm`, both already present in the base image; the
  build toolchain itself is confined to a throwaway multi-stage builder and
  does not ship in either the compile or run image.
- Available:
  - Lua 5.5 standard library (`io`, `math`, `string`, `table`, `os`, etc.)
- Not available:
  - LuaRocks or third-party modules

### Prolog (`prolog`)

- Compile step is a syntax check (loads and immediately halts):
  - `/usr/bin/swipl -q --on-error=status -g halt -t halt /sandbox/source.pl`
- Run command:
  - `/usr/bin/swipl -q -O /sandbox/source.pl`
- Base image: `debian:bookworm-slim`
- Available:
  - SWI-Prolog 9.0.4 standard library
- Not available:
  - SWI-Prolog GUI libraries (`xpce`)
  - third-party pack downloads
- **Entry-point contract**: the source file must contain `:- initialization(main, main).`
  so SWI-Prolog invokes `main/1` (the argument list) on startup.

### Fortran (`fortran`)

- Compile command:
  - `gfortran -O2 -std=f2018 -o /sandbox/solution /sandbox/source.f90`
- Run command:
  - `/sandbox/solution`
- Base image: `debian:bookworm-slim`
- Available:
  - gfortran 12.2.0 from Debian bookworm
  - `libgfortran5`, `libquadmath0`, `libgcc-s1` runtime libraries
  - Fortran 2018 standard (`-std=f2018`), free-form source (`source.f90`)
- Not available:
  - multi-file projects
  - third-party Fortran libraries beyond what the image packages provide

### Swift (`swift`)

- Compile: the single source file is wrapped in a throwaway SwiftPM package and built with
  `swift build -c release --swift-sdk "$(uname -m)-swift-linux-musl"`, then the product binary
  is copied to `/sandbox/solution`. (`--swift-sdk` is a SwiftPM selector; `swiftc` does not
  accept it, hence the package wrapper.)
- Run command:
  - `/sandbox/solution`
- Base image: compile `swift:6.3.2` with the Static Linux SDK (musl); run `debian:bookworm-slim`
- Available:
  - Swift 6.3 compiler, standard library, and Foundation at compile time
  - statically linked (musl) binary, so the run image carries no Swift runtime and the
    sandbox needs no directory binds
- Not available:
  - third-party Swift Package Manager dependencies (no network at build time)

## Practical Guidance For Teams

- If your solution needs a third-party package manager, assume it will not work.
- Prefer standard-library-only solutions.
- For JavaScript, rely only on Node built-ins.
- For Go and Rust, submit a single source file and rely only on the standard library.
- For C#, rely only on assemblies that ship with .NET 10.
- For Java and Kotlin, rely only on the JDK/JRE standard libraries bundled with the image.
- For Prolog, end your source file with `:- initialization(main, main).` where `main(_)` is your entry predicate.
- For Fortran, use free-form source layout (`.f90`) and target the Fortran 2018 standard.
- For Swift, submit a single source file and rely only on the standard library.
