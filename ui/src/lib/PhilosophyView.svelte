<!--
  PhilosophyView — space for Loca's design philosophy writeup. Matches
  the legacy HTML structure: a simple titled panel whose body is
  deliberately empty until the author writes it. Kept in shape so that
  landing the copy later is a body-only edit.
-->
<script lang="ts">
  interface Props {
    onClose?: () => void;
  }
  let { onClose }: Props = $props();
</script>

<section class="panel" role="dialog" aria-label="Philosophy">
  <header>
    <h2>Philosophy</h2>
    {#if onClose}
      <button class="close" aria-label="Close" onclick={onClose}>×</button>
    {/if}
  </header>

  <div class="divider"></div>

  <div class="body">
    <p class="placeholder">Philosophy writeup coming soon.</p>
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
  }
  .close:hover { background: rgba(128, 128, 128, 0.2); }

  .divider {
    height: 1px;
    background: var(--loca-color-border);
  }

  .body {
    flex: 1;
    padding: 40px 20px;
    overflow-y: auto;
  }
  .placeholder {
    margin: 0;
    text-align: center;
    color: var(--loca-color-text-muted);
    font-size: 13px;
  }
</style>
