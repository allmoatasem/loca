# Svelte e2e suite

Playwright-based smoke coverage for Loca's browser UI. Replaces the
legacy-HTML-targeted Playwright suite retired 2026-04-18.

## Scope

Smoke-only. Every spec verifies one critical flow renders + responds
correctly. We do **not** cover:
- full chat streaming (requires a loaded model — moot in CI)
- voice mode (mic perms flake in headless)
- actual backend integration (tests stub every route via `page.route`)

Deeper integration coverage belongs in unit tests
(`tests/test_proxy.py`, `tests/test_orchestrator.py`) where it runs
faster and more reliably.

## Layout

```
ui/
├── playwright.config.ts    — baseURL, reporter, webServer config
└── e2e/
    ├── helpers.ts          — mockBackend() stubs every route the UI polls
    └── smoke.spec.ts       — 7 smoke specs: sidebar, composer banner,
                              RAM indicator, Memory panel, bulk-delete
                              controls, Manage Models, Obsidian Watcher
```

## Running locally

```bash
make ui-e2e
```

First run pulls the Chromium headless-shell binary (~100 MB, cached
at `~/Library/Caches/ms-playwright/`). Subsequent runs reuse it.

To iterate on a single spec:

```bash
cd ui
npx playwright test smoke.spec.ts -g "RAM indicator"
```

The `--ui` mode is handy for debugging:

```bash
cd ui && npx playwright test --ui
```

## Adding a test

1. **Decide if it's smoke.** If you're pinning a single prop or
   rendering branch, a Svelte unit test via `@testing-library` is
   better. e2e is for flows that span store + routing + component
   interactions.
2. **Stub the backend.** Call `mockBackend(page, { … })` in a
   `beforeEach` — never hit a real FastAPI server. Customise the
   state object for the scenario.
3. **Prefer role/text selectors.** `page.getByRole('button', { name: /…/ })`
   is more robust than CSS selectors against Svelte class-hash changes.
4. **Navigate by route.** `/ui/memory`, `/ui/manage-models`, `/ui/vault`
   — the app's router resolves these to the matching overlay.

## CI

The suite runs on every PR. `webServer` is told to reuse an existing
dev server when available (local) and spin one up on demand (CI).
Reporter is `github` + `html` in CI for annotations and an attached
HTML report; plain list locally.

## Failure triage

On retry, Playwright captures a trace + screenshot — download the
CI artifact and run `npx playwright show-trace <file>.zip` to step
through it. Most flakes are:

- **Route stub missing** → the test loaded the page, the page polled
  a route the helper doesn't stub, and the UI hung waiting. Add
  the route to `helpers.ts`.
- **Race on dynamic render** → the element is rendered after a
  poll interval (e.g. the 10 s RAM refresh). Bump `expect(...)`'s
  `{ timeout: N }` option for that specific assertion.
