# Loca developer tasks
# Usage: make <target>
#
#   make check     — lint + type-check Python
#   make test      — run Python unit tests
#   make swift     — build Swift package only (fast, no bundle)
#   make build     — full release build + bundle + install to ~/Applications
#   make all       — check + test + swift (CI-equivalent, no bundle)
#   make ci        — alias for all (matches what GitHub Actions runs)

.PHONY: check test swift build all ci import train-build train ui-dev ui-build openapi

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
	$(PYTHON) -m pytest tests/ -q --tb=short -m "not network"
	@echo "✓ tests passed"

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

eval:
	@[ "$(base)" ] && [ "$(adapter)" ] || (echo "Usage: make eval base=<model-name> adapter=<adapter-name> [prompts=<path>] [out=<path>]"; exit 1)
	$(PYTHON) -m src.training.eval_cli run --base "$(base)" --adapter "$(adapter)" \
		$${prompts:+--prompts "$(prompts)"} $${out:+--out "$(out)"}

# ── Svelte UI (second UI — full parity with SwiftUI) ──────────────────────────

ui-dev:
	cd ui && npm install --silent --no-audit --no-fund && npm run dev

ui-build:
	cd ui && npm install --silent --no-audit --no-fund && npm run build

# Svelte e2e smoke suite. First run does a one-off `npx playwright
# install chromium` to pull the browser binary; reuses it thereafter.
ui-e2e:
	cd ui && npm install --silent --no-audit --no-fund && npm run test:e2e:install && npm run test:e2e

# Regenerate ui/openapi.json + ui/src/lib/api.ts from FastAPI's live schema.
# Run whenever routes or schemas change; both files are committed.
openapi:
	@echo "▶ export openapi.json"
	$(PYTHON) -c "from src.proxy import app; import json, sys; json.dump(app.openapi(), sys.stdout, indent=2)" > ui/openapi.json
	@echo "▶ generate ui/src/lib/api.ts"
	cd ui && npm install --silent --no-audit --no-fund && npm run openapi
	@echo "✓ api.ts regenerated"
