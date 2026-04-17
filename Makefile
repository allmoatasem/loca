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

.PHONY: check test e2e swift build all ci import train-build train ui-dev ui-build openapi

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

# ── MLX LoRA training (foundation — see docs) ────────────────────────────────

train-build:
	@[ "$(out)" ] || (echo "Usage: make train-build out=<dataset-dir>"; exit 1)
	$(PYTHON) -m src.training.cli build --out "$(out)"

train:
	@[ "$(model)" ] && [ "$(data)" ] || (echo "Usage: make train model=<path> data=<dataset-dir> [iters=1000] [adapter=./loca-adapter]"; exit 1)
	$(PYTHON) -m src.training.cli train --model "$(model)" --data "$(data)" --iters $${iters:-1000} --adapter-out $${adapter:-./loca-adapter}

# ── Svelte UI (second UI — full parity with SwiftUI) ──────────────────────────

ui-dev:
	cd ui && npm install --silent --no-audit --no-fund && npm run dev

ui-build:
	cd ui && npm install --silent --no-audit --no-fund && npm run build

# Regenerate ui/openapi.json + ui/src/lib/api.ts from FastAPI's live schema.
# Run whenever routes or schemas change; both files are committed.
openapi:
	@echo "▶ export openapi.json"
	$(PYTHON) -c "from src.proxy import app; import json, sys; json.dump(app.openapi(), sys.stdout, indent=2)" > ui/openapi.json
	@echo "▶ generate ui/src/lib/api.ts"
	cd ui && npm install --silent --no-audit --no-fund && npm run openapi
	@echo "✓ api.ts regenerated"
