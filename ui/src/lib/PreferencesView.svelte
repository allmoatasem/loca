<!--
  PreferencesView — mirrors Loca-SwiftUI/Sources/Loca/Views/PreferencesView.swift.

  Phase 3 scope: the tab strip + the full General tab (Appearance, Context,
  Models directory). Other tabs land in Phase 3b:
      - Inference       (recipes + sliders + external server toggle)
      - Performance     (MLX param suggestions)
      - System Prompt   (override textarea)
      - Knowledge       (import UI)
      - Server          (native-vs-external toggle)

  SwiftUI is source of truth for layout and copy; edits to either file
  land in the same PR per the parity checklist.
-->
<script lang="ts">
  interface Props {
    onClose?: () => void;
  }
  let { onClose }: Props = $props();

  type Tab = 'general' | 'inference' | 'performance' | 'sys-prompt' | 'knowledge' | 'server';
  const TABS: ReadonlyArray<{ id: Tab; label: string; enabled: boolean }> = [
    { id: 'general',     label: 'General',       enabled: true  },
    { id: 'inference',   label: 'Inference',     enabled: false },
    { id: 'performance', label: 'Performance',   enabled: false },
    { id: 'sys-prompt',  label: 'System Prompt', enabled: false },
    { id: 'knowledge',   label: 'Knowledge',     enabled: false },
    { id: 'server',      label: 'Server',        enabled: false },
  ];

  let active = $state<Tab>('general');

  // ── Appearance: theme ───────────────────────────────────────────────
  type Theme = 'auto' | 'light' | 'dark';
  const themeKey = 'loca-theme';

  function readTheme(): Theme {
    const t = localStorage.getItem(themeKey);
    return t === 'light' || t === 'dark' ? t : 'auto';
  }

  function applyTheme(t: Theme): void {
    // tokens.css uses prefers-color-scheme by default. Explicit choice is
    // applied by setting a color-scheme on <html> and a data-attribute
    // the tokens file can key off.
    document.documentElement.dataset.theme = t;
    if (t === 'auto') {
      document.documentElement.style.colorScheme = '';
    } else {
      document.documentElement.style.colorScheme = t;
    }
  }

  let theme = $state<Theme>(readTheme());
  applyTheme(theme);
  function setTheme(v: Theme): void {
    theme = v;
    localStorage.setItem(themeKey, v);
    applyTheme(v);
  }

  // ── Context window default ──────────────────────────────────────────
  const ctxKey = 'loca-default-ctx';
  const CTX_OPTIONS = [4096, 8192, 16384, 32768, 65536, 131072, 262144];
  function readCtx(): number {
    const n = parseInt(localStorage.getItem(ctxKey) ?? '', 10);
    return CTX_OPTIONS.includes(n) ? n : 32768;
  }
  let ctx = $state<number>(readCtx());
  function setCtx(n: number): void {
    ctx = n;
    localStorage.setItem(ctxKey, String(n));
  }
  function ctxLabel(n: number): string {
    return n >= 1024 ? `${n / 1024}K tokens` : `${n} tokens`;
  }

  // ── Models directory ────────────────────────────────────────────────
  let modelsDir = $state<string>('');
  let modelsDirStatus = $state<string | null>(null);
  let modelsDirStatusOk = $state<boolean>(false);

  (async () => {
    try {
      const r = await fetch('/api/config/models-dir');
      if (r.ok) {
        const d = await r.json();
        modelsDir = d.models_dir ?? '';
      }
    } catch {
      // Silent: shown as empty input so the user can fill it.
    }
  })();

  async function saveModelsDir(): Promise<void> {
    const path = modelsDir.trim();
    if (!path) {
      modelsDirStatus = 'Path cannot be empty.';
      modelsDirStatusOk = false;
      return;
    }
    try {
      const r = await fetch('/api/config/models-dir', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ models_dir: path }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      modelsDir = d.models_dir;
      modelsDirStatus = 'Saved — restart Loca for all services to use the new path.';
      modelsDirStatusOk = true;
    } catch (e) {
      modelsDirStatus = `Failed to save: ${e instanceof Error ? e.message : e}`;
      modelsDirStatusOk = false;
    }
  }
</script>

<section class="panel" role="dialog" aria-label="Preferences">
  <header>
    <h2>Preferences</h2>
    {#if onClose}
      <button class="close" aria-label="Close" onclick={onClose}>×</button>
    {/if}
  </header>

  <nav class="tabs" role="tablist">
    {#each TABS as tab (tab.id)}
      <button
        role="tab"
        class:active={active === tab.id}
        disabled={!tab.enabled}
        onclick={() => { if (tab.enabled) active = tab.id; }}
        aria-selected={active === tab.id}
      >
        {tab.label}{#if !tab.enabled}&nbsp;<span class="soon">(3b)</span>{/if}
      </button>
    {/each}
  </nav>

  <div class="body">
    {#if active === 'general'}
      <!-- Appearance -->
      <section class="group">
        <h3>Appearance</h3>
        <div class="row">
          <label for="pref-theme">Theme</label>
          <select
            id="pref-theme"
            value={theme}
            onchange={(e) => setTheme((e.currentTarget as HTMLSelectElement).value as Theme)}
          >
            <option value="auto">Auto (system)</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </div>
      </section>

      <!-- Context -->
      <section class="group">
        <h3>Context</h3>
        <div class="row">
          <label for="pref-ctx">Default context window</label>
          <select
            id="pref-ctx"
            value={ctx}
            onchange={(e) => setCtx(parseInt((e.currentTarget as HTMLSelectElement).value, 10))}
          >
            {#each CTX_OPTIONS as n}
              <option value={n}>{ctxLabel(n)}</option>
            {/each}
          </select>
        </div>
        <p class="hint">
          Tokens the model keeps in memory per conversation. Higher values use
          more RAM.
        </p>
      </section>

      <!-- Models directory -->
      <section class="group">
        <h3>Models directory</h3>
        <p class="hint">
          Where Loca scans for GGUF / MLX models. Change this to store models
          on an external SSD. Restart recommended after changing.
        </p>
        <div class="models-dir">
          <input
            type="text"
            placeholder="~/loca_models"
            bind:value={modelsDir}
          />
          <button onclick={saveModelsDir} disabled={!modelsDir.trim()}>Save</button>
        </div>
        {#if modelsDirStatus}
          <p class="status" class:ok={modelsDirStatusOk} class:err={!modelsDirStatusOk}>
            {modelsDirStatus}
          </p>
        {/if}
      </section>
    {:else}
      <p class="coming">This tab ports in Phase 3b.</p>
    {/if}
  </div>
</section>

<style>
  .panel {
    width: 560px;
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
  h2 { font-size: 14px; font-weight: 600; margin: 0; }
  .close {
    width: 24px; height: 24px;
    border-radius: 50%;
    border: none;
    background: rgba(128, 128, 128, 0.1);
    color: var(--loca-color-text-muted);
    font-size: 14px;
    cursor: pointer;
  }
  .close:hover { background: rgba(128, 128, 128, 0.2); }

  .tabs {
    display: flex;
    gap: 4px;
    padding: 0 20px 12px;
    border-bottom: 1px solid var(--loca-color-border);
  }
  .tabs button {
    background: none;
    border: none;
    padding: 6px 10px;
    font-size: 12px;
    color: var(--loca-color-text-muted);
    cursor: pointer;
    border-radius: var(--loca-radius-sm);
  }
  .tabs button:hover:not(:disabled) { color: var(--loca-color-text); }
  .tabs button.active {
    background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent);
    color: var(--loca-color-accent);
    font-weight: 500;
  }
  .tabs button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .soon {
    font-size: 10px;
    opacity: 0.7;
  }

  .body {
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px 24px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .group h3 {
    font-size: 13px;
    font-weight: 600;
    margin: 0 0 10px;
    color: var(--loca-color-text);
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .row label {
    font-size: 12px;
    color: var(--loca-color-text);
  }
  select {
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 4px 8px;
    font-size: 12px;
    color: var(--loca-color-text);
    cursor: pointer;
  }
  .hint {
    margin: 6px 0 0;
    font-size: 11px;
    color: var(--loca-color-text-muted);
    line-height: 1.5;
  }
  .models-dir {
    display: flex;
    gap: 8px;
    margin-top: 8px;
  }
  .models-dir input {
    flex: 1;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 6px 10px;
    font-size: 12px;
    color: var(--loca-color-text);
    font-family: var(--loca-font-mono);
  }
  .models-dir button {
    background: var(--loca-color-accent);
    color: #fff;
    border: none;
    border-radius: var(--loca-radius-sm);
    padding: 6px 14px;
    font-size: 12px;
    cursor: pointer;
  }
  .models-dir button:hover:not(:disabled) { background: var(--loca-color-accent-hover); }
  .models-dir button:disabled { opacity: 0.4; cursor: not-allowed; }

  .status {
    margin: 8px 0 0;
    font-size: 11px;
  }
  .status.ok { color: var(--loca-color-success); }
  .status.err { color: var(--loca-color-danger); }

  .coming {
    color: var(--loca-color-text-muted);
    font-size: 13px;
    padding: 24px;
    text-align: center;
  }
</style>
