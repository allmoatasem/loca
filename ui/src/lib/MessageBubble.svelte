<!--
  MessageBubble — single chat turn. Mirrors SwiftUI's ChatMessageRow /
  bubbleContent split between user / assistant / typing-indicator.
-->
<script lang="ts">
  import { linkMemoryCitations, renderMarkdown, splitThinkBlocks, stripToolCallJson } from './markdown';
  import ThinkBlock from './ThinkBlock.svelte';

  export type Role = 'user' | 'assistant';
  interface Props {
    role: Role;
    content: string;
    isStreaming?: boolean;
    imageUrls?: string[];       // rendered above the bubble for user messages
  }
  let { role, content, isStreaming = false, imageUrls = [] }: Props = $props();

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
    } catch {
      // Older browsers / insecure contexts — silent fail is fine.
    }
  }
</script>

<article class="bubble" class:user={role === 'user'} class:assistant={role === 'assistant'}>
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

  .attach-images {
    display: flex;
    gap: 6px;
    margin: 0 0 8px;
    flex-wrap: wrap;
  }
  .attach-images img {
    max-width: 200px;
    max-height: 200px;
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

  .dots {
    display: inline-flex;
    gap: 4px;
    padding: 4px 0;
  }
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
  /* Memory citation pill — same visual language as the adapter chip
     in the sidebar, distinct from external markdown links. */
  .md :global(a[href^="#loca-memory-"]) {
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
  }
  .md :global(a[href^="#loca-memory-"]:hover) {
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
  .md :global(ol ul), .md :global(ol ol) {
    margin: 4px 0;
  }

  .md :global(.katex-display) {
    margin: 8px 0;
    overflow-x: auto;
    overflow-y: hidden;
  }
  .md :global(.katex) {
    font-size: 1em;
  }
</style>
