# Loca developer tasks
# Usage: make <target>
#
#   make check     — lint + type-check Python
#   make test      — run Python unit tests
#   make e2e       — run Playwright e2e tests (requires app running)
#   make swift     — build Swift package only (fast, no bundle)
#   make build     — full release build + bundle + install to ~/Applications
#   make all       — check + test + swift (CI-equivalent, no bundle)
#   make ci        — alias for all (matches what GitHub Actions runs)

.PHONY: check test e2e swift build all ci import

PYTHON := $(CURDIR)/.venv/bin/python3
RUFF    := $(shell command -v ruff)
MYPY    := $(shell command -v mypy)

# ── Python checks ─────────────────────────────────────────────────────────────

check:
	@echo "▶ ruff"
	$(RUFF) check src tests
	@echo "▶ mypy"
	$(MYPY) src --ignore-missing-imports
	@echo "✓ check passed"

# ── Python tests ──────────────────────────────────────────────────────────────

test:
	@echo "▶ pytest (unit tests)"
	$(PYTHON) -m pytest tests/ -q --tb=short --ignore=tests/e2e -m "not network"
	@echo "✓ tests passed"

# ── Playwright e2e ────────────────────────────────────────────────────────────

e2e:
	@echo "▶ playwright e2e (requires Loca running on :8000)"
	$(PYTHON) -m pytest tests/e2e/ -q --tb=short

# ── Swift build (fast, no bundle) ─────────────────────────────────────────────

swift:
	@echo "▶ swift build"
	swift build --package-path Loca-SwiftUI
	@echo "✓ swift build passed"

# ── Full release build + install ──────────────────────────────────────────────

build:
	@echo "▶ build_app.sh"
	./build_app.sh

# ── All checks (CI-equivalent, no bundle) ─────────────────────────────────────

all: check test swift
	@echo "✓ all checks passed"

ci: all

# ── Knowledge import ──────────────────────────────────────────────────────────

import:
	@[ "$(path)" ] || (echo "Usage: make import path=<path-or-url>"; exit 1)
	$(PYTHON) -m src.importers.cli "$(path)"
