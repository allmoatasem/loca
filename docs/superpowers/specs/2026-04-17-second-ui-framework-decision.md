# Second UI Framework — Decision Spec

**Date:** 2026-04-17
**Status:** Draft — awaiting approval
**Branch:** TBD (implement migration on fresh branches after approval)

---

## Problem

Loca ships two user interfaces:

- **Swift (`Loca-SwiftUI/`)** — native macOS shell, ~17 000 lines. Stays as-is; Mac-first is the product stance.
- **HTML (`src/static/index.html`)** — served by FastAPI for any platform. Currently **4 119 lines in one file**: 121 top-level JS functions, 116 `getElementById` calls, 81 inline `onclick`/`onchange` handlers, 24 `localStorage` touches, 35 `fetch` calls. No build step, no framework, no types.

Every feature now ships in both. That cost is visible in every recent PR — the Knowledge panel, the Preferences models-directory row, the `<think>` block renderer, the `[memory: N]` citations — each had to be implemented twice with subtly different state plumbing. The HTML side has no component boundaries; sections share a global namespace (`history`, `streaming`, `currentConvId`, `attachments`…) and are stitched together with string IDs and inline handlers.

Status quo makes every UI PR a two-stop journey, grows the single file without bound, and leaks bugs through asymmetries between the two implementations.

---

## Goal

Decide on a framework for the **second UI** (the non-Mac, browser-served one) that minimises long-term maintenance cost for a single-developer project, without breaking Loca's self-contained bundle story.

**Non-goals**

- Replacing the Swift UI. SwiftUI stays the Mac-native surface.
- Porting the whole HTML to a new framework in a single PR. Migration is staged after the decision.
- Adopting anything that breaks the "FastAPI serves static files from `src/static/`" contract. The bundled app must keep working with no runtime network.

---

## Decision criteria

Ranked by weight:

1. **Maintenance cost over the next 3 years** — single maintainer, part-time. Framework stability, ecosystem churn, and boilerplate dominate here.
2. **Smallest runtime and bundle size** — Loca's self-contained app ships the built assets; smaller is better for startup and for the DMG.
3. **Shortest learning curve coming from plain HTML/CSS/JS** — current code base is vanilla JS; every hour on framework arcana is an hour not spent on product.
4. **Good fit for streaming chat SPA** — SSE to `/v1/chat/completions`, live Markdown render, attachment state, preferences panels, memory browser. Server-rendering isn't useful here.
5. **Preserve the self-contained bundle** — build output must be plain files `src/static/` can serve; no runtime server beyond FastAPI.
6. **Tooling footprint** — Node/npm is acceptable only as a dev-time dependency. No runtime Node in `Loca.app`.

---

## Options considered

### A. Stay vanilla — split into ES modules only

No new framework. Break `index.html` into `<script type="module">` imports across several `.js` files. Keep existing DOM patterns.

- Pros: zero new tooling; smallest possible runtime; no migration risk; the 4 119-line file becomes ten 400-line files.
- Cons: doesn't fix the real problems — global state, stringly-typed wiring, no component boundaries, no type safety. Removes a symptom, keeps the disease.
- Verdict: **not enough lift**. Solves organisation, not maintainability.

### B. Web Components + plain JS

Use native Custom Elements for structure. No build step. Still vanilla.

- Pros: stdlib, framework-free, future-proof, no dependencies.
- Cons: verbose ergonomics (Shadow DOM, slot semantics, attribute/property plumbing). Little ecosystem for chat-UI primitives. Developer experience is poor for a single maintainer.
- Verdict: **right philosophy, wrong cost curve** at Loca's size.

### C. HTMX

HTML-over-the-wire: server returns HTML fragments, HTMX swaps them into the DOM.

- Pros: minimal client code, stays close to FastAPI, trivial mental model.
- Cons: Loca's chat UI is fundamentally client-state heavy — streaming tokens, local history buffer, attachments, Markdown+Prism render, Preferences with optimistic UI, memory browser pagination. Fighting HTMX's grain on every panel would cost more than a small framework.
- Verdict: **wrong shape** for a chat SPA.

### D. Vue 3

Reactive component framework. Can run without a build (CDN import) or with Vite.

- Pros: gentle learning curve, excellent docs, optional runtime-only mode, stable core.
- Cons: runtime is ~25 KB min+gz before any app code, larger than the lightest options. SFC `.vue` files add a compilation step for serious use. Bigger ecosystem than Svelte but also more churn (Options API vs Composition API, Pinia vs Vuex).
- Verdict: **solid middle ground** but not the smallest or lightest path.

### E. React + Vite

Default industry pick.

- Pros: biggest ecosystem, most documentation, easiest to hire for.
- Cons: not a relevant advantage here (single maintainer). Runtime is ~40 KB+ min+gz. JSX is a bigger jump from plain HTML than `.svelte` or `.vue`. React's ecosystem has the highest churn of these options (routers, state libs, form libs reinvented every cycle).
- Verdict: **overkill** for Loca's size.

### F. Solid

React-like syntax, Svelte-like performance through fine-grained reactivity.

- Pros: small runtime, fast, stable API.
- Cons: smallest ecosystem of the serious options, niche. More risk on hitting a library gap.
- Verdict: **attractive but riskier** than Svelte for the same wins.

### G. Svelte 5 + Vite (recommended)

Compile-time framework. `.svelte` files compile to small imperative JS with no VDOM runtime.

- Pros:
    - Smallest non-zero runtime of the serious options (~3 KB gzipped core; per-component code is inlined).
    - `.svelte` files are HTML + JS + scoped CSS in one file — closest syntactic jump from Loca's current `index.html` style.
    - Reactivity with minimal ceremony (`$state`, `$derived` in Svelte 5 runes, or `$:` in legacy syntax).
    - Scoped styles by default; no Tailwind / CSS-in-JS needed.
    - Vite gives fast dev loop (`npm run dev` with HMR) and produces plain static files for FastAPI to serve.
- Cons:
    - Smaller ecosystem than React/Vue. Chat-UI primitives (Markdown, code highlighting) still need wiring — but we already own those paths from the current code and Prism already ships under `/assets/`.
    - Adds a Node dev dependency and a compiled output step.
- Verdict: **best maintenance-cost-per-feature** for a single maintainer with vanilla-JS heritage.

---

## Recommendation: **Svelte 5 + Vite**

Reasoning in one paragraph:

> For a single-maintainer chat SPA that's already closer to HTML than to JSX, Svelte's compile-time model gives the smallest runtime surface, the shortest syntactic jump from the current code, and the cleanest escape from the global-state / inline-handler pattern that's dragging the HTML UI down. Vue and React would work; they'd just cost more every month than Svelte for the same outcome.

---

## Migration plan (phased, not in this PR)

Each phase is its own PR. Old `index.html` keeps working until the final phase — both UIs coexist behind different paths while migration is in-flight.

**Phase 0 — scaffolding (one PR)**
- Add `ui/` at the repo root with `vite.config.ts`, `package.json`, and a minimal Svelte "hello" app.
- Vite build output goes to `src/static/ui/` (mounted by FastAPI at `/ui`).
- Add `.gitignore` entries for `ui/node_modules` and `ui/.svelte-kit`.
- Add an `npm run build --prefix ui` step to `build_app.sh` and the CI lint job.
- Outcome: visiting `/ui` loads the new app; `/` still serves the old `index.html`.

**Phase 1 — port one isolated panel (one PR)**
- Port the **Glossary** page (static content, no streaming, no state beyond open/closed).
- Validates: Vite build output plays nicely with the bundled app, reload loop works, theme variables carry over.
- Risk floor: if the pipeline doesn't work, we learned that with no migration cost.

**Phase 2 — sidebar + model picker (one PR)**
- Includes `fetch`-based state loading, localStorage for preferences.
- Exercises Svelte stores.

**Phase 3 — Preferences panel (one PR)**
- The most form-heavy surface; validates reactivity patterns on nested state.

**Phase 4 — the chat view (one or two PRs)**
- SSE streaming, Markdown rendering with live updates, `<think>` splitting, `[memory: N]` citation preservation, attachments, Prism highlighting.
- This is the hardest slice. Do it last when the framework's patterns are proven.

**Phase 5 — cutover (one PR)**
- Flip `/` to serve `src/static/ui/index.html`.
- Delete the old `src/static/index.html` and its sibling assets.
- Update Playwright e2e tests to point at the new selectors.

**Phase 6 — follow-up cleanup**
- Remove onclick/onchange handlers that only existed to reach into the old file.
- Consolidate shared shapes with Swift via generated TypeScript types from FastAPI's OpenAPI schema (nice-to-have, not blocking).

---

## Costs and risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Build step breaks `build_app.sh` in CI | medium | Phase 0 commits the `ui/dist/` integration with a CI check before any UI work lands |
| Svelte 5 runes introduce breaking changes | low | Svelte 5 is stable (GA); we pin the major |
| Ecosystem gap for a needed primitive | low | Every UI element we have today already exists as hand-written code; porting beats finding a library |
| Contributor ramp cost | low | `.svelte` files are readable with no framework knowledge |
| Dual-UI bug drift during staged migration | medium | Keep Playwright tests for both UIs until Phase 5; prefer porting whole panels, not half-panels |

---

## What changes today if this is approved

Nothing in the shipped binary. Approval kicks off **Phase 0** as the next PR. No user-facing change until Phase 1.

---

## Alternative to approve instead

If Svelte is rejected, the strongest runner-up is **Vue 3 with Vite** for the same staged migration plan. Everything in this spec (phases, risks, coexistence strategy) applies identically; only the framework in `ui/` changes.
