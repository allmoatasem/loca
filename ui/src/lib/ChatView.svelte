<!--
  ChatView — main conversation pane. Twins SwiftUI's ChatView.swift.

  Phase 4 scope: message list + input + streaming SSE against
  /v1/chat/completions with <think> splitting and basic Markdown.

  Deferred to Phase 4b: attachments (images, docs), Prism syntax
  highlighting, copy-message buttons, conversation save/load,
  voice mode, tool-call UI, research mode toggle.
-->
<script lang="ts">
  import { app } from './app-store.svelte';
  import MessageBubble, { type Role } from './MessageBubble.svelte';
  import { tick } from 'svelte';

  interface Message {
    role: Role;
    content: string;
  }

  let history = $state<Message[]>([]);
  let input   = $state<string>('');
  let streaming = $state<boolean>(false);
  let errorMsg  = $state<string | null>(null);
  let scroller: HTMLDivElement | undefined = $state();

  async function scrollToBottom(): Promise<void> {
    await tick();
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }

  async function send(): Promise<void> {
    const text = input.trim();
    if (!text || streaming) return;
    errorMsg = null;

    history = [...history, { role: 'user', content: text }];
    input = '';
    streaming = true;
    await scrollToBottom();

    // Placeholder assistant bubble we stream into
    history = [...history, { role: 'assistant', content: '' }];
    const assistantIdx = history.length - 1;

    try {
      const resp = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: app.selectedCapability,
          messages: history.slice(0, -1).map((m) => ({ role: m.role, content: m.content })),
          stream: true,
          num_ctx: app.contextWindow,
        }),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let assembled = '';

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
            const delta = parsed?.choices?.[0]?.delta?.content;
            if (typeof delta === 'string' && delta.length > 0) {
              assembled += delta;
              const updated = [...history];
              updated[assistantIdx] = { role: 'assistant', content: assembled };
              history = updated;
              await scrollToBottom();
            }
          } catch { /* ignore malformed SSE lines */ }
        }
      }
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
      // Drop the empty assistant bubble so the error stands alone
      if (history[assistantIdx]?.content === '') {
        history = history.slice(0, assistantIdx);
      }
    } finally {
      streaming = false;
    }
  }

  function onKeydown(e: KeyboardEvent): void {
    // Cmd/Ctrl+Enter to send; plain Enter inserts a newline like most chat UIs.
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      void send();
    }
  }
</script>

<div class="chat">
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
            isStreaming={streaming && i === history.length - 1 && msg.role === 'assistant'}
          />
        {/each}
      </div>
    {/if}
  </div>

  {#if errorMsg}
    <div class="error">{errorMsg}</div>
  {/if}

  <div class="composer">
    <textarea
      placeholder="Message Loca…  (⌘↵ to send)"
      bind:value={input}
      onkeydown={onKeydown}
      rows="3"
      disabled={streaming}
    ></textarea>
    <button onclick={send} disabled={streaming || !input.trim()}>
      {streaming ? 'Streaming…' : 'Send'}
    </button>
  </div>
</div>

<style>
  .chat {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--loca-color-bg);
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
  .messages {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .error {
    margin: 0 40px 10px;
    padding: 8px 12px;
    border-radius: var(--loca-radius-sm);
    background: color-mix(in srgb, var(--loca-color-danger) 15%, transparent);
    color: var(--loca-color-danger);
    font-size: 12px;
  }
  .composer {
    display: flex;
    gap: 8px;
    padding: 12px 40px 16px;
    border-top: 1px solid var(--loca-color-border);
    background: var(--loca-color-bg);
  }
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
  button {
    align-self: stretch;
    padding: 0 20px;
    background: var(--loca-color-accent);
    color: #fff;
    border: none;
    border-radius: var(--loca-radius-md);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
  }
  button:hover:not(:disabled) { background: var(--loca-color-accent-hover); }
  button:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
