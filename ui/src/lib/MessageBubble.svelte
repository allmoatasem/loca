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
    /** Live query from the chat-search bar — matches inside the bubble
     *  get a yellow background so the user can spot them while typing. */
    highlight?: string;
  }
  let {
    role, content, isStreaming = false, imageUrls = [], citations = [],
    highlight = '',
  }: Props = $props();

  const split = $derived(role === 'assistant' ? splitThinkBlocks(content) : null);
  const answerHtml = $derived.by(() => {
    if (!split) return '';
    const rendered = renderMarkdown(linkMemoryCitations(stripToolCallJson(split.answer)));
    return applyHighlight(rendered, highlight);
  });

  /** Wrap case-insensitive substring matches in a `<mark>` so the
   *  chat-search bar can spotlight results without rerendering the
   *  whole markdown pipeline. Skips anchors / tag contents by only
   *  touching text nodes. */
  function applyHighlight(html: string, needle: string): string {
    const q = needle.trim();
    if (!q) return html;
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    const escapeRe = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp(escapeRe, 'gi');
    const walk = (node: Node): void => {
      if (node.nodeType === 3) { // text
        const t = node as Text;
        if (!re.test(t.data)) return;
        const frag = document.createDocumentFragment();
        let last = 0;
        re.lastIndex = 0;
        let m: RegExpExecArray | null;
        while ((m = re.exec(t.data)) !== null) {
          if (m.index > last) frag.appendChild(document.createTextNode(t.data.slice(last, m.index)));
          const mark = document.createElement('mark');
          mark.textContent = m[0];
          frag.appendChild(mark);
          last = m.index + m[0].length;
        }
        if (last < t.data.length) frag.appendChild(document.createTextNode(t.data.slice(last)));
        t.parentNode?.replaceChild(frag, t);
        return;
      }
      if (node.nodeType === 1) {
        const el = node as Element;
        const tag = el.tagName.toLowerCase();
        if (tag === 'script' || tag === 'style' || tag === 'a' || tag === 'mark') return;
        // Iterate over a snapshot since walk() mutates siblings.
        for (const child of Array.from(el.childNodes)) walk(child);
      }
    };
    for (const child of Array.from(tpl.content.childNodes)) walk(child);
    const out = document.createElement('div');
    out.appendChild(tpl.content);
    return out.innerHTML;
  }

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
    const popoverH = 260;          // conservative; actual content is smaller
    const popoverW = 360;
    const margin = 12;
    // Prefer above the pill; fall back below when there's no room.
    let top: number;
    if (r.top - popoverH - margin > margin) {
      top = r.top - popoverH - margin + window.scrollY;
    } else {
      top = r.bottom + margin + window.scrollY;
    }
    // Clamp horizontally so the popover stays on screen.
    const maxLeft = window.innerWidth - popoverW - margin;
    const left = Math.max(margin, Math.min(r.left + window.scrollX, maxLeft));
    return { top, left, cit };
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
    <button class="copy user-copy" onclick={copyToClipboard} aria-label="Copy message" title={copied ? 'Copied' : 'Copy message'}>
      {#if copied}
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="3 8 7 12 13 4" />
        </svg>
      {:else}
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true">
          <rect x="4" y="4" width="9" height="10" rx="1.5" />
          <path d="M10 2H3.5A1.5 1.5 0 0 0 2 3.5V11" />
        </svg>
      {/if}
    </button>
  {:else if isStreaming && content === ''}
    <span class="dots"><span></span><span></span><span></span></span>
  {:else if split}
    {#if split.thinking}
      <ThinkBlock text={split.thinking} defaultOpen={split.answer === ''} />
    {/if}
    <!-- eslint-disable-next-line svelte/no-at-html-tags -->
    <div class="md">{@html answerHtml}</div>
    {#if !isStreaming && citations.length > 0}
      <!-- Footer chip: makes sources visible even when the model
           forgot to cite inline with `[memory: N]`. Clicking expands
           the full list; individual rows open in Memory / URL just
           like the inline popover. -->
      <details class="sources-chip">
        <summary>📓 {citations.length} source{citations.length === 1 ? '' : 's'} used</summary>
        <ul class="sources-list">
          {#each citations as c (c.idx)}
            <li>
              <header>
                <span class="cit-kind">{kindLabel(c.kind)}</span>
                <span class="cit-idx">[memory: {c.idx}]</span>
              </header>
              {#if c.title}<p class="cit-title">{c.title}</p>{/if}
              {#if c.snippet}<p class="cit-snippet">{c.snippet}</p>{/if}
              <div class="cit-actions">
                {#if c.kind === 'memory' && c.memory_id}
                  <button class="primary" onclick={() => openInMemoryPanel(c.memory_id!)}>Open in Memory</button>
                {/if}
                {#if c.url}
                  <button class="primary" onclick={() => openUrl(c.url!)}>Open link ↗</button>
                {/if}
              </div>
            </li>
          {/each}
        </ul>
      </details>
    {/if}
    {#if !isStreaming && split.answer.trim()}
      <button class="copy" onclick={copyToClipboard} aria-label="Copy reply" title={copied ? 'Copied' : 'Copy reply'}>
        {#if copied}
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="3 8 7 12 13 4" />
          </svg>
        {:else}
          <!-- Document-on-document glyph matching SwiftUI's `doc.on.doc`. -->
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true">
            <rect x="4" y="4" width="9" height="10" rx="1.5" />
            <path d="M10 2H3.5A1.5 1.5 0 0 0 2 3.5V11" />
          </svg>
        {/if}
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
    /* Responsive — stretch up to nearly the scroller's width but
       leave a gutter on the opposite side so user/assistant bubbles
       stay visually distinct. Mirrors Swift's `Spacer(minLength: 80)`. */
    max-width: calc(100% - 80px);
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
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: none;
    border: 1px solid var(--loca-color-border);
    color: var(--loca-color-text-muted);
    border-radius: var(--loca-radius-sm);
    padding: 3px 6px;
    width: 24px;
    height: 22px;
    cursor: pointer;
  }
  .copy:hover { color: var(--loca-color-text); background: rgba(127, 127, 127, 0.08); }
  /* User messages align to the right of the chat column so their
     copy affordance lives on the right edge of the bubble. */
  .copy.user-copy { align-self: flex-end; }

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
  .md :global(mark) {
    background: #fff1a8;
    color: inherit;
    padding: 0 1px;
    border-radius: 2px;
  }
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

  /* Sources-used expandable footer */
  .sources-chip {
    margin-top: 8px;
    font-size: 11px;
    color: var(--loca-color-text-muted);
  }
  .sources-chip summary {
    cursor: pointer;
    padding: 3px 8px;
    background: color-mix(in srgb, var(--loca-color-accent) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--loca-color-accent) 20%, transparent);
    border-radius: 999px;
    width: fit-content;
    list-style: none;
    user-select: none;
  }
  .sources-chip summary::-webkit-details-marker { display: none; }
  .sources-chip summary:hover {
    background: color-mix(in srgb, var(--loca-color-accent) 14%, transparent);
  }
  .sources-list {
    list-style: none;
    padding: 10px 0 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .sources-list li {
    padding: 8px 10px;
    background: var(--loca-color-bg);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
  }
  .sources-list header {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 4px;
  }
  .sources-list .cit-title {
    margin: 0 0 3px;
    font-size: 12px;
    font-weight: 600;
    color: var(--loca-color-text);
  }
  .sources-list .cit-snippet {
    margin: 0;
    font-size: 11px;
    line-height: 1.45;
    color: var(--loca-color-text-muted);
    max-height: 4.5em;
    overflow: hidden;
    white-space: pre-wrap;
  }
  .sources-list .cit-actions {
    display: flex;
    gap: 6px;
    margin-top: 6px;
  }
  .sources-list .primary {
    background: color-mix(in srgb, var(--loca-color-accent) 14%, transparent);
    color: var(--loca-color-accent);
    border: 1px solid color-mix(in srgb, var(--loca-color-accent) 32%, transparent);
    border-radius: var(--loca-radius-sm);
    padding: 3px 8px;
    font-size: 10px;
    cursor: pointer;
  }
  .sources-list .primary:hover {
    background: color-mix(in srgb, var(--loca-color-accent) 22%, transparent);
  }

  /* Citation preview popover — `top`/`left` are precomputed in JS
     (see `anchorPopover`) so the popover is always fully on-screen.
     No transform here: keeps the positioning logic in one place. */
  .citation-pop {
    position: absolute;
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
