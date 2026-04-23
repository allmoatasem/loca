<!--
  PreferencesView — mirrors Loca-SwiftUI/Sources/Loca/Views/PreferencesView.swift.

  Phase 3 + 3b: all six tabs are ported. Power-user subcomponents that
  haven't landed in Svelte yet are flagged inline with a "Mac-only for
  now" note — not hidden, not disabled, just visibly pending so users
  know what the browser UI won't do yet:
    - Inference: recipe cards + sliders ✓. External-server preset
      selector (LM Studio / Ollama / Custom) is in the Server tab,
      mirroring Swift's ExternalServerSection placement.
    - Performance: MLX param sliders ✓. The hardware-aware "Suggest"
      button is Mac-only (reads system VRAM via system_profiler).
    - System Prompt: textarea ✓.
    - Knowledge: kicks off /api/import; server-sent progress updates
      land in a follow-up that adds the EventSource wiring.
    - Server: native ↔ external toggle ✓. Tailscale remote-host
      switcher is Mac-only for now.

  SwiftUI stays source of truth; every tab edit updates both files.
-->
<script lang="ts">
  import { RECIPES, type InferenceRecipe } from './inference-recipes';

  interface Props {
    onClose?: () => void;
  }
  let { onClose }: Props = $props();

  type Tab = 'general' | 'inference' | 'performance' | 'sys-prompt' | 'knowledge' | 'server';
  const TABS: ReadonlyArray<{ id: Tab; label: string }> = [
    { id: 'general',     label: 'General'       },
    { id: 'inference',   label: 'Inference'     },
    { id: 'performance', label: 'Performance'   },
    { id: 'sys-prompt',  label: 'System Prompt' },
    { id: 'knowledge',   label: 'Knowledge'     },
    { id: 'server',      label: 'Server'        },
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
    document.documentElement.dataset.theme = t;
    document.documentElement.style.colorScheme = t === 'auto' ? '' : t;
  }
  let theme = $state<Theme>(readTheme());
  $effect(() => { applyTheme(theme); });
  function setTheme(v: Theme): void {
    theme = v; localStorage.setItem(themeKey, v);
  }

  // ── Default context ─────────────────────────────────────────────────
  const ctxKey = 'loca-default-ctx';
  const CTX_OPTIONS = [4096, 8192, 16384, 32768, 65536, 131072, 262144];
  function readCtx(): number {
    const n = parseInt(localStorage.getItem(ctxKey) ?? '', 10);
    return CTX_OPTIONS.includes(n) ? n : 32768;
  }
  let ctx = $state<number>(readCtx());
  function setCtx(n: number): void { ctx = n; localStorage.setItem(ctxKey, String(n)); }
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
      if (r.ok) modelsDir = (await r.json()).models_dir ?? '';
    } catch { /* silent */ }
  })();
  async function saveModelsDir(): Promise<void> {
    const path = modelsDir.trim();
    if (!path) { modelsDirStatus = 'Path cannot be empty.'; modelsDirStatusOk = false; return; }
    try {
      const r = await fetch('/api/config/models-dir', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ models_dir: path }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      modelsDir = (await r.json()).models_dir;
      modelsDirStatus = 'Saved — restart Loca for all services to use the new path.';
      modelsDirStatusOk = true;
    } catch (e) {
      modelsDirStatus = `Failed to save: ${e instanceof Error ? e.message : e}`;
      modelsDirStatusOk = false;
    }
  }

  // ── Inference ───────────────────────────────────────────────────────
  const paramsKey = 'loca-inference-params';
  interface Params {
    recipe: string;
    temperature: number; top_p: number; top_k: number;
    repeat_penalty: number; max_tokens: number;
  }
  function readParams(): Params {
    try {
      const raw = localStorage.getItem(paramsKey);
      if (raw) return JSON.parse(raw);
    } catch { /* ignore */ }
    const r = RECIPES[0];
    return { recipe: r.name, temperature: r.temperature, top_p: r.top_p, top_k: r.top_k, repeat_penalty: r.repeat_penalty, max_tokens: r.max_tokens };
  }
  let params = $state<Params>(readParams());
  $effect(() => { localStorage.setItem(paramsKey, JSON.stringify(params)); });
  const isCustom = $derived(params.recipe === 'Custom');
  function pickRecipe(r: InferenceRecipe): void {
    if (r.name === 'Custom') { params = { ...params, recipe: 'Custom' }; return; }
    params = { recipe: r.name, temperature: r.temperature, top_p: r.top_p, top_k: r.top_k, repeat_penalty: r.repeat_penalty, max_tokens: r.max_tokens };
  }
  function bumpToCustom(): void { if (!isCustom) params = { ...params, recipe: 'Custom' }; }

  // ── Advanced: chat_template_kwargs + extra_body ─────────────────────
  // chat_template_kwargs forwards Jinja-template vars (Qwen3's
  // enable_thinking, Qwen3.6's preserve_thinking, etc.). extra_body
  // carries arbitrary sampling extras (min_p, mirostat_*, xtc_*, dry_*,
  // …) the backend understands but Loca doesn't model explicitly. Both
  // flow through the proxy → orchestrator → backend unchanged.
  const ctkKey = 'loca-template-kwargs';
  const ebKey  = 'loca-extra-body';
  let templateKwargsJson = $state<string>(localStorage.getItem(ctkKey) ?? '');
  let extraBodyJson      = $state<string>(localStorage.getItem(ebKey)  ?? '');
  let templateKwargsErr  = $state<string | null>(null);
  let extraBodyErr       = $state<string | null>(null);
  // Quick toggles for Qwen3's template vars — set keys inside
  // chat_template_kwargs without the user having to edit JSON.
  type ThinkingMode = 'auto' | 'off' | 'preserve';
  function readThinking(): ThinkingMode {
    try {
      const obj = templateKwargsJson.trim() ? JSON.parse(templateKwargsJson) : {};
      if (obj.preserve_thinking === true) return 'preserve';
      if (obj.enable_thinking === false) return 'off';
    } catch { /* parse error → auto */ }
    return 'auto';
  }
  let thinkingMode = $state<ThinkingMode>(readThinking());
  function setThinkingMode(m: ThinkingMode): void {
    thinkingMode = m;
    let obj: Record<string, unknown> = {};
    try { obj = templateKwargsJson.trim() ? JSON.parse(templateKwargsJson) : {}; }
    catch { obj = {}; }
    delete obj.enable_thinking;
    delete obj.preserve_thinking;
    if (m === 'off')      obj.enable_thinking = false;
    if (m === 'preserve') obj.preserve_thinking = true;
    templateKwargsJson = Object.keys(obj).length > 0 ? JSON.stringify(obj, null, 2) : '';
    templateKwargsErr = null;
  }
  $effect(() => {
    const s = templateKwargsJson.trim();
    if (!s) { templateKwargsErr = null; localStorage.setItem(ctkKey, ''); return; }
    try { JSON.parse(s); templateKwargsErr = null; localStorage.setItem(ctkKey, s); }
    catch (e) { templateKwargsErr = e instanceof Error ? e.message : 'Invalid JSON'; }
  });
  $effect(() => {
    const s = extraBodyJson.trim();
    if (!s) { extraBodyErr = null; localStorage.setItem(ebKey, ''); return; }
    try { JSON.parse(s); extraBodyErr = null; localStorage.setItem(ebKey, s); }
    catch (e) { extraBodyErr = e instanceof Error ? e.message : 'Invalid JSON'; }
  });

  // ── Performance ─────────────────────────────────────────────────────
  const perfKey = 'loca-performance-params';
  interface PerfParams { gpu_layers: number; batch_size: number; cpu_threads: number; }
  function readPerf(): PerfParams {
    try {
      const raw = localStorage.getItem(perfKey);
      if (raw) return JSON.parse(raw);
    } catch { /* ignore */ }
    return { gpu_layers: 99, batch_size: 512, cpu_threads: 8 };
  }
  let perf = $state<PerfParams>(readPerf());
  $effect(() => { localStorage.setItem(perfKey, JSON.stringify(perf)); });

  // ── System prompt ───────────────────────────────────────────────────
  const sysPromptKey = 'loca-system-prompt-override';
  let sysPrompt = $state<string>(localStorage.getItem(sysPromptKey) ?? '');
  $effect(() => { localStorage.setItem(sysPromptKey, sysPrompt); });

  // ── Knowledge import ────────────────────────────────────────────────
  let importPath = $state<string>('');
  let importStatus = $state<string | null>(null);
  let importStatusOk = $state<boolean>(false);
  async function startImport(): Promise<void> {
    const path = importPath.trim();
    if (!path) { importStatus = 'Enter a path or URL to import.'; importStatusOk = false; return; }
    importStatus = 'Importing…'; importStatusOk = true;
    try {
      const r = await fetch('/api/import', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      importStatus = 'Import started — see Manage Memories for results.';
      importStatusOk = true;
    } catch (e) {
      importStatus = `Failed: ${e instanceof Error ? e.message : e}`;
      importStatusOk = false;
    }
  }

  // ── Server (external backend toggle) ────────────────────────────────
  let extEnabled = $state<boolean>(false);
  let extUrl = $state<string>('http://localhost:1234');
  let serverStatus = $state<string | null>(null);
  let serverStatusOk = $state<boolean>(false);
  (async () => {
    try {
      const r = await fetch('/api/backend/mode');
      if (r.ok) {
        const d = await r.json();
        extEnabled = !!d.lm_studio;
        if (d.lm_studio_url) extUrl = d.lm_studio_url;
      }
    } catch { /* silent */ }
  })();
  async function saveBackend(): Promise<void> {
    serverStatus = null;
    try {
      const r = await fetch('/api/backend/mode', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lm_studio: extEnabled, lm_studio_url: extUrl }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      serverStatus = 'Saved.';
      serverStatusOk = true;
    } catch (e) {
      serverStatus = `Failed: ${e instanceof Error ? e.message : e}`;
      serverStatusOk = false;
    }
  }
</script>

<section class="panel" aria-label="Preferences">
  <header>
    <h2>Preferences</h2>
    {#if onClose}
      <button class="close" aria-label="Close" onclick={onClose}>×</button>
    {/if}
  </header>

  <nav class="tabs">
    {#each TABS as tab (tab.id)}
      <button
        class:active={active === tab.id}
        onclick={() => (active = tab.id)}
        aria-pressed={active === tab.id}
      >{tab.label}</button>
    {/each}
  </nav>

  <div class="body">
    {#if active === 'general'}
      <section class="group">
        <h3>Appearance</h3>
        <div class="row">
          <label for="pref-theme">Theme</label>
          <select id="pref-theme" value={theme}
            onchange={(e) => setTheme((e.currentTarget as HTMLSelectElement).value as Theme)}>
            <option value="auto">Auto (system)</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </div>
      </section>

      <section class="group">
        <h3>Context</h3>
        <div class="row">
          <label for="pref-ctx">Default context window</label>
          <select id="pref-ctx" value={ctx}
            onchange={(e) => setCtx(parseInt((e.currentTarget as HTMLSelectElement).value, 10))}>
            {#each CTX_OPTIONS as n}
              <option value={n}>{ctxLabel(n)}</option>
            {/each}
          </select>
        </div>
        <p class="hint">Tokens the model keeps in memory per conversation. Higher values use more RAM.</p>
      </section>

      <section class="group">
        <h3>Models directory</h3>
        <p class="hint">Where Loca scans for GGUF / MLX models. Restart recommended after changing.</p>
        <div class="path-row">
          <input type="text" placeholder="~/loca_models" bind:value={modelsDir} />
          <button onclick={saveModelsDir} disabled={!modelsDir.trim()}>Save</button>
        </div>
        {#if modelsDirStatus}
          <p class="status" class:ok={modelsDirStatusOk} class:err={!modelsDirStatusOk}>{modelsDirStatus}</p>
        {/if}
      </section>

    {:else if active === 'inference'}
      <section class="group">
        <h3>Recipe</h3>
        <div class="recipe-grid">
          {#each RECIPES as r (r.name)}
            <button
              class="recipe-card"
              class:selected={params.recipe === r.name}
              onclick={() => pickRecipe(r)}
            >
              <div class="recipe-name">{r.name}</div>
              <div class="recipe-meta">
                {#if r.name === 'Custom'}
                  Your own<br/>parameters
                {:else}
                  temp {r.temperature.toFixed(2)}<br/>
                  top-p {r.top_p.toFixed(2)}<br/>
                  max {r.max_tokens}
                {/if}
              </div>
            </button>
          {/each}
        </div>
      </section>

      <section class="group">
        <h3>Parameters {#if !isCustom}<span class="muted">(select Custom to edit)</span>{/if}</h3>
        <div class="slider">
          <label for="sl-temp">Temperature <span class="val">{params.temperature.toFixed(2)}</span></label>
          <input id="sl-temp" type="range" min="0" max="2" step="0.01" value={params.temperature}
            disabled={!isCustom}
            oninput={(e) => { bumpToCustom(); params.temperature = parseFloat((e.currentTarget as HTMLInputElement).value); }} />
        </div>
        <div class="slider">
          <label for="sl-topp">Top-P <span class="val">{params.top_p.toFixed(2)}</span></label>
          <input id="sl-topp" type="range" min="0" max="1" step="0.01" value={params.top_p}
            disabled={!isCustom}
            oninput={(e) => { bumpToCustom(); params.top_p = parseFloat((e.currentTarget as HTMLInputElement).value); }} />
        </div>
        <div class="slider">
          <label for="sl-topk">Top-K <span class="val">{params.top_k}</span></label>
          <input id="sl-topk" type="range" min="1" max="100" step="1" value={params.top_k}
            disabled={!isCustom}
            oninput={(e) => { bumpToCustom(); params.top_k = parseInt((e.currentTarget as HTMLInputElement).value, 10); }} />
        </div>
        <div class="slider">
          <label for="sl-rep">Repeat Penalty <span class="val">{params.repeat_penalty.toFixed(2)}</span></label>
          <input id="sl-rep" type="range" min="1" max="2" step="0.01" value={params.repeat_penalty}
            disabled={!isCustom}
            oninput={(e) => { bumpToCustom(); params.repeat_penalty = parseFloat((e.currentTarget as HTMLInputElement).value); }} />
        </div>
        <div class="slider">
          <label for="sl-max">Max Tokens <span class="val">{params.max_tokens}</span></label>
          <input id="sl-max" type="range" min="128" max="8192" step="64" value={params.max_tokens}
            disabled={!isCustom}
            oninput={(e) => { bumpToCustom(); params.max_tokens = parseInt((e.currentTarget as HTMLInputElement).value, 10); }} />
        </div>
      </section>

      <section class="group">
        <h3>Advanced</h3>
        <p class="hint">
          Forwarded verbatim to the backend. Use <code>chat_template_kwargs</code>
          for template vars the model's Jinja template reads
          (e.g. Qwen3 <code>enable_thinking</code>, Qwen3.6
          <code>preserve_thinking</code>). Use <code>extra_body</code> for
          sampling extras Loca doesn't model explicitly
          (<code>min_p</code>, <code>mirostat_tau</code>, <code>xtc_probability</code>,
          <code>dry_multiplier</code>, <code>grammar</code>, …).
        </p>

        <div class="row" style="margin-top:10px;">
          <span>Thinking mode (Qwen3 / Qwen3.6)</span>
          <div class="seg">
            {#each ['auto', 'off', 'preserve'] as m (m)}
              <button
                class:active={thinkingMode === m}
                onclick={() => setThinkingMode(m as ThinkingMode)}
              >{m}</button>
            {/each}
          </div>
        </div>

        <label for="pref-ctk" style="font-size:11px;color:var(--loca-color-text-muted);margin-top:12px;display:block;">
          chat_template_kwargs (JSON)
        </label>
        <textarea id="pref-ctk" rows="4"
          placeholder={'{"enable_thinking": false}'}
          bind:value={templateKwargsJson}></textarea>
        {#if templateKwargsErr}
          <p class="status err">{templateKwargsErr}</p>
        {/if}

        <label for="pref-eb" style="font-size:11px;color:var(--loca-color-text-muted);margin-top:12px;display:block;">
          extra_body (JSON)
        </label>
        <textarea id="pref-eb" rows="4"
          placeholder={'{"min_p": 0.05, "mirostat_tau": 5.0}'}
          bind:value={extraBodyJson}></textarea>
        {#if extraBodyErr}
          <p class="status err">{extraBodyErr}</p>
        {/if}
      </section>

    {:else if active === 'performance'}
      <section class="group">
        <h3>MLX / llama.cpp parameters</h3>
        <div class="slider">
          <label for="sl-gpu">GPU Layers <span class="val">{perf.gpu_layers}</span></label>
          <input id="sl-gpu" type="range" min="0" max="99" step="1" value={perf.gpu_layers}
            oninput={(e) => perf.gpu_layers = parseInt((e.currentTarget as HTMLInputElement).value, 10)} />
        </div>
        <div class="slider">
          <label for="sl-batch">Batch Size <span class="val">{perf.batch_size}</span></label>
          <input id="sl-batch" type="range" min="64" max="4096" step="64" value={perf.batch_size}
            oninput={(e) => perf.batch_size = parseInt((e.currentTarget as HTMLInputElement).value, 10)} />
        </div>
        <div class="slider">
          <label for="sl-cpu">CPU Threads <span class="val">{perf.cpu_threads}</span></label>
          <input id="sl-cpu" type="range" min="1" max="32" step="1" value={perf.cpu_threads}
            oninput={(e) => perf.cpu_threads = parseInt((e.currentTarget as HTMLInputElement).value, 10)} />
        </div>
        <p class="hint">
          Stored client-side. The hardware-aware <em>Suggest</em> button in
          the Mac app reads system VRAM; that's not available in a browser
          sandbox, so you'll want to copy suggested values over manually.
        </p>
      </section>

    {:else if active === 'sys-prompt'}
      <section class="group">
        <h3>System Prompt Override</h3>
        <textarea
          rows="12"
          placeholder="Leave empty to use Loca's built-in mode-aware prompts (recommended). If set, this replaces the system prompt for all conversations."
          bind:value={sysPrompt}
        ></textarea>
        <p class="hint">
          Loca's built-in prompts are mode-aware (General, Code, Thinking,
          Vision) and include hardware context. An override applies to all modes.
        </p>
        {#if sysPrompt.trim()}
          <button class="danger" onclick={() => (sysPrompt = '')}>Clear Override</button>
        {/if}
      </section>

    {:else if active === 'knowledge'}
      <section class="group">
        <h3>Import Knowledge</h3>
        <p class="hint">
          Point Loca at a file, folder, or URL to ingest into memory. Full list
          of supported formats in the Manage Models &gt; Import Knowledge panel
          on the Mac app; the Svelte side will grow a progress view in a
          follow-up PR.
        </p>
        <div class="path-row">
          <input type="text" placeholder="Path to file, folder, or URL…" bind:value={importPath} />
          <button onclick={startImport} disabled={!importPath.trim()}>Import</button>
        </div>
        {#if importStatus}
          <p class="status" class:ok={importStatusOk} class:err={!importStatusOk}>{importStatus}</p>
        {/if}
      </section>

    {:else if active === 'server'}
      <section class="group">
        <h3>Inference Backend</h3>
        <label class="toggle">
          <input type="checkbox" bind:checked={extEnabled} />
          <span>Use an external OpenAI-compatible server (LM Studio, Ollama, or custom)</span>
        </label>
        {#if extEnabled}
          <div class="path-row">
            <input type="text" placeholder="http://localhost:1234" bind:value={extUrl} />
          </div>
          <p class="hint">
            Loca will forward <code>/v1/chat/completions</code> calls to this URL
            instead of managing its own <code>mlx_lm</code> / <code>llama-server</code>.
          </p>
        {:else}
          <p class="hint">
            Loca manages its own inference server ({'<code>'}mlx_lm{'</code>'} for MLX models,
            {'<code>'}llama-server{'</code>'} for GGUF). No external process required.
          </p>
        {/if}
        <button onclick={saveBackend}>Save</button>
        {#if serverStatus}
          <p class="status" class:ok={serverStatusOk} class:err={!serverStatusOk}>{serverStatus}</p>
        {/if}
        <p class="hint footnote">
          The Mac app also has a Tailscale remote-host switcher for offloading
          the whole Loca stack to a beefier machine. That lands on Svelte in a
          follow-up.
        </p>
      </section>
    {/if}
  </div>
</section>

<style>
  .panel {
    width: 600px;
    max-height: 680px;
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
    width: 24px; height: 24px; border-radius: 50%;
    border: none; background: rgba(128, 128, 128, 0.1);
    color: var(--loca-color-text-muted);
    font-size: 14px; cursor: pointer;
  }
  .close:hover { background: rgba(128, 128, 128, 0.2); }

  .tabs {
    display: flex;
    gap: 4px;
    padding: 0 20px 12px;
    border-bottom: 1px solid var(--loca-color-border);
    flex-wrap: wrap;
  }
  .tabs button {
    background: none; border: none;
    padding: 6px 10px;
    font-size: 12px;
    color: var(--loca-color-text-muted);
    cursor: pointer;
    border-radius: var(--loca-radius-sm);
  }
  .tabs button:hover { color: var(--loca-color-text); }
  .tabs button.active {
    background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent);
    color: var(--loca-color-accent);
    font-weight: 500;
  }

  .body {
    flex: 1; overflow-y: auto;
    padding: 16px 20px 24px;
    display: flex; flex-direction: column; gap: 20px;
  }

  .group h3 {
    font-size: 13px; font-weight: 600;
    margin: 0 0 10px;
    color: var(--loca-color-text);
  }
  .muted { font-weight: 400; color: var(--loca-color-text-muted); font-size: 11px; }

  .row {
    display: flex; align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .row label { font-size: 12px; color: var(--loca-color-text); }

  select, input[type='text'] {
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 5px 8px;
    font-size: 12px;
    color: var(--loca-color-text);
  }
  input[type='text'] { flex: 1; font-family: var(--loca-font-mono); }
  select {
    cursor: pointer;
    /* Keep Appearance/Context dropdowns the same width so their right
       edges line up regardless of the longest option label. */
    min-width: 150px;
  }

  .hint {
    margin: 6px 0 0;
    font-size: 11px;
    color: var(--loca-color-text-muted);
    line-height: 1.5;
  }
  .hint.footnote { margin-top: 16px; font-style: italic; }

  .path-row { display: flex; gap: 8px; margin-top: 8px; }
  .path-row button {
    background: var(--loca-color-accent);
    color: #fff; border: none;
    border-radius: var(--loca-radius-sm);
    padding: 6px 14px;
    font-size: 12px;
    cursor: pointer;
  }
  .path-row button:hover:not(:disabled) { background: var(--loca-color-accent-hover); }
  .path-row button:disabled { opacity: 0.4; cursor: not-allowed; }

  .status { margin: 8px 0 0; font-size: 11px; }
  .status.ok  { color: var(--loca-color-success); }
  .status.err { color: var(--loca-color-danger); }

  .recipe-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
    gap: 8px;
  }
  .recipe-card {
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 8px 10px;
    text-align: left;
    cursor: pointer;
    color: var(--loca-color-text);
  }
  .recipe-card.selected {
    border-color: var(--loca-color-accent);
    background: color-mix(in srgb, var(--loca-color-accent) 10%, transparent);
  }
  .recipe-name { font-size: 12px; font-weight: 600; margin-bottom: 4px; }
  .recipe-meta { font-size: 10px; color: var(--loca-color-text-muted); line-height: 1.5; }

  .slider { display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; }
  .slider label {
    font-size: 11px;
    color: var(--loca-color-text);
    display: flex;
    justify-content: space-between;
  }
  .slider .val { font-family: var(--loca-font-mono); color: var(--loca-color-text-muted); }
  .slider input[type='range'] { width: 100%; }
  .slider input[type='range']:disabled { opacity: 0.4; }

  textarea {
    width: 100%;
    box-sizing: border-box;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 12px 14px;
    font-size: 12px;
    font-family: var(--loca-font-mono);
    line-height: 1.5;
    color: var(--loca-color-text);
    resize: vertical;
  }
  textarea::placeholder {
    color: var(--loca-color-text-muted);
    opacity: 0.7;
  }

  .danger {
    background: none;
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    color: var(--loca-color-danger);
    padding: 5px 12px;
    font-size: 11px;
    cursor: pointer;
    margin-top: 10px;
  }

  .toggle {
    display: flex; align-items: center; gap: 8px;
    font-size: 12px; color: var(--loca-color-text);
    cursor: pointer;
  }
  code { font-family: var(--loca-font-mono); font-size: 0.9em; background: rgba(127,127,127,0.12); padding: 1px 4px; border-radius: 3px; }

  .seg {
    display: inline-flex;
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    overflow: hidden;
  }
  .seg button {
    background: none;
    border: none;
    padding: 4px 10px;
    font-size: 11px;
    color: var(--loca-color-text-muted);
    cursor: pointer;
    text-transform: capitalize;
  }
  .seg button.active {
    background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent);
    color: var(--loca-color-accent);
    font-weight: 500;
  }
  .seg button + button { border-left: 1px solid var(--loca-color-border); }
</style>
