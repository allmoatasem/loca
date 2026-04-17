<!--
  Phase 0 placeholder. The real UI is ported panel-by-panel in later phases
  (see docs/superpowers/specs/2026-04-17-second-ui-framework-decision.md).
  This page exists only to verify the pipeline:
    - Vite builds into ../src/static/ui/
    - FastAPI serves it at /ui
    - Design tokens from tokens.css are applied
-->
<script lang="ts">
  let backendHealth = $state<'checking' | 'ok' | 'error'>('checking');

  (async () => {
    try {
      const r = await fetch('/health');
      backendHealth = r.ok ? 'ok' : 'error';
    } catch {
      backendHealth = 'error';
    }
  })();
</script>

<main>
  <h1>Loca</h1>
  <p class="tag">Second UI — Svelte scaffolding (Phase 0)</p>

  <section class="card">
    <h2>Status</h2>
    <ul>
      <li>Build pipeline: <span class="ok">active</span></li>
      <li>Backend health: <span class={backendHealth}>{backendHealth}</span></li>
    </ul>
    <p class="hint">
      The real UI lands incrementally in phases 1–5. Meanwhile, use the macOS
      app or the legacy browser UI at <a href="/">/</a>.
    </p>
  </section>
</main>

<style>
  main {
    max-width: 560px;
    margin: 48px auto;
    padding: 0 20px;
    font-family: var(--loca-font-ui);
    color: var(--loca-color-text);
  }
  h1 {
    font-size: 28px;
    font-weight: 600;
    margin: 0 0 4px;
  }
  .tag {
    color: var(--loca-color-text-muted);
    font-size: 13px;
    margin: 0 0 24px;
  }
  .card {
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-md);
    padding: 16px 20px;
  }
  h2 {
    font-size: 14px;
    font-weight: 600;
    margin: 0 0 10px;
    color: var(--loca-color-text);
  }
  ul {
    list-style: none;
    padding: 0;
    margin: 0 0 16px;
    font-size: 13px;
  }
  li { padding: 4px 0; }
  .ok { color: var(--loca-color-success); font-weight: 500; }
  .error { color: var(--loca-color-danger); font-weight: 500; }
  .checking { color: var(--loca-color-text-muted); }
  .hint {
    margin: 0;
    font-size: 12px;
    color: var(--loca-color-text-muted);
    line-height: 1.5;
  }
  a { color: var(--loca-color-accent); }
</style>
