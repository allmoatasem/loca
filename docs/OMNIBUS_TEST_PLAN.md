# Quality omnibus test plan — PR #92

**Bar:** this PR doesn't merge until every check below passes on both Svelte and SwiftUI (where applicable). User drives the walkthrough; anything that fails is fixed inside this PR, not deferred.

**Scope locked:**
- All confirmed bug fixes from the quick-wins backlog + filed #92 items
- Chat-composer consolidation: drop `Agent`, keep single **🌊 Deep Dive** that handles both the multi-role loop AND Playwright-enriched web search when on
- Deferred pull-ins: token-level Writer streaming, explicit Reviewer role, per-conversation adapter override

**Out of scope** (separate PRs if prioritised): tool-equipped sub-agents, QLoRA/DPO, continual training, multi-adapter merging, metric-framework eval harness.

---

## A. Downloads

- [ ] **A1.** Discover tab → pick a model → click Get. **Expected:** within ~1s a progress bar starts moving, not 3+ seconds of nothing.
- [ ] **A2.** Mid-download, click Pause. **Expected:** bar keeps showing the current percent, not `0%` or `—`.
- [ ] **A3.** Click Resume. **Expected:** bar resumes from the same percent; no `0%` flicker.
- [ ] **A4.** Close + reopen the app, check Dock icon. **Expected:** Loca icon visible in Dock when running and in the app switcher.
- [ ] **A5.** Build a DMG (`./build_app.sh` then inspect `Loca.app/Contents/Resources/`). **Expected:** `loca.icns` present; DMG mount shows the correct icon.
- [ ] **A6.** Try to chat before a model is loaded. **Expected:** friendly "Load a model first" banner or disabled input, not a raw `curl: all connection attempts failed`.
- [ ] **A7.** Load a model, chat during the load. **Expected:** input disabled or a "loading model…" overlay until ready.
- [ ] **A8.** Bottom stats bar RAM indicator. **Expected:** matches Activity Monitor's system-used figure to within ~0.5 GB.
- [ ] **A9.** Discover → For You tab → click Get on any row. **Expected:** jumps to Search HF tab with the model name pre-populated.

## B. Chat polish

- [ ] **B1.** Settings → Preferences → typewriter-stream toggle. Flip on, send a message. **Expected:** response renders word-by-word at a readable pace.
- [ ] **B2.** With typewriter on, Preferences has a reading-speed slider. Drag to slowest. **Expected:** display rate drops noticeably.
- [ ] **B3.** Hover over a link or `[memory: N]` pill in an assistant reply. **Expected:** cursor becomes a pointer (🖱️). Swift: paragraph-level (any paragraph containing a link gets the pointer on hover). Svelte: browser-default per link range.
- [ ] **B4.** Ask a question that triggers tool use (via an agentic client). **Expected:** no raw JSON `{"name": "web_search", ...}` in the bubble; either hidden or collapsed into a "called web_search" indicator.
- [ ] **B5.** Click any `[memory: N]` citation. **Expected:** an inline **popover** appears showing the cited source's kind badge (MEMORY / VAULT / WEB / PROJECT), title, and snippet. Web sources also offer an **"Open link ↗"** button. Missing-metadata or phantom indices render a "MISSING" placeholder explaining the turn didn't ship source data. *(Deep-linking the popover into the Memory panel is explicitly out — removed in this PR; follow-up ticket tracks it.)*
- [ ] **B6.** After a reply that used memories but didn't cite inline, the bubble shows a **"📓 N sources used"** expandable footer. **Expected:** clicking expands a list of every retrieved source with kind badge, title, and snippet. Works even when the model skips `[memory: N]` markers entirely.
- [ ] **B7.** Copy-button parity: assistant AND user bubbles both show a `doc.on.doc` icon button. Click it → flips to a checkmark for ~1s, clipboard contains the message text.

## C. Deep Dive consolidation

- [ ] **C1.** Composer shows **one** 🌊 Deep Dive button, no separate Agent. **Expected:** only the one toggle.
- [ ] **C2.** Deep Dive off → normal chat. Deep Dive on → think-block appears with sub-queries and the answer uses Playwright-fetched pages.
- [ ] **C3.** In a Deep Dive reply, the Writer streams token-by-token (deferred item pulled in). **Expected:** text appears progressively, not all at once.
- [ ] **C4.** In a Deep Dive reply, the think-block mentions a Reviewer step dropping K low-signal sources.
- [ ] **C5.** Turn off Deep Dive, send same question. **Expected:** quick single-call response with snippet-only memory context.

## D. Research Partner polish

- [ ] **D1.** Create a project with Obsidian source ON. Overview → Related Notes. **Expected:** no duplicate titles between memory 🧠 and vault 📓 rows.
- [ ] **D2.** Related Notes items never contain raw `[[foo|bar]]` wiki-link syntax.
- [ ] **D3.** Related Notes items never show `--- tags: - xyz ---` frontmatter blocks.
- [ ] **D4.** Long Related-Note titles end with `…` at a word boundary, not mid-word.
- [ ] **D5.** Set a per-conversation adapter override on a single conv. **Expected:** that conv uses the override; siblings in the same project use the project's adapter; other convs use base.

## D-bis. Obsidian Watcher (new)

- [ ] **W1.** Open Vault panel (Svelte + Swift). Header says **Obsidian Watcher**, not "Vault Analyser".
- [ ] **W2.** With no vault watched, pick a detected vault → click **Watch this vault**. **Expected:** appears in the watched list within ~2s; first scan kicks off automatically; last-scan label flips to "syncing…" then a timestamp.
- [ ] **W3.** Click **Scan now** on a watched vault. **Expected:** "syncing…" appears, resolves to "Ns ago · N notes", no UI freeze.
- [ ] **W4.** While a scan is running, switch projects in the sidebar. **Expected:** switch completes instantly — no freeze.
- [ ] **W5.** With 2+ vaults watched, click the **Remove** button on one. **Expected:** row disappears; other vaults unaffected.
- [ ] **W6.** Create a Research Partner project. Sources section shows **Use Obsidian Watcher** toggle. Flip on. Ask a question grounded in vault content. **Expected:** the response cites a vault note; nothing was "synced" into the project.
- [ ] **W7.** Parity: watched list, Scan now, Remove, and the per-project toggle all work identically in Swift and Svelte.

## E. Adapter UX

- [ ] **E1.** Manage Models with no model loaded. **Expected:** adapter picker visible (maybe disabled) with hint, not hidden.
- [ ] **E2.** Svelte Manage Models shows `llmfit` fit-scores (Perfect / Good / Tight) in Discover tab.
- [ ] **E3.** SwiftUI Research → Watches → "Create watch" reads as a proper filled button, not plain text.

## F. Parity spot-checks

For each feature below, open it on Svelte, then Swift, and confirm behaviour matches:

- [ ] **F1.** Voice mode (mic → transcribe → TTS → resume listening)
- [ ] **F2.** Research project Overview → all rows render and work
- [ ] **F3.** Watches tab → create, list, Run-now, delete
- [ ] **F4.** Manage Models → Downloaded + Discover
- [ ] **F5.** Memory viewer → pagination, manual add, extract
- [ ] **F6.** Glossary / Philosophy / Acknowledgements
- [ ] **F7.** Preferences → every tab loads and saves
- [ ] **F8.** Chat bubbles stretch responsively (cap ≈ `100% - 80px`), copy icon on both sides, citation popover stays on-screen (no offscreen clipping when clicking pills near the top edge).
- [ ] **F9.** ⌘F opens an inline chat search bar that highlights matches inside visible bubbles with a yellow `<mark>` background. Escape closes.
- [ ] **F10.** Bottom stats bar shows `model · TTFT · tok/s · total · P+C · context %` with 🔍/🧠 badges when search/memory fired. Matches Swift's `GenerationStatsBar` placement (below messages, above composer).

## G. Autonomous loop (inside Deep Dive)

- [ ] **G1.** Enable Deep Dive, ask a factual question. Think-block shows Researcher + Reviewer + Writer + Verifier phases.
- [ ] **G2.** Citations resolve. Click one → popover shows the actual cited source content (per item B5). In Deep Dive, kinds include MEMORY / VAULT / WEB / PROJECT as appropriate for the pool.
- [ ] **G3.** Invoke phantom: stub a question that's likely to trigger a `[memory: 99]` hallucination. **Expected:** verifier footer flags it.
- [ ] **G4.** Plan checkpoint at `~/Library/Application Support/Loca/data/plans/<conv_id>.md` is written with Phase: done.

---

## Notes

- **Parity as acceptance criterion.** Every omnibus item is "fix X and verify it works identically on Svelte + Swift". No one-shot parity audit — parity happens per item.
- **Do not merge with red boxes.** Any unchecked item that cannot be fixed in this PR must be moved to a follow-up ticket with user's explicit sign-off before merge.
- **UI-triggered adapter training** is explicitly *not* in this PR. It's flagged on #92 as its own work item.
