<!--
  ChatView — mirrors Loca-SwiftUI/Sources/Loca/Views/ChatView.swift.

  Phase 4 + 4b scope:
    - Message list with user / assistant bubbles
    - Composer: textarea + attachment chips + Send button
    - Streaming SSE against /v1/chat/completions
    - <think>…</think> splitting → collapsible ThinkBlock
    - Markdown rendering (paragraphs / headers / lists / links / code
      fences) with Prism syntax highlighting on code blocks
    - Copy-reply button on assistant bubbles
    - File attachments: POST /api/upload, then either embed as
      base64 data URL image (OpenAI vision format) or as extracted
      text inside an <attachment> tag (same shape Swift uses)
    - Conversation persistence: POST /api/conversations after each
      turn so refresh / sidebar-click round-trips the conv

  Deferred to Phase 4c:
    - Voice mode (audio recorder, transcription roundtrip, TTS
      playback)
    - Tool-call / research-mode UI
    - Attachment drag-and-drop from Finder (paste / Choose files
      work today)
    - Conversation rename in place
-->
<script lang="ts">
  import { app } from './app-store.svelte';
  import MessageBubble, { type Role } from './MessageBubble.svelte';
  import StatsBar from './StatsBar.svelte';
  import { tick } from 'svelte';

  interface Attachment {
    type: 'image' | 'text' | 'audio' | 'video' | 'binary';
    name: string;
    data?: string;       // for images: data:…;base64,…
    content?: string;    // for text: extracted text
  }
  interface Message {
    role: Role;
    content: string;
    imageUrls?: string[];
  }

  interface TurnStats {
    model: string;
    promptTokens: number;
    completionTokens: number;
    ttftMs: number;        // time-to-first-token in milliseconds
    totalMs: number;       // wall time for the whole turn
    tokensPerSec: number;
    searchTriggered: boolean;
    memoryInjected: boolean;
  }

  let history = $state<Message[]>([]);
  let input   = $state<string>('');
  let attachments = $state<Attachment[]>([]);
  let streaming = $state<boolean>(false);
  let errorMsg  = $state<string | null>(null);
  let scroller: HTMLDivElement | undefined = $state();
  let fileInput: HTMLInputElement | undefined = $state();
  // Last turn's generation stats — rendered as a dense mono-spaced bar
  // above the scroller, mirroring SwiftUI's GenerationStatsBar.
  let lastStats = $state<TurnStats | null>(null);
  // Session-only toggles, same as SwiftUI's AppState.researchMode /
  // lockdownMode. Research enables SearXNG + web fetching; lockdown
  // disables all network tools and mutually excludes research.
  let researchMode = $state<boolean>(false);
  let lockdownMode = $state<boolean>(false);
  function toggleResearch(): void {
    if (lockdownMode) return;
    researchMode = !researchMode;
  }
  function toggleLockdown(): void {
    lockdownMode = !lockdownMode;
    if (lockdownMode) researchMode = false;
  }

  // Round-trip the conversation to /api/conversations so a refresh
  // doesn't lose state and the sidebar picks it up.
  let convId = $state<string | null>(null);
  let convTitle = $state<string>('');

  // Sidebar clicks and the New Conversation button both route through
  // `app.activeConvId`. `convSelectNonce` bumps on every set (including
  // clicking the already-active row), so this effect always fires.
  $effect(() => {
    const nonce = app.convSelectNonce;
    void nonce;
    const target = app.activeConvId;
    if (target === convId) return;
    if (target === null) {
      history = [];
      convId = null;
      convTitle = '';
      lastStats = null;
      errorMsg = null;
      return;
    }
    void loadConversation(target);
  });

  async function loadConversation(id: string): Promise<void> {
    try {
      const r = await fetch(`/api/conversations/${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error(`GET /api/conversations/${id} → ${r.status}`);
      const data = await r.json();
      const msgs = (data.messages ?? []) as Array<{ role: string; content: string }>;
      history = msgs
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role as Role, content: m.content }));
      convId = id;
      convTitle = data.title ?? '';
      lastStats = null;
      errorMsg = null;
      await scrollToBottom();
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    }
  }

  async function scrollToBottom(): Promise<void> {
    await tick();
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }

  async function persistConversation(msgs: Message[]): Promise<void> {
    if (msgs.length < 2) return; // only persist after at least one exchange
    if (!convTitle) convTitle = msgs[0].content.slice(0, 60);
    try {
      const payload = {
        id: convId,
        title: convTitle,
        messages: msgs.map((m) => ({ role: m.role, content: m.content })),
        model: app.activeModelName ?? '',
      };
      const r = await fetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (r.ok) {
        const data = await r.json();
        if (!convId && data.id) {
          const newId = String(data.id);
          convId = newId;
          // Let the store know so the sidebar's `active` highlight
          // follows the newly-persisted conversation instead of sitting
          // on null. `refresh()` pulls the new row into the sidebar list.
          app.adoptActiveConv(newId);
          void app.refresh();
        } else if (convId) {
          // Existing conv — update title/preview so the sidebar's list
          // reflects the latest exchange without a full reload round-trip.
          void app.refresh();
        }
      }
    } catch {
      // Fire-and-forget: persistence failure doesn't block the chat.
    }
  }

  async function send(): Promise<void> {
    const text = input.trim();
    if ((!text && attachments.length === 0) || streaming) return;
    errorMsg = null;

    const imageAttachments = attachments.filter((a) => a.type === 'image');
    const textAttachments  = attachments.filter((a) => a.type === 'text');

    // Compose user content — text attachments wrap their extracted text in
    // an <attachment> tag so the model sees the filename alongside the body.
    const textParts: string[] = [];
    for (const a of textAttachments) {
      textParts.push(`<attachment name="${a.name}">\n${a.content ?? ''}\n</attachment>`);
    }
    if (text) textParts.push(text);
    const userText = textParts.join('\n\n');
    const userImageUrls = imageAttachments.map((a) => a.data!).filter(Boolean);

    history = [...history, { role: 'user', content: userText, imageUrls: userImageUrls }];
    input = '';
    attachments = [];
    streaming = true;
    await scrollToBottom();

    history = [...history, { role: 'assistant', content: '' }];
    const assistantIdx = history.length - 1;

    // Build the payload's messages. Images travel as OpenAI vision parts.
    const wireMessages = history.slice(0, -1).map((m, i) => {
      if (m.role === 'user' && i === history.length - 2 && imageAttachments.length > 0) {
        const parts: Array<Record<string, unknown>> = [{ type: 'text', text: userText }];
        for (const img of imageAttachments) parts.push({ type: 'image_url', image_url: { url: img.data } });
        return { role: 'user', content: parts };
      }
      return { role: m.role, content: m.content };
    });

    // Load advanced params from Preferences (see PreferencesView.svelte > Advanced).
    // Both are validated JSON at save-time, so parse-errors here should be rare —
    // if they happen, silently drop the field rather than block the request.
    function parseJsonPref(key: string): unknown | undefined {
      const raw = localStorage.getItem(key);
      if (!raw || !raw.trim()) return undefined;
      try { return JSON.parse(raw); } catch { return undefined; }
    }
    const chatTemplateKwargs = parseJsonPref('loca-template-kwargs');
    const extraBody          = parseJsonPref('loca-extra-body');

    try {
      const body: Record<string, unknown> = {
        mode: app.selectedCapability,
        messages: wireMessages,
        stream: true,
        num_ctx: app.contextWindow,
        research_mode: researchMode && !lockdownMode,
      };
      // Research Partner — project scope + partner-mode overlay. Both
      // optional; backend ignores default/empty values.
      if (app.activeProjectId) body.project_id = app.activeProjectId;
      if (app.partnerMode && app.partnerMode !== 'default') {
        body.partner_mode = app.partnerMode;
      }
      if (chatTemplateKwargs) body.chat_template_kwargs = chatTemplateKwargs;
      if (extraBody)          body.extra_body            = extraBody;
      const resp = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let assembled = '';
      const tStart = performance.now();
      let tFirst: number | null = null;
      let usagePromptTokens = 0;
      let usageCompletionTokens = 0;
      let modelName = app.activeModelName ?? 'local';
      let searchTriggered = false;
      let memoryInjected  = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';
        for (const raw of lines) {
          if (!raw.startsWith('data: ')) continue;
          const payload = raw.slice(6).trim();
          if (!payload || payload === '[DONE]') continue;
          try {
            const parsed = JSON.parse(payload);
            if (parsed?.model) modelName = parsed.model;
            const delta = parsed?.choices?.[0]?.delta?.content;
            if (typeof delta === 'string' && delta.length > 0) {
              if (tFirst === null) tFirst = performance.now();
              assembled += delta;
              const updated = [...history];
              updated[assistantIdx] = { role: 'assistant', content: assembled };
              history = updated;
              await scrollToBottom();
            }
            // Final usage payload — proxy emits these stats right before [DONE].
            const usage = parsed?.usage;
            if (usage) {
              usagePromptTokens     = Number(usage.prompt_tokens)     || usagePromptTokens;
              usageCompletionTokens = Number(usage.completion_tokens) || usageCompletionTokens;
              if (typeof usage.search_triggered === 'boolean') searchTriggered = usage.search_triggered;
              if (typeof usage.memory_injected  === 'boolean') memoryInjected  = usage.memory_injected;
            }
          } catch { /* ignore malformed SSE lines */ }
        }
      }

      const tEnd = performance.now();
      const totalMs = tEnd - tStart;
      const ttftMs  = tFirst === null ? 0 : tFirst - tStart;
      const genSeconds = tFirst === null ? 0 : (tEnd - tFirst) / 1000;
      if (!usageCompletionTokens) usageCompletionTokens = Math.max(1, Math.ceil(assembled.length / 4));
      lastStats = {
        model: modelName,
        promptTokens: usagePromptTokens,
        completionTokens: usageCompletionTokens,
        ttftMs,
        totalMs,
        tokensPerSec: genSeconds > 0 ? usageCompletionTokens / genSeconds : 0,
        searchTriggered,
        memoryInjected,
      };
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
      if (history[assistantIdx]?.content === '') {
        history = history.slice(0, assistantIdx);
      }
    } finally {
      streaming = false;
      void persistConversation(history);
    }
  }

  // Drag-and-drop attachments — drop a file anywhere over the chat
  // area or composer to upload it. Overrides the default browser
  // behaviour of opening the file in a new tab.
  let dragDepth = $state<number>(0);
  const isDragging = $derived(dragDepth > 0);
  function onDragEnter(e: DragEvent): void {
    if (e.dataTransfer?.types.includes('Files')) {
      e.preventDefault();
      dragDepth += 1;
    }
  }
  function onDragLeave(): void {
    if (dragDepth > 0) dragDepth -= 1;
  }
  function onDragOver(e: DragEvent): void {
    if (e.dataTransfer?.types.includes('Files')) e.preventDefault();
  }
  function onDrop(e: DragEvent): void {
    if (!e.dataTransfer?.types.includes('Files')) return;
    e.preventDefault();
    dragDepth = 0;
    if (e.dataTransfer.files.length > 0) {
      void handleFiles(e.dataTransfer.files);
    }
  }

  async function handleFiles(files: FileList | null): Promise<void> {
    if (!files) return;
    for (const file of Array.from(files)) {
      const fd = new FormData();
      fd.append('file', file);
      try {
        const r = await fetch('/api/upload', { method: 'POST', body: fd });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const info = (await r.json()) as Attachment;
        attachments = [...attachments, info];
      } catch (e) {
        errorMsg = `Upload failed for ${file.name}: ${e instanceof Error ? e.message : e}`;
      }
    }
  }

  function removeAttachment(idx: number): void {
    attachments = attachments.filter((_, i) => i !== idx);
  }

  function onKeydown(e: KeyboardEvent): void {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      void send();
    }
  }

  function newConversation(): void {
    history = [];
    convId = null;
    convTitle = '';
    errorMsg = null;
    lastStats = null;
  }
</script>

<div
  class="chat"
  class:dragging={isDragging}
  ondragenter={onDragEnter}
  ondragleave={onDragLeave}
  ondragover={onDragOver}
  ondrop={onDrop}
  role="region"
  aria-label="Chat"
>
  {#if isDragging}
    <div class="drop-overlay">
      <div class="drop-card">Drop files to attach</div>
    </div>
  {/if}
  <StatsBar stats={lastStats} contextWindow={app.contextWindow} />
  <div class="scroller" bind:this={scroller}>
    {#if history.length === 0}
      <div class="empty">
        <h1>Start a conversation</h1>
        <p>Select a model on the left and send a message to begin.</p>
      </div>
    {:else}
      <div class="messages">
        {#each history as msg, i (i)}
          <MessageBubble
            role={msg.role}
            content={msg.content}
            imageUrls={msg.imageUrls ?? []}
            isStreaming={streaming && i === history.length - 1 && msg.role === 'assistant'}
          />
        {/each}
      </div>
    {/if}
  </div>

  {#if errorMsg}
    <div class="error">{errorMsg}</div>
  {/if}

  {#if attachments.length > 0}
    <div class="attach-strip">
      {#each attachments as a, i (a.name + i)}
        <span class="chip">
          <span class="chip-icon">{a.type === 'image' ? '🖼' : a.type === 'audio' ? '🎙' : a.type === 'video' ? '🎞' : '📄'}</span>
          <span class="chip-name">{a.name}</span>
          <button class="chip-x" onclick={() => removeAttachment(i)} aria-label="Remove attachment">×</button>
        </span>
      {/each}
    </div>
  {/if}

  <div class="composer">
    <button class="attach" onclick={() => fileInput?.click()} disabled={streaming} title="Attach file">
      +
    </button>
    <input
      type="file"
      multiple
      bind:this={fileInput}
      style="display:none"
      onchange={(e) => {
        void handleFiles((e.currentTarget as HTMLInputElement).files);
        (e.currentTarget as HTMLInputElement).value = '';
      }}
    />
    <textarea
      placeholder="Message Loca…  (⌘↵ to send)"
      bind:value={input}
      onkeydown={onKeydown}
      rows="3"
      disabled={streaming}
    ></textarea>
    <button
      class="send"
      onclick={send}
      disabled={streaming || (!input.trim() && attachments.length === 0)}
    >
      {streaming ? 'Streaming…' : 'Send'}
    </button>
  </div>

  <!-- Session-only toggles, mirrored from SwiftUI's composer row. -->
  <div class="input-tools">
    <button
      class="tool"
      class:active={researchMode}
      disabled={lockdownMode}
      onclick={toggleResearch}
      title="Deep Dive — render full pages (not just snippets) and pull richer web context into the turn"
    >🌊 Deep Dive</button>
    <button
      class="tool"
      class:active={lockdownMode}
      onclick={toggleLockdown}
      title="Lockdown — disable all network tools"
    >🔒 Lockdown</button>
    {#if app.activeProject}
      <div class="partner-segment" role="group" aria-label="Partner mode">
        <button
          class="partner-label"
          title={`${app.activeProject.title} — click to exit research mode`}
          onclick={() => app.setActiveProject(null)}
        >📚 {app.activeProject.title} <span class="partner-x" aria-hidden="true">×</span></button>
        <button
          class="tool segment"
          class:active={app.partnerMode === 'default'}
          onclick={() => app.setPartnerMode('default')}
          title="Default partner — normal chat, biased to project sources"
        >Default</button>
        <button
          class="tool segment"
          class:active={app.partnerMode === 'critique'}
          onclick={() => app.setPartnerMode('critique')}
          title="Critique — devil's advocate, surfaces weak claims"
        >🥊 Critique</button>
        <button
          class="tool segment"
          class:active={app.partnerMode === 'teach'}
          onclick={() => app.setPartnerMode('teach')}
          title="Teach — step-by-step pedagogy"
        >🎓 Teach</button>
      </div>
    {/if}
  </div>

  {#if history.length > 0}
    <div class="conv-tools">
      <button onclick={newConversation}>New Conversation</button>
      {#if convId}<span class="conv-id">Saved as {convTitle || convId.slice(0, 8)}</span>{/if}
    </div>
  {/if}
</div>

<style>
  .chat {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--loca-color-bg);
    position: relative;
  }
  .chat.dragging { outline: 2px dashed var(--loca-color-accent); outline-offset: -8px; }
  .drop-overlay {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: none;
    z-index: 10;
  }
  .drop-card {
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-accent);
    color: var(--loca-color-accent);
    padding: 12px 24px;
    border-radius: var(--loca-radius-md);
    font-size: 14px;
    font-weight: 500;
  }
  .scroller {
    flex: 1;
    overflow-y: auto;
    padding: 20px 40px;
  }
  .empty {
    text-align: center;
    color: var(--loca-color-text-muted);
    padding: 80px 20px;
  }
  .empty h1 {
    font-size: 18px;
    font-weight: 600;
    margin: 0 0 6px;
    color: var(--loca-color-text);
  }
  .empty p { margin: 0; font-size: 13px; }
  .messages { display: flex; flex-direction: column; gap: 10px; }

  .error {
    margin: 0 40px 10px;
    padding: 8px 12px;
    border-radius: var(--loca-radius-sm);
    background: color-mix(in srgb, var(--loca-color-danger) 15%, transparent);
    color: var(--loca-color-danger);
    font-size: 12px;
  }

  .attach-strip {
    display: flex;
    gap: 6px;
    padding: 0 40px 8px;
    flex-wrap: wrap;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 8px;
    border: 1px solid var(--loca-color-border);
    border-radius: 999px;
    background: var(--loca-color-surface);
    font-size: 11px;
    color: var(--loca-color-text);
  }
  .chip-icon { font-size: 11px; }
  .chip-name { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chip-x {
    background: none;
    border: none;
    color: var(--loca-color-text-muted);
    font-size: 13px;
    cursor: pointer;
    padding: 0 2px;
  }

  .composer {
    display: flex;
    gap: 8px;
    padding: 12px 40px 8px;
    border-top: 1px solid var(--loca-color-border);
    background: var(--loca-color-bg);
    align-items: stretch;
  }
  .attach {
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-md);
    padding: 0 12px;
    font-size: 18px;
    color: var(--loca-color-text-muted);
    cursor: pointer;
  }
  .attach:hover:not(:disabled) { color: var(--loca-color-text); }
  .attach:disabled { opacity: 0.4; cursor: not-allowed; }
  textarea {
    flex: 1;
    resize: none;
    padding: 10px 12px;
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-md);
    background: var(--loca-color-surface);
    color: var(--loca-color-text);
    font-family: var(--loca-font-ui);
    font-size: 13px;
    line-height: 1.5;
  }
  textarea:focus {
    outline: 2px solid color-mix(in srgb, var(--loca-color-accent) 60%, transparent);
    outline-offset: -1px;
    border-color: var(--loca-color-accent);
  }
  .send {
    padding: 0 20px;
    background: var(--loca-color-accent);
    color: #fff;
    border: none;
    border-radius: var(--loca-radius-md);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
  }
  .send:hover:not(:disabled) { background: var(--loca-color-accent-hover); }
  .send:disabled { opacity: 0.45; cursor: not-allowed; }

  .conv-tools {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 40px 14px;
    font-size: 11px;
    color: var(--loca-color-text-muted);
  }
  .conv-tools button {
    background: none;
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    color: var(--loca-color-text);
    font-size: 11px;
    padding: 3px 10px;
    cursor: pointer;
  }
  .conv-tools button:hover { background: var(--loca-color-surface); }
  .conv-id { font-family: var(--loca-font-mono); }

  .input-tools {
    display: flex;
    gap: 6px;
    padding: 0 40px 10px;
    font-size: 11px;
  }
  .input-tools .tool {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: none;
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    color: var(--loca-color-text-muted);
    padding: 3px 10px;
    font-size: 11px;
    cursor: pointer;
  }
  .input-tools .tool:hover:not(:disabled) {
    color: var(--loca-color-text);
    background: rgba(127, 127, 127, 0.08);
  }
  .input-tools .tool.active {
    background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent);
    color: var(--loca-color-accent);
    border-color: var(--loca-color-accent);
  }
  .input-tools .tool:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .partner-segment {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-left: 10px;
    padding-left: 10px;
    border-left: 1px solid var(--loca-color-border);
  }
  .partner-label {
    background: none;
    border: 1px solid var(--loca-color-border);
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 10px;
    color: var(--loca-color-text);
    margin-right: 4px;
    white-space: nowrap;
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .partner-label:hover {
    border-color: var(--loca-color-danger);
    color: var(--loca-color-danger);
  }
  .partner-x {
    font-size: 11px;
    opacity: 0.6;
  }
  .partner-label:hover .partner-x { opacity: 1; }
</style>
