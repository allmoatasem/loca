<!--
  GlossaryView — visual twin of Loca-SwiftUI/Sources/Loca/Views/GlossaryView.swift.

  Layout (from Swift, to be preserved on every edit):
    - 520pt wide, max 560pt tall panel
    - Header row: "Glossary" (14 semibold) left, circular 24×24 close button right
    - Divider
    - Scrollable body with 20pt padding, each entry:
        term (13 semibold) + definition (12 secondary), 10pt vertical padding
        Divider between entries (not after the last)

  Entries are sourced from glossary-entries.ts, which mirrors the Swift array
  line-for-line. Editing one file without the other is a parity bug.
-->
<script lang="ts">
  import { GLOSSARY_ENTRIES } from './glossary-entries';

  interface Props {
    onClose?: () => void;
  }
  let { onClose }: Props = $props();
</script>

<section class="panel" role="dialog" aria-label="Glossary">
  <header>
    <h2>Glossary</h2>
    {#if onClose}
      <button class="close" aria-label="Close" onclick={onClose}>×</button>
    {/if}
  </header>

  <div class="divider"></div>

  <div class="body">
    {#each GLOSSARY_ENTRIES as entry, i (entry.term)}
      <article class="entry">
        <h3>{entry.term}</h3>
        <p>{entry.definition}</p>
      </article>
      {#if i < GLOSSARY_ENTRIES.length - 1}
        <div class="divider subtle"></div>
      {/if}
    {/each}
  </div>
</section>

<style>
  .panel {
    width: 520px;
    max-height: 560px;
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
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .close:hover { background: rgba(128, 128, 128, 0.2); }
  .divider {
    height: 1px;
    background: var(--loca-color-border);
  }
  .divider.subtle { opacity: 0.6; margin: 0 20px; }
  .body {
    overflow-y: auto;
    padding: 20px;
  }
  .entry {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 10px 0;
  }
  .entry h3 {
    font-size: 13px;
    font-weight: 600;
    margin: 0;
    color: var(--loca-color-text);
  }
  .entry p {
    font-size: 12px;
    margin: 0;
    color: var(--loca-color-text-muted);
    line-height: 1.5;
  }
</style>
