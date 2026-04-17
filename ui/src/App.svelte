<!--
  Svelte UI shell. Layout is a two-pane SwiftUI-style split:
    - Left: SidebarView (Phase 2 scope — controls + conv list + footer)
    - Right: route-dependent content. Phase 4 will bring the chat view;
             meanwhile the right pane either shows an overlay panel
             (Glossary for now) or a "Chat view lands in Phase 4"
             placeholder.
-->
<script lang="ts">
  import SidebarView from './lib/SidebarView.svelte';
  import GlossaryView from './lib/GlossaryView.svelte';
  import PreferencesView from './lib/PreferencesView.svelte';

  let path = $state(location.pathname);
  window.addEventListener('popstate', () => { path = location.pathname; });

  function navigate(to: string): void {
    history.pushState(null, '', to);
    path = to;
  }

  const openOverlay = $derived.by<null | 'glossary' | 'preferences'>(() => {
    if (path.endsWith('/glossary')) return 'glossary';
    if (path.endsWith('/preferences')) return 'preferences';
    return null;
  });

  function onOverlayKeydown(e: KeyboardEvent): void {
    if (e.key === 'Escape') navigate('/ui');
  }
</script>

<div class="shell">
  <SidebarView onOpenRoute={navigate} />

  <main>
    <div class="placeholder">
      <h1>Chat view</h1>
      <p class="tag">Ported in Phase 4 of the Svelte migration.</p>
      <p class="hint">
        Meanwhile, use the macOS app or the legacy browser UI at
        <a href="/">/</a>.
      </p>
    </div>
  </main>

  {#if openOverlay}
    <div
      class="overlay"
      role="presentation"
      onclick={(e) => { if (e.currentTarget === e.target) navigate('/ui'); }}
      onkeydown={onOverlayKeydown}
    >
      {#if openOverlay === 'glossary'}
        <GlossaryView onClose={() => navigate('/ui')} />
      {:else if openOverlay === 'preferences'}
        <PreferencesView onClose={() => navigate('/ui')} />
      {/if}
    </div>
  {/if}
</div>

<style>
  .shell {
    display: grid;
    grid-template-columns: 260px 1fr;
    height: 100vh;
    background: var(--loca-color-bg);
  }

  main {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 40px;
  }
  .placeholder {
    max-width: 420px;
    text-align: center;
    color: var(--loca-color-text);
  }
  h1 {
    font-size: 20px;
    font-weight: 600;
    margin: 0 0 6px;
  }
  .tag {
    color: var(--loca-color-text-muted);
    font-size: 13px;
    margin: 0 0 14px;
  }
  .hint {
    color: var(--loca-color-text-muted);
    font-size: 12px;
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
