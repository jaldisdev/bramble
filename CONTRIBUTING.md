# Contributing to bramble

Thanks for your interest in contributing to bramble! This document covers
how to get set up, the expectations for pull requests, and where to look
first if you're new to the codebase.

## Code of Conduct

This project follows a [Code of Conduct](CODE_OF_CONDUCT.md). By
participating, you agree to abide by its terms.

## Project layout

```
crates/bramble-core/   Rust core: parsing, validation, schema IR, SDL, execution
crates/bramble-py/     PyO3 bindings exposing bramble-core to Python
bramble/               Python package: decorators, DI, HTTP adapters, GraphQL types
tests/                 Python test suite (pytest)
examples/              Runnable example schemas (blog, federation_products, ...)
docs/                  User-facing documentation
```

`bramble-core` should contain no PyO3 dependencies — pure Rust logic only.
Anything that touches the Python C API belongs in `bramble-py`. Please keep
this split intact in new code; it's load-bearing for the project's ability
to be embedded elsewhere later.

## Getting started

### Prerequisites

* Rust (stable toolchain, see `rust-toolchain.toml` if present)
* Python 3.11+
* [`uv`](https://github.com/astral-sh/uv) or `pip` for the Python environment

### Setup

```bash
git clone https://github.com/<org>/bramble.git
cd bramble
pip install -e ".[dev]"
cargo build --workspace
```

### Running tests

```bash
# Rust
cargo test --workspace

# Python
pytest

# Lint / format (CI enforces all three, so run them before opening a PR)
ruff check .
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --check
```

## Making a change

1. Open an issue first for anything non-trivial (new public API, behavior
   change, new backend) so we can agree on the approach before you invest
   time in an implementation.
2. Keep PRs focused — one logical change per PR is much easier to review
   and bisect later.
3. Add tests for new behavior. Rust logic in `bramble-core` should have Rust
   unit tests; Python-facing behavior should have a `pytest` test, ideally
   one that exercises the full path (Python → Rust → Python) rather than
   mocking the boundary.
4. Update `docs/` when you change or add public API surface. Undocumented
   public decorators are a known gap we're actively closing — please don't
   add to it.
5. Run the full test suite and linters locally before opening the PR.

## API compatibility with Strawberry

bramble aims to be close to API-compatible with
[Strawberry](https://strawberry.rocks/) wherever practical, so that porting
an existing Strawberry schema is mostly a matter of changing imports. If
you're adding or changing public API surface, check how Strawberry does the
equivalent thing first, and note deliberate deviations in your PR
description and in the relevant `docs/` page.

## Known gaps (good first areas to help with)

These are tracked in the project's internal audit and are good places to
start if you're looking for something concrete to work on:

* `bramble-py` and `bramble-core/src/validation.rs` have little to no direct
  test coverage despite being on the hot path for every request.

If you'd like to work on one of these, feel free to open an issue to claim
it or ask in the PR before starting, so effort isn't duplicated.

## Commit messages and PR descriptions

Write commit messages that explain *why*, not just *what* — the diff already
shows what changed. In the PR description, note any behavior changes,
migration considerations, and what you tested.

## License

By contributing, you agree that your contributions will be dual-licensed
under the [MIT](LICENSE-MIT) and [Apache 2.0](LICENSE-APACHE) licenses, the
same as the rest of the project.
