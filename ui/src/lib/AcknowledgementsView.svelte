<!--
  AcknowledgementsView — visual twin of
  Loca-SwiftUI/Sources/Loca/Views/AcknowledgementsView.swift. Panel lists the
  open-source projects Loca bundles or depends on. Data lives in
  acknowledgements-entries.ts and must stay in sync with the Swift side and
  the legacy HTML (src/static/index.html) — these three surfaces are the
  single source of truth for what ships in the app.
-->
<script lang="ts">
  import { ACK_SECTIONS } from './acknowledgements-entries';
  import { APP_VERSION } from './version';

  interface Props {
    onClose?: () => void;
  }
  let { onClose }: Props = $props();
</script>

<section class="panel" role="dialog" aria-label="Acknowledgements">
  <header>
    <h2>Acknowledgments</h2>
    {#if onClose}
      <button class="close" aria-label="Close" onclick={onClose}>×</button>
    {/if}
  </header>

  <div class="divider"></div>

  <div class="body">
    <p class="version">Loca {APP_VERSION}</p>

    {#each ACK_SECTIONS as section (section.title)}
      <section class="ack-section">
        <h3 class="section-title">{section.title}</h3>
        {#each section.items as item (item.name)}
          <div class="ack-item">
            <div class="ack-left">
              <span class="ack-name">{item.name}</span>
              <span class="ack-author">{item.author}</span>
            </div>
            <div class="ack-right">
              {#if item.license}
                <span class="ack-license">{item.license}</span>
              {/if}
              <a class="ack-link" href={item.url} target="_blank" rel="noopener noreferrer">
                {item.url.includes('github.com') ? 'GitHub' : 'Website'}
              </a>
            </div>
          </div>
        {/each}
      </section>
    {/each}
  </div>

  <div class="divider"></div>

  <footer>
    Loca is built on the shoulders of these open source projects. Thank you.
  </footer>
</section>

<style>
  .panel {
    width: 540px;
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
    padding: 20px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .version {
    margin: 0 0 8px;
    font-size: 22px;
    font-weight: 700;
    text-align: center;
    color: var(--loca-color-text);
  }

  .ack-section {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .section-title {
    margin: 0 0 4px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    color: var(--loca-color-text-muted);
  }
  .ack-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 6px 0;
    font-size: 12px;
  }
  .ack-left {
    display: flex;
    flex-direction: column;
  }
  .ack-name {
    font-weight: 600;
    color: var(--loca-color-text);
  }
  .ack-author {
    color: var(--loca-color-text-muted);
    font-size: 11px;
  }
  .ack-right {
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }
  .ack-license {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 4px;
    background: rgba(128, 128, 128, 0.12);
    color: var(--loca-color-text-muted);
  }
  .ack-link {
    color: var(--loca-color-accent);
    text-decoration: none;
  }
  .ack-link:hover { text-decoration: underline; }

  footer {
    padding: 12px 20px;
    font-size: 11px;
    text-align: center;
    color: var(--loca-color-text-muted);
  }
</style>
