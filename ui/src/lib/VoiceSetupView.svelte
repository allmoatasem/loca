<!--
  VoiceSetupView — Svelte twin of Loca-SwiftUI/.../VoiceSetupSheet.swift.

  Shown when the user enables voice mode but the STT/TTS models haven't
  been downloaded yet. The backend lazy-downloads them on first use, so
  the primary button just enables voice mode and the real download
  happens during the first transcription call.
-->
<script lang="ts">
  import { app } from './app-store.svelte';

  interface Props {
    onClose?: () => void;
  }
  let { onClose }: Props = $props();

  $effect(() => { void app.refreshVoiceConfig(); });

  const sttReady = $derived(
    app.voiceConfig?.models?.find((m) => m.model_type === 'stt')?.downloaded ?? false,
  );
  const ttsReady = $derived(
    app.voiceConfig?.models?.find((m) => m.model_type === 'tts')?.downloaded ?? false,
  );
  const allReady = $derived(sttReady && ttsReady);

  function enableAndClose(): void {
    app.setVoiceMode(true);
    onClose?.();
  }

  function close(): void {
    onClose?.();
  }

  function onKey(e: KeyboardEvent): void {
    if (e.key === 'Escape') close();
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="backdrop" onclick={close} onkeydown={onKey} role="presentation">
  <section
    class="panel"
    role="dialog"
    aria-modal="true"
    aria-label="Voice Mode Setup"
    onclick={(e) => e.stopPropagation()}
  >
    <header>
      <div class="icon" aria-hidden="true">🎙️</div>
      <h2>Voice Mode Setup</h2>
      <p class="lede">
        Voice mode needs two small speech models. They run locally on your machine —
        nothing leaves the device.
      </p>
    </header>

    <div class="models">
      <div class="model-row" class:ready={sttReady}>
        <div class="m-icon" aria-hidden="true">👂</div>
        <div class="m-text">
          <div class="m-name">Whisper Large v3 Turbo</div>
          <div class="m-desc">Speech-to-text — transcribes your voice</div>
        </div>
        <div class="m-status">
          {#if sttReady}<span class="check">Downloaded</span>{:else}<span class="pending">Not downloaded</span>{/if}
        </div>
      </div>

      <div class="model-row" class:ready={ttsReady}>
        <div class="m-icon" aria-hidden="true">🔊</div>
        <div class="m-text">
          <div class="m-name">Kokoro 82M</div>
          <div class="m-desc">Text-to-speech — speaks responses aloud</div>
        </div>
        <div class="m-status">
          {#if ttsReady}<span class="check">Downloaded</span>{:else}<span class="pending">Not downloaded</span>{/if}
        </div>
      </div>
    </div>

    <div class="actions">
      {#if allReady}
        <button class="primary" onclick={enableAndClose}>Enable Voice Mode</button>
      {:else}
        <button class="primary" onclick={enableAndClose}>
          Download Recommended Models
        </button>
        <p class="hint">
          Models download automatically on first use. Expect a one-time delay of 30–60s.
        </p>
      {/if}
      <button class="secondary" onclick={close}>Cancel</button>
    </div>
  </section>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 100;
    background: rgba(0, 0, 0, 0.35);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .panel {
    width: 100%;
    max-width: 460px;
    background: var(--loca-color-surface);
    color: var(--loca-color-text);
    border-radius: var(--loca-radius-md);
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.25);
    padding: 28px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  header { display: flex; flex-direction: column; align-items: center; gap: 6px; }
  .icon { font-size: 32px; }
  h2 { font-size: 18px; font-weight: 600; margin: 0; }
  .lede { font-size: 12px; color: var(--loca-color-text-muted); text-align: center; margin: 0; }

  .models { display: flex; flex-direction: column; gap: 10px; }
  .model-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px;
    background: var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
  }
  .model-row.ready { background: color-mix(in srgb, var(--loca-color-accent) 10%, var(--loca-color-border)); }
  .m-icon { font-size: 20px; width: 28px; text-align: center; }
  .m-text { flex: 1; min-width: 0; }
  .m-name { font-size: 13px; font-weight: 500; }
  .m-desc { font-size: 11px; color: var(--loca-color-text-muted); }
  .m-status .check { font-size: 11px; color: var(--loca-color-accent); font-weight: 500; }
  .m-status .pending { font-size: 11px; color: #b38900; }

  .actions { display: flex; flex-direction: column; gap: 8px; align-items: stretch; }
  .primary {
    background: var(--loca-color-accent);
    color: #fff;
    border: none;
    padding: 10px 16px;
    border-radius: var(--loca-radius-sm);
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .primary:hover { background: var(--loca-color-accent-hover); }
  .secondary {
    background: none;
    border: none;
    color: var(--loca-color-text-muted);
    font-size: 12px;
    padding: 6px;
    cursor: pointer;
  }
  .secondary:hover { color: var(--loca-color-text); }
  .hint { font-size: 11px; color: var(--loca-color-text-muted); margin: 0; text-align: center; }
</style>
