# Repository Guidelines

## Project Structure & Module Organization
This repository is split into a Rust backend and a Rust/WASM frontend. `server/` is a Cargo workspace with crates in `data/`, `service/`, `cli/`, and `server-v2/`. `client-v2/` contains the Leptos client, with Rust sources in `client-v2/src/` and static CSS in `client-v2/static/`. Top-level `config/` holds example TOML configuration, `tests/inputs/` stores BOCA webcast fixtures and `.revelation` golden files, and `doc/` contains operational notes such as nginx examples.

## Build, Test, and Development Commands
Use Docker for the default local stack:

```bash
docker compose up
docker compose run printurls
```

For local development without Docker:

```bash
make rebuild-client-for-release   # build client-v2/release with trunk
make run-standalone-loop          # run server against BOCA_URL from .env
make run-debug-client             # serve the client on :8080 with live reload
cargo test --manifest-path server/Cargo.toml
cargo test --manifest-path server/Cargo.toml --features slow_tests
```

## Coding Style & Naming Conventions
Follow idiomatic Rust formatting and keep code `cargo fmt` clean before opening a PR. Use `snake_case` for modules, files, and functions; `CamelCase` for types; and keep crate boundaries aligned with responsibilities already present in `server/`. Prefer small focused modules over cross-cutting utility files. CSS customizations belong in `client-v2/static/user-styles.css` when they are deployment-specific.

## Testing Guidelines
Backend tests live with the Rust crates, with golden-model coverage in [`server/cli/src/test_revelation.rs`](/home/dclobato/maratona-animator/server/cli/src/test_revelation.rs). Add new BOCA snapshots under `tests/inputs/` and keep the paired `*.revelation` file beside each fixture. Name tests descriptively, and gate expensive fixture suites behind the existing `slow_tests` feature.

## Commit & Pull Request Guidelines
Recent history is PR-driven and branch-oriented, with merge commits like `Merge pull request #49 ...` and topic branches such as `topic/update-runs-with-cli`. Keep commit subjects short, imperative, and scoped to one change. PRs should describe the user-visible effect, note any config or fixture changes, link the related issue when available, and include screenshots or served URLs when UI behavior changes.

## Configuration & Security Tips
Copy and edit `.env` locally; do not commit environment-specific secrets. Treat files in `config/` as templates and keep real credentials outside version control.
