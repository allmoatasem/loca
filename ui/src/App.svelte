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
  import ManageModelsView from './lib/ManageModelsView.svelte';
  import VaultView from './lib/VaultView.svelte';
  import MemoryView from './lib/MemoryView.svelte';
  import PhilosophyView from './lib/PhilosophyView.svelte';
  import AcknowledgementsView from './lib/AcknowledgementsView.svelte';
  import ResearchWorkspaceView from './lib/ResearchWorkspaceView.svelte';
  import ChatView from './lib/ChatView.svelte';
  import VoiceSetupView from './lib/VoiceSetupView.svelte';
  import { app } from './lib/app-store.svelte';

  let path = $state(location.pathname);
  window.addEventListener('popstate', () => { path = location.pathname; });

  function navigate(to: string): void {
    history.pushState(null, '', to);
    path = to;
  }

  // Per-citation deep-link target. `id:<uuid>` when the orchestrator
  // shipped per-turn provenance; `idx:<N>` as a fallback. MemoryView
  // reads this and scrolls/highlights the matching row.
  let memoryHighlight = $state<{ kind: 'id' | 'idx'; value: string } | null>(null);

  // Global click interceptor for `[memory: N]` citations rendered by
  // MessageBubble. The marker is encoded as `#loca-memory-<target>` so
  // DOMPurify keeps the anchor; here we catch the click before the
  // browser scrolls to a nonexistent fragment and route to the Memory
  // panel, passing the target so the view can highlight the row.
  document.addEventListener('click', (e) => {
    const target = (e.target as HTMLElement)?.closest('a');
    if (!target) return;
    const href = target.getAttribute('href') ?? '';
    if (!href.startsWith('#loca-memory-')) return;
    e.preventDefault();
    const payload = href.slice('#loca-memory-'.length);
    if (payload.startsWith('id:')) {
      memoryHighlight = { kind: 'id', value: decodeURIComponent(payload.slice(3)) };
    } else if (payload.startsWith('idx:')) {
      memoryHighlight = { kind: 'idx', value: payload.slice(4) };
    } else {
      // Legacy numeric fallback — treat as an index.
      memoryHighlight = { kind: 'idx', value: payload };
    }
    navigate('/ui/memory');
  });

  type OverlayKind = 'glossary' | 'preferences' | 'manage-models' | 'vault' | 'memory' | 'philosophy' | 'acknowledgements' | 'research';
  const openOverlay = $derived.by<null | OverlayKind>(() => {
    if (path.endsWith('/glossary'))         return 'glossary';
    if (path.endsWith('/preferences'))      return 'preferences';
    if (path.endsWith('/manage-models'))    return 'manage-models';
    if (path.endsWith('/vault'))            return 'vault';
    if (path.endsWith('/memory'))           return 'memory';
    if (path.endsWith('/philosophy'))       return 'philosophy';
    if (path.endsWith('/acknowledgements')) return 'acknowledgements';
    if (path.endsWith('/research'))         return 'research';
    return null;
  });

  function onOverlayKeydown(e: KeyboardEvent): void {
    if (e.key === 'Escape') navigate('/ui');
  }
</script>

<div class="shell">
  <SidebarView onOpenRoute={navigate} />

  <main>
    <ChatView />
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
      {:else if openOverlay === 'manage-models'}
        <ManageModelsView onClose={() => navigate('/ui')} />
      {:else if openOverlay === 'vault'}
        <VaultView onClose={() => navigate('/ui')} />
      {:else if openOverlay === 'memory'}
        <MemoryView
          onClose={() => { memoryHighlight = null; navigate('/ui'); }}
          highlight={memoryHighlight}
        />
      {:else if openOverlay === 'philosophy'}
        <PhilosophyView onClose={() => navigate('/ui')} />
      {:else if openOverlay === 'acknowledgements'}
        <AcknowledgementsView onClose={() => navigate('/ui')} />
      {:else if openOverlay === 'research'}
        <ResearchWorkspaceView onClose={() => navigate('/ui')} />
      {/if}
    </div>
  {/if}

  {#if app.showVoiceSetup}
    <VoiceSetupView onClose={() => app.setShowVoiceSetup(false)} />
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
    overflow: hidden;
    /* Visible edge between sidebar and chat — matches SwiftUI's split. */
    border-left: 1px solid var(--loca-color-border);
  }

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
