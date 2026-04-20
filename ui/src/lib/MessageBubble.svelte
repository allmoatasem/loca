<!--
  MessageBubble — single chat turn. Mirrors SwiftUI's ChatMessageRow /
  bubbleContent split between user / assistant / typing-indicator.
-->
<script lang="ts">
  import { linkMemoryCitations, renderMarkdown, splitThinkBlocks, stripToolCallJson } from './markdown';
  import ThinkBlock from './ThinkBlock.svelte';
  import { app } from './app-store.svelte';

  export type Role = 'user' | 'assistant';

  /** Structured citation shipped by the proxy's usage payload. Covers
   *  memory, project_item, obsidian note, and web hits — so `[memory: N]`
   *  can show the actual cited content regardless of source type. */
  export interface Citation {
    idx: number;
    kind: 'memory' | 'project_item' | 'obsidian' | 'vault' | 'web' | string;
    title: string;
    snippet: string;
    url?: string | null;
    memory_id?: string | null;
  }

  interface Props {
    role: Role;
    content: string;
    isStreaming?: boolean;
    imageUrls?: string[];
    /** Per-turn structured citations from the proxy's usage payload. */
    citations?: Citation[];
  }
  let {
    role, content, isStreaming = false, imageUrls = [], citations = [],
  }: Props = $props();

  const split = $derived(role === 'assistant' ? splitThinkBlocks(content) : null);
  const answerHtml = $derived.by(() => {
    if (!split) return '';
    return renderMarkdown(linkMemoryCitations(stripToolCallJson(split.answer)));
  });

  let copied = $state(false);
  async function copyToClipboard(): Promise<void> {
    const text = split ? split.answer : content;
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
      setTimeout(() => (copied = false), 1200);
    } catch { /* silent */ }
  }

  // Citation popover — opened on click, positioned above the clicked
  // pill. Anchored to page coordinates so it survives bubble reflow.
  let popover = $state<{ top: number; left: number; cit: Citation } | null>(null);

  function onBubbleClick(e: MouseEvent): void {
    const anchor = (e.target as HTMLElement)?.closest('a');
    if (!anchor) return;
    const href = anchor.getAttribute('href') ?? '';
    if (!href.startsWith('#loca-citation-')) return;
    e.preventDefault();
    const n = Number(href.slice('#loca-citation-'.length));
    if (!Number.isFinite(n)) return;
    const cit = citations.find((c) => c.idx === n);
    if (!cit) {
      // No structured citation available — probably a legacy turn or a
      // phantom index. Show a placeholder so the click still produces
      // feedback instead of silently doing nothing.
      popover = anchorPopover(anchor, {
        idx: n, kind: 'missing',
        title: `Citation [memory: ${n}]`,
        snippet: 'This turn did not ship source metadata for this citation. The model may have hallucinated the index (phantom citation) or the server is on an older version.',
      });
      return;
    }
    popover = anchorPopover(anchor, cit);
  }

  function anchorPopover(el: HTMLElement, cit: Citation): { top: number; left: number; cit: Citation } {
    const r = el.getBoundingClientRect();
    return {
      top: window.scrollY + r.top,
      left: window.scrollX + r.left,
      cit,
    };
  }

  function closePopover(): void { popover = null; }

  function openInMemoryPanel(memoryId: string): void {
    // Flip the nav flag — App.svelte's click handler is bypassed by
    // `onBubbleClick`'s preventDefault, so do it directly here.
    app.memoryHighlightId = memoryId;
    history.pushState(null, '', '/ui/memory');
    dispatchEvent(new PopStateEvent('popstate'));
    popover = null;
  }

  function openUrl(url: string): void {
    window.open(url, '_blank', 'noopener,noreferrer');
    popover = null;
  }

  function kindLabel(k: string): string {
    switch (k) {
      case 'memory':       return 'Memory';
      case 'project_item': return 'Project source';
      case 'obsidian':
      case 'vault':        return 'Vault note';
      case 'web':          return 'Web';
      case 'missing':      return 'Missing';
      default:             return k;
    }
  }
</script>

<svelte:window onclick={(e) => {
  // Close the popover on any click that isn't inside it.
  if (!popover) return;
  const inside = (e.target as HTMLElement)?.closest('.citation-pop, a[href^="#loca-citation-"]');
  if (!inside) popover = null;
}} />

<article
  class="bubble"
  class:user={role === 'user'}
  class:assistant={role === 'assistant'}
  onclick={onBubbleClick}
  role="presentation"
>
  {#if role === 'user'}
    {#if imageUrls.length > 0}
      <div class="attach-images">
        {#each imageUrls as url}
          <img src={url} alt="attachment" />
        {/each}
      </div>
    {/if}
    <p class="text">{content}</p>
  {:else if isStreaming && content === ''}
    <span class="dots"><span></span><span></span><span></span></span>
  {:else if split}
    {#if split.thinking}
      <ThinkBlock text={split.thinking} defaultOpen={split.answer === ''} />
    {/if}
    <!-- eslint-disable-next-line svelte/no-at-html-tags -->
    <div class="md">{@html answerHtml}</div>
    {#if !isStreaming && split.answer.trim()}
      <button class="copy" onclick={copyToClipboard} aria-label="Copy reply">
        {copied ? '✓ Copied' : 'Copy'}
      </button>
    {/if}
  {/if}
</article>

{#if popover}
  <div
    class="citation-pop"
    style:top="{popover.top - 8}px"
    style:left="{popover.left}px"
    role="dialog"
    aria-label="Citation preview"
  >
    <header>
      <span class="cit-kind">{kindLabel(popover.cit.kind)}</span>
      <span class="cit-idx">[memory: {popover.cit.idx}]</span>
      <button class="close" onclick={closePopover} aria-label="Close">×</button>
    </header>
    {#if popover.cit.title}
      <p class="cit-title">{popover.cit.title}</p>
    {/if}
    {#if popover.cit.snippet}
      <p class="cit-snippet">{popover.cit.snippet}</p>
    {/if}
    <footer>
      {#if popover.cit.kind === 'memory' && popover.cit.memory_id}
        <button class="primary" onclick={() => openInMemoryPanel(popover!.cit.memory_id!)}>
          Open in Memory
        </button>
      {/if}
      {#if popover.cit.url}
        <button class="primary" onclick={() => openUrl(popover!.cit.url!)}>
          Open link ↗
        </button>
      {/if}
    </footer>
  </div>
{/if}

<style>
  .bubble {
    max-width: 720px;
    padding: 10px 14px;
    border-radius: var(--loca-radius-md);
    font-size: 14px;
    line-height: 1.55;
    color: var(--loca-color-text);
  }
  .bubble.user {
    align-self: flex-end;
    background: color-mix(in srgb, var(--loca-color-accent) 18%, transparent);
    white-space: pre-wrap;
  }
  .bubble.assistant {
    align-self: flex-start;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
  }
  .text { margin: 0; }

  .attach-images { display: flex; gap: 6px; margin: 0 0 8px; flex-wrap: wrap; }
  .attach-images img {
    max-width: 200px; max-height: 200px;
    border-radius: var(--loca-radius-sm);
    object-fit: cover;
  }

  .copy {
    align-self: flex-start;
    margin-top: 6px;
    background: none;
    border: 1px solid var(--loca-color-border);
    color: var(--loca-color-text-muted);
    border-radius: var(--loca-radius-sm);
    padding: 2px 8px;
    font-size: 11px;
    cursor: pointer;
  }
  .copy:hover { color: var(--loca-color-text); background: rgba(127, 127, 127, 0.08); }

  .dots { display: inline-flex; gap: 4px; padding: 4px 0; }
  .dots span {
    width: 6px; height: 6px;
    background: var(--loca-color-text-muted);
    border-radius: 50%;
    animation: dot 1.2s ease-in-out infinite;
  }
  .dots span:nth-child(2) { animation-delay: 0.15s; }
  .dots span:nth-child(3) { animation-delay: 0.30s; }
  @keyframes dot {
    0%, 60%, 100% { transform: translateY(0); opacity: 0.35; }
    30%           { transform: translateY(-4px); opacity: 1; }
  }

  .md :global(p) { margin: 0 0 10px; }
  .md :global(p:last-child) { margin-bottom: 0; }
  .md :global(pre) {
    background: rgba(127, 127, 127, 0.1);
    padding: 10px 12px;
    border-radius: var(--loca-radius-sm);
    overflow-x: auto;
    font-family: var(--loca-font-mono);
    font-size: 12px;
    margin: 0 0 10px;
  }
  .md :global(code) {
    font-family: var(--loca-font-mono);
    font-size: 0.9em;
    background: rgba(127, 127, 127, 0.12);
    padding: 1px 4px;
    border-radius: 3px;
  }
  .md :global(pre code) { background: transparent; padding: 0; }
  .md :global(h1), .md :global(h2), .md :global(h3) {
    margin: 12px 0 6px;
    font-weight: 600;
  }
  .md :global(h1) { font-size: 18px; }
  .md :global(h2) { font-size: 15px; }
  .md :global(h3) { font-size: 14px; }
  .md :global(ul), .md :global(ol) { margin: 0 0 10px; padding-left: 22px; }
  .md :global(blockquote) {
    border-left: 3px solid var(--loca-color-border);
    margin: 0 0 10px;
    padding: 2px 10px;
    color: var(--loca-color-text-muted);
  }
  .md :global(a) {
    color: var(--loca-color-accent);
    cursor: pointer;
    text-decoration: underline;
    text-decoration-thickness: 1px;
    text-underline-offset: 2px;
  }
  .md :global(a:hover) { text-decoration-thickness: 2px; }
  .md :global(a[href^="#loca-citation-"]) {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 0 6px;
    margin: 0 1px;
    font-family: var(--loca-font-mono);
    font-size: 0.85em;
    color: color-mix(in srgb, var(--loca-color-accent) 85%, var(--loca-color-text));
    background: color-mix(in srgb, var(--loca-color-accent) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--loca-color-accent) 28%, transparent);
    border-radius: 999px;
    text-decoration: none;
    cursor: pointer;
  }
  .md :global(a[href^="#loca-citation-"]:hover) {
    background: color-mix(in srgb, var(--loca-color-accent) 18%, transparent);
    text-decoration: none;
  }

  .md :global(table) {
    border-collapse: collapse;
    margin: 0 0 10px;
    font-size: 13px;
  }
  .md :global(th), .md :global(td) {
    border: 1px solid var(--loca-color-border);
    padding: 4px 8px;
    text-align: left;
  }
  .md :global(th) {
    background: color-mix(in srgb, var(--loca-color-accent) 8%, transparent);
    font-weight: 600;
  }
  .md :global(ul ul), .md :global(ul ol),
  .md :global(ol ul), .md :global(ol ol) { margin: 4px 0; }
  .md :global(.katex-display) { margin: 8px 0; overflow-x: auto; overflow-y: hidden; }
  .md :global(.katex) { font-size: 1em; }

  /* Citation preview popover */
  .citation-pop {
    position: absolute;
    transform: translateY(-100%);
    width: 360px;
    max-width: calc(100vw - 40px);
    background: var(--loca-color-bg);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-md);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.22);
    padding: 10px 12px;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .citation-pop header {
    display: flex; align-items: center; gap: 8px;
  }
  .cit-kind {
    font-size: 10px; font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--loca-color-accent);
    background: color-mix(in srgb, var(--loca-color-accent) 12%, transparent);
    padding: 2px 6px;
    border-radius: 3px;
  }
  .cit-idx {
    font-family: var(--loca-font-mono);
    font-size: 11px;
    color: var(--loca-color-text-muted);
  }
  .citation-pop .close {
    margin-left: auto;
    background: none;
    border: none;
    color: var(--loca-color-text-muted);
    font-size: 16px;
    cursor: pointer;
    padding: 0 4px;
  }
  .citation-pop .close:hover { color: var(--loca-color-text); }
  .cit-title {
    margin: 0;
    font-size: 12px;
    font-weight: 600;
    color: var(--loca-color-text);
  }
  .cit-snippet {
    margin: 0;
    font-size: 12px;
    line-height: 1.45;
    color: var(--loca-color-text-muted);
    max-height: 180px;
    overflow-y: auto;
    white-space: pre-wrap;
  }
  .citation-pop footer {
    display: flex;
    gap: 6px;
    justify-content: flex-end;
  }
  .citation-pop .primary {
    background: color-mix(in srgb, var(--loca-color-accent) 14%, transparent);
    color: var(--loca-color-accent);
    border: 1px solid color-mix(in srgb, var(--loca-color-accent) 35%, transparent);
    border-radius: var(--loca-radius-sm);
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 500;
    cursor: pointer;
  }
  .citation-pop .primary:hover {
    background: color-mix(in srgb, var(--loca-color-accent) 22%, transparent);
  }
</style>
