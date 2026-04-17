## Summary

<!-- 1–3 sentences. Focus on the *why* rather than a diff summary. -->

## Test plan

- [ ] `make check` (ruff + mypy)
- [ ] `make test` (pytest)
- [ ] `swift build --package-path Loca-SwiftUI` (or `./build_app.sh` for a full bundle)
- [ ] Playwright e2e if any HTML/Svelte change (`make e2e`)
- [ ] Manual verification of the user-facing change

## UI parity (tick whichever applies; leave rest)

Loca ships two UIs — SwiftUI (Mac) and Svelte (everything else). Every user-facing
change should land in both in the same PR. SwiftUI is the source of truth.

- [ ] Not a UI change — skip this section.
- [ ] Updated in **Swift** (`Loca-SwiftUI/`).
- [ ] Updated in **Svelte** (`ui/`).
- [ ] Split across PRs — linked follow-up:

## Notes

<!-- Anything reviewers should know: risks, rollouts, follow-ups. -->
