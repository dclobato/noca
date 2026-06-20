.PHONY: help lint format check typecheck test assets clean all

help:
	@echo "Available targets:"
	@echo "  lint      - ruff check (errors only, no fix)"
	@echo "  format    - ruff format + ruff check --fix"
	@echo "  check     - ruff check + ruff format --check (CI-safe, no writes)"
	@echo "  typecheck - mypy static type checking"
	@echo "  test      - run pytest"
	@echo "  assets    - build static assets (e.g. for production)"
	@echo "  all       - check + typecheck + test"

lint:
	uv run ruff check .

format:
	uv run ruff format .
	uv run ruff check --fix .

check:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run mypy web shared autojudge

test:
	uv run pytest

assets:
	uv run python scripts/fetch_assets.py

clean:
	find . -path ./.docker -prune -o -type d -name __pycache__ -exec rm -rf {} +
	find . -path ./.docker -prune -o -type d -name .mypy_cache -exec rm -rf {} +
	find . -path ./.docker -prune -o -type d -name .ruff_cache -exec rm -rf {} +
	find . -path ./.docker -prune -o -type d -name .pytest_cache -exec rm -rf {} +
	find . -path ./.docker -prune -o -type f -name "*.pyc" -exec rm -f {} +
	find . -path ./.docker -prune -o -type f -name "*.pyo" -exec rm -f {} +

distclean:
	find . -path ./.docker -prune -o -type d -name __pycache__ -exec rm -rf {} +
	find . -path ./.docker -prune -o -type d -name .mypy_cache -exec rm -rf {} +
	find . -path ./.docker -prune -o -type d -name .ruff_cache -exec rm -rf {} +
	find . -path ./.docker -prune -o -type d -name .pytest_cache -exec rm -rf {} +
	find . -path ./.docker -prune -o -type f -name "*.pyc" -exec rm -f {} +
	find . -path ./.docker -prune -o -type f -name "*.pyo" -exec rm -f {} +
	find shared/static/vendor -mindepth 1 -type f ! -name ".git*" -exec rm -f {} +
	find shared/static/vendor -mindepth 1 -type d \
		! -path "shared/static/vendor/fonts" \
		! -path "shared/static/vendor/highlight" \
		! -path "shared/static/vendor/highlight/languages" \
		! -path "shared/static/vendor/highlight/plugins" \
		! -path "shared/static/vendor/highlight/styles" \
		-exec rm -rf {} +
	mkdir -p shared/static/vendor/fonts \
		shared/static/vendor/highlight/languages \
		shared/static/vendor/highlight/plugins \
		shared/static/vendor/highlight/styles
	find shared/static/webfonts -mindepth 1 -type f ! -name ".git*" -exec rm -f {} +

all: assets check typecheck test
