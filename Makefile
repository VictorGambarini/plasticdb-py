PY ?= .venv/bin/python

.PHONY: docs install test lint format typecheck check build contract-refresh contract-check smoke

install:
	uv venv .venv && uv pip install -p $(PY) -e . --group dev

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format:
	$(PY) -m ruff format .

typecheck:
	$(PY) -m mypy

check: lint typecheck test

build:
	rm -rf dist && $(PY) -m build && $(PY) -m twine check --strict dist/*

contract-refresh:
	$(PY) -m scripts.refresh_contract

contract-check:
	$(PY) -m scripts.refresh_contract --check

smoke:
	$(PY) -m scripts.live_smoke

docs:
	NO_MKDOCS_2_WARNING=1 mkdocs build --strict
