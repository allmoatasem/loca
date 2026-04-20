<!--
  MemoryView — panel surfacing the memory store (facts injected into every
  chat turn). Mirrors the legacy `#mem-overlay` in src/static/index.html:
  manual add, extract-from-current-conversation, paginated list with
  per-item delete.
-->
<script lang="ts">
  import { tick } from 'svelte';
  import { app } from './app-store.svelte';

  interface Props {
    onClose?: () => void;
    /** Deep-link target from a `[memory: N]` citation click. `id:<uuid>`
     *  scrolls to that specific memory; `idx:<n>` scrolls to the Nth
     *  row in the most-recent list. `null` (default) just opens the
     *  panel at the top. */
    highlight?: { kind: 'id' | 'idx'; value: string } | null;
  }
  let { onClose, highlight = null }: Props = $props();

  interface MemoryItem {
    id: string;
    content: string;
    created: number;
  }

  const PAGE_SIZE = 50;

  let memories = $state<MemoryItem[]>([]);
  let total = $state(0);
  let offset = $state(0);
  let loading = $state(true);
  let manualInput = $state('');
  let extractLabel = $state('✦ Extract memories from current conversation');
  let extractBusy = $state(false);
  /** Memory id that just got highlighted — drives a ring flash. */
  let flashId = $state<string | null>(null);
  let itemEls = $state<Record<string, HTMLElement>>({});

  async function loadFirstPage(): Promise<void> {
    loading = true;
    try {
      const r = await fetch(`/api/memories?limit=${PAGE_SIZE}&offset=0`);
      const data = await r.json();
      const items: MemoryItem[] = data.memories ?? [];
      memories = items;
      total = data.total ?? items.length;
      offset = items.length;
    } catch {
      memories = [];
      total = 0;
      offset = 0;
    }
    loading = false;
  }

  async function loadMore(): Promise<void> {
    try {
      const r = await fetch(`/api/memories?limit=${PAGE_SIZE}&offset=${offset}`);
      const data = await r.json();
      const more: MemoryItem[] = data.memories ?? [];
      memories = [...memories, ...more];
      total = data.total ?? total;
      offset += more.length;
    } catch {
      // silent: load-more is retryable
    }
  }

  async function deleteMemory(id: string): Promise<void> {
    try {
      await fetch(`/api/memories/${encodeURIComponent(id)}`, { method: 'DELETE' });
      memories = memories.filter((m) => m.id !== id);
      total = Math.max(0, total - 1);
      offset = Math.max(0, offset - 1);
    } catch {
      // silent
    }
  }

  async function saveManual(): Promise<void> {
    const content = manualInput.trim();
    if (!content) return;
    try {
      await fetch('/api/memories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      });
      manualInput = '';
      await loadFirstPage();
    } catch {
      // silent
    }
  }

  async function triggerExtract(): Promise<void> {
    const convId = app.activeConvId;
    if (!convId || extractBusy) return;
    extractBusy = true;
    extractLabel = '⏳ Extracting…';
    try {
      const convResp = await fetch(`/api/conversations/${encodeURIComponent(convId)}`);
      const conv = await convResp.json();
      const messages = (conv?.messages ?? []).map((m: { role: string; content: string }) => ({
        role: m.role,
        content: m.content,
      }));
      if (!messages.length) {
        extractLabel = '✦ Nothing to extract yet';
        return;
      }
      const r = await fetch('/api/extract-memories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages, conv_id: convId }),
      });
      const data = await r.json();
      const count = (data.memories ?? []).length;
      extractLabel = `✦ Extracted ${count} fact${count === 1 ? '' : 's'}`;
      await loadFirstPage();
    } catch {
      extractLabel = '✦ Extraction failed';
    } finally {
      setTimeout(() => {
        extractLabel = '✦ Extract memories from current conversation';
        extractBusy = false;
      }, 3000);
    }
  }

  function formatDate(created: number): string {
    return new Date(created * 1000).toLocaleDateString();
  }

  // Eagerly load on mount.
  loadFirstPage();

  // Deep-link target from `[memory: N]` click. Resolves as soon as
  // the referenced row is in `memories` (may require load-more calls
  // when the citation points deep into a long history).
  async function focusHighlight(): Promise<void> {
    if (!highlight || loading) return;
    let targetId: string | null = null;
    if (highlight.kind === 'id') {
      targetId = highlight.value;
    } else {
      // idx is 1-based from the orchestrator's retrieval pool. The
      // memory list here is in descending-created order which won't
      // match the pool ordering, so idx-based fallback just picks
      // the Nth most-recent memory as an approximation.
      const n = Number(highlight.value);
      if (Number.isFinite(n) && n >= 1 && memories[n - 1]) {
        targetId = memories[n - 1].id;
      }
    }
    if (!targetId) return;
    // Page in more rows until the id appears or we run out.
    while (!memories.some((m) => m.id === targetId) && offset < total) {
      await loadMore();
    }
    if (!memories.some((m) => m.id === targetId)) return;
    await tick();
    const el = itemEls[targetId];
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      flashId = targetId;
      setTimeout(() => { if (flashId === targetId) flashId = null; }, 1800);
    }
  }

  $effect(() => {
    if (!loading && highlight) void focusHighlight();
  });

  const remaining = $derived(Math.max(0, total - offset));
  const pageLoadSize = $derived(Math.min(PAGE_SIZE, remaining));
  const canExtract = $derived(!!app.activeConvId && !extractBusy);
</script>

<section class="panel" role="dialog" aria-label="Memory">
  <header>
    <h2>
      Memory
      <span class="count">
        {#if loading}
          loading…
        {:else}
          {total} fact{total === 1 ? '' : 's'}
        {/if}
      </span>
    </h2>
    {#if onClose}
      <button class="close" aria-label="Close" onclick={onClose}>×</button>
    {/if}
  </header>

  <div class="divider"></div>

  <div class="body">
    <div class="manual-add">
      <input
        type="text"
        placeholder="Add a memory manually…"
        bind:value={manualInput}
        onkeydown={(e) => { if (e.key === 'Enter') saveManual(); }}
      />
      <button onclick={saveManual} disabled={!manualInput.trim()}>Save</button>
    </div>

    <button
      class="extract"
      onclick={triggerExtract}
      disabled={!canExtract}
      title={canExtract ? '' : 'Open a conversation first'}
    >
      {extractLabel}
    </button>

    <div class="list">
      {#if loading}
        <p class="empty">Loading…</p>
      {:else if memories.length === 0}
        <div class="empty">
          <p>No memories yet.</p>
          <p>Click <strong>Extract memories from current conversation</strong> above,
          or start a new conversation — extraction runs automatically when you switch.</p>
          <p>You can also add facts manually using the field above.</p>
        </div>
      {:else}
        {#each memories as m (m.id)}
          <article
            class="item"
            class:flash={flashId === m.id}
            bind:this={itemEls[m.id]}
          >
            <div class="item-body">
              <p class="content">{m.content}</p>
              <p class="date">{formatDate(m.created)}</p>
            </div>
            <button class="del" aria-label="Delete" onclick={() => deleteMemory(m.id)}>×</button>
          </article>
        {/each}
        {#if remaining > 0}
          <button class="load-more" onclick={loadMore}>
            Load {pageLoadSize} more ({offset} of {total} shown)
          </button>
        {/if}
      {/if}
    </div>
  </div>
</section>

<style>
  .panel {
    width: 520px;
    max-height: 640px;
    display: flex;
    flex-direction: column;
    background: var(--loca-color-bg);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-lg);
    overflow: hidden;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.15);
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px;
  }
  h2 {
    font-size: 14px;
    font-weight: 600;
    margin: 0;
    color: var(--loca-color-text);
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }
  .count {
    font-size: 11px;
    font-weight: 500;
    color: var(--loca-color-text-muted);
  }
  .close {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    border: none;
    background: rgba(128, 128, 128, 0.1);
    color: var(--loca-color-text-muted);
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
  }
  .close:hover { background: rgba(128, 128, 128, 0.2); }

  .divider {
    height: 1px;
    background: var(--loca-color-border);
  }

  .body {
    padding: 14px 20px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .manual-add {
    display: flex;
    gap: 6px;
  }
  .manual-add input {
    flex: 1;
    padding: 6px 10px;
    font-size: 12px;
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    background: var(--loca-color-surface);
    color: var(--loca-color-text);
  }
  .manual-add button,
  .extract {
    padding: 6px 12px;
    font-size: 12px;
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    background: var(--loca-color-surface);
    color: var(--loca-color-text);
    cursor: pointer;
  }
  .manual-add button:hover:not(:disabled),
  .extract:hover:not(:disabled) {
    background: color-mix(in srgb, var(--loca-color-accent) 12%, var(--loca-color-surface));
  }
  .manual-add button:disabled,
  .extract:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .extract {
    align-self: stretch;
    text-align: left;
  }

  .list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .item {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 8px 10px;
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    background: var(--loca-color-surface);
    transition: background 0.3s ease, border-color 0.3s ease,
                box-shadow 0.3s ease;
  }
  .item.flash {
    background: color-mix(in srgb, var(--loca-color-accent) 12%, var(--loca-color-surface));
    border-color: var(--loca-color-accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--loca-color-accent) 25%, transparent);
  }
  .item-body { flex: 1; }
  .content {
    margin: 0 0 4px;
    font-size: 13px;
    color: var(--loca-color-text);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .date {
    margin: 0;
    font-size: 10px;
    color: var(--loca-color-text-muted);
  }
  .del {
    width: 22px;
    height: 22px;
    border: none;
    background: none;
    color: var(--loca-color-text-muted);
    cursor: pointer;
    font-size: 16px;
    line-height: 1;
    border-radius: 50%;
  }
  .del:hover {
    color: var(--loca-color-text);
    background: rgba(128, 128, 128, 0.12);
  }

  .empty {
    margin: 10px 0;
    padding: 12px;
    font-size: 12px;
    color: var(--loca-color-text-muted);
    text-align: center;
  }
  .empty p { margin: 6px 0; }

  .load-more {
    margin-top: 8px;
    align-self: center;
    padding: 6px 14px;
    font-size: 11px;
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    background: var(--loca-color-surface);
    color: var(--loca-color-text);
    cursor: pointer;
  }
  .load-more:hover {
    background: color-mix(in srgb, var(--loca-color-accent) 12%, var(--loca-color-surface));
  }
</style>
