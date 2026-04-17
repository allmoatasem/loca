<!--
  Phase 0 + 1 shell.

  Routing: pathname-driven. `/ui` or `/ui/` shows the scaffolding page.
  `/ui/glossary` shows the Glossary panel. More routes land phase by phase.

  Real panels are ported one at a time into ui/src/lib/*View.svelte, mirroring
  Loca-SwiftUI/Sources/Loca/Views/*View.swift one-for-one.
-->
<script lang="ts">
  import GlossaryView from './lib/GlossaryView.svelte';

  let path = $state(location.pathname);

  // Back/forward support in case we add more routes later.
  window.addEventListener('popstate', () => { path = location.pathname; });

  function navigate(to: string) {
    history.pushState(null, '', to);
    path = to;
  }

  let backendHealth = $state<'checking' | 'ok' | 'error'>('checking');
  (async () => {
    try {
      const r = await fetch('/health');
      backendHealth = r.ok ? 'ok' : 'error';
    } catch {
      backendHealth = 'error';
    }
  })();

  const isGlossary = $derived(path.endsWith('/glossary'));
</script>

{#if isGlossary}
  <div class="overlay">
    <GlossaryView onClose={() => navigate('/ui')} />
  </div>
{:else}
  <main>
    <h1>Loca</h1>
    <p class="tag">Second UI — Svelte (Phase 1: Glossary ported)</p>

    <section class="card">
      <h2>Status</h2>
      <ul>
        <li>Build pipeline: <span class="ok">active</span></li>
        <li>Backend health: <span class={backendHealth}>{backendHealth}</span></li>
      </ul>

      <div class="actions">
        <button onclick={() => navigate('/ui/glossary')}>Open Glossary</button>
      </div>

      <p class="hint">
        The real UI lands incrementally in phases 2–5. Meanwhile, use the macOS
        app or the legacy browser UI at <a href="/">/</a>.
      </p>
    </section>
  </main>
{/if}

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
  }
  ul { list-style: none; padding: 0; margin: 0 0 16px; font-size: 13px; }
  li { padding: 4px 0; }
  .ok { color: var(--loca-color-success); font-weight: 500; }
  .error { color: var(--loca-color-danger); font-weight: 500; }
  .checking { color: var(--loca-color-text-muted); }
  .actions { margin: 0 0 12px; }
  .actions button {
    background: var(--loca-color-accent);
    color: #fff;
    border: none;
    border-radius: var(--loca-radius-sm);
    padding: 6px 14px;
    font-size: 12px;
    cursor: pointer;
  }
  .actions button:hover { background: var(--loca-color-accent-hover); }
  .hint {
    margin: 0;
    font-size: 12px;
    color: var(--loca-color-text-muted);
    line-height: 1.5;
  }
  a { color: var(--loca-color-accent); }

  .overlay {
    position: fixed;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 40px 20px;
    background: rgba(0, 0, 0, 0.35);
  }
</style>
