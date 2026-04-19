/**
 * Browser-side voice recorder with voice activity detection (VAD).
 *
 * Twin of Loca-SwiftUI/Sources/Loca/Voice/AudioRecorder.swift — same two-
 * threshold hysteresis, same silence timeout, same noise-floor calibration
 * during the first ~0.5s of listening. Output is a mono 16-bit PCM WAV
 * blob that the backend's /v1/audio/transcriptions endpoint expects.
 *
 * Flow: start() → listening → speech crosses speechStart → recording →
 * RMS stays below silence threshold for silenceDuration → processing →
 * onComplete(wav). resumeListening() restarts the cycle after the LLM
 * response finishes so hands-free conversation loops until stop().
 */

export type VoiceState = 'idle' | 'listening' | 'recording' | 'processing';

export interface VoiceRecorderCallbacks {
  /** State changed — use for UI updates. */
  onState?: (state: VoiceState) => void;
  /** Normalised RMS (0–1) for waveform display. Called every audio frame. */
  onLevel?: (level: number) => void;
  /** VAD produced a completed utterance — deliver the WAV blob. */
  onComplete?: (wav: Blob) => void;
  /** Fatal recorder error (mic denied, device missing, etc). */
  onError?: (message: string) => void;
}

export class VoiceRecorder {
  // VAD tuning — kept in lockstep with AudioRecorder.swift so tone feels
  // identical across clients.
  private readonly speechStartThreshold = 0.012;
  private readonly silenceThreshold = 0.008;
  private readonly silenceDuration = 1500;   // ms
  private readonly minSpeechDuration = 400;  // ms
  private readonly maxRecordingDuration = 30_000; // ms

  private stream: MediaStream | null = null;
  private audioCtx: AudioContext | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;

  private buffers: Float32Array[] = [];
  private sampleRate = 16_000;

  private state: VoiceState = 'idle';
  private speechStartTime: number | null = null;
  private lastSpeechTime: number | null = null;
  private silenceInterval: ReturnType<typeof setInterval> | null = null;

  private noiseFloor = 0;
  private noiseSamples: number[] = [];
  private isCalibrating = false;

  constructor(private callbacks: VoiceRecorderCallbacks = {}) {}

  get currentState(): VoiceState { return this.state; }
  get isActive(): boolean { return this.state === 'listening' || this.state === 'recording'; }

  async start(): Promise<void> {
    if (this.state !== 'idle') return;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch (err) {
      this.callbacks.onError?.(
        `Microphone access denied or unavailable: ${(err as Error).message}`,
      );
      return;
    }

    // Safari + older Chromium still expose AudioContext under webkit prefix.
    const Ctx: typeof AudioContext =
      (window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext) as typeof AudioContext;
    const ctx = new Ctx();
    this.audioCtx = ctx;
    this.sampleRate = ctx.sampleRate;

    this.source = ctx.createMediaStreamSource(this.stream);
    // ScriptProcessorNode is deprecated but universally supported; AudioWorklet
    // would require a separate worklet file served as a module. For a 16-bit
    // mono path at 16-48kHz, the perf cost is negligible and reliability
    // matters more than being on the bleeding edge of the WebAudio spec.
    this.processor = ctx.createScriptProcessor(4096, 1, 1);
    this.processor.onaudioprocess = (ev) => {
      const input = ev.inputBuffer.getChannelData(0);
      const rms = computeRMS(input);
      this.handleFrame(input, rms);
    };

    this.source.connect(this.processor);
    this.processor.connect(ctx.destination);

    this.resetForListening();
    this.state = 'listening';
    this.callbacks.onState?.(this.state);

    this.silenceInterval = setInterval(() => this.checkSilenceTimeout(), 200);
  }

  stop(): void {
    if (this.silenceInterval) {
      clearInterval(this.silenceInterval);
      this.silenceInterval = null;
    }
    try { this.processor?.disconnect(); } catch { /* no-op */ }
    try { this.source?.disconnect(); } catch { /* no-op */ }
    try { this.audioCtx?.close(); } catch { /* no-op */ }
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.audioCtx = null;
    this.source = null;
    this.processor = null;
    this.buffers = [];
    this.speechStartTime = null;
    this.lastSpeechTime = null;
    this.state = 'idle';
    this.callbacks.onState?.(this.state);
    this.callbacks.onLevel?.(0);
  }

  /** Restart VAD after a transcription/response without closing the mic. */
  resumeListening(): void {
    if (!this.audioCtx || this.state === 'idle') return;
    this.resetForListening();
    this.state = 'listening';
    this.callbacks.onState?.(this.state);
  }

  private resetForListening(): void {
    this.buffers = [];
    this.speechStartTime = null;
    this.lastSpeechTime = null;
    this.noiseFloor = 0;
    this.noiseSamples = [];
    this.isCalibrating = true;
  }

  private handleFrame(samples: Float32Array, rms: number): void {
    // First ~0.5s: measure ambient RMS so thresholds adapt to quiet vs
    // noisy environments. The raw constants are a floor, not a ceiling.
    if (this.isCalibrating) {
      this.noiseSamples.push(rms);
      if (this.noiseSamples.length >= 5) {
        this.noiseFloor =
          this.noiseSamples.reduce((a, b) => a + b, 0) / this.noiseSamples.length;
        this.isCalibrating = false;
      }
      this.callbacks.onLevel?.(0);
      return;
    }

    const speechStart = Math.max(this.speechStartThreshold, this.noiseFloor * 2.5);
    const silence = Math.max(this.silenceThreshold, this.noiseFloor * 1.5);

    const level = Math.min(rms / Math.max(speechStart * 3, 0.01), 1);
    this.callbacks.onLevel?.(level);

    const isSpeech = rms > speechStart;
    const isSilence = rms < silence;

    if (this.state === 'listening') {
      if (isSpeech) {
        this.state = 'recording';
        this.callbacks.onState?.(this.state);
        this.speechStartTime = performance.now();
        this.lastSpeechTime = this.speechStartTime;
        this.buffers = [new Float32Array(samples)];
      }
      return;
    }

    if (this.state === 'recording') {
      this.buffers.push(new Float32Array(samples));
      if (!isSilence) {
        this.lastSpeechTime = performance.now();
      }
    }
  }

  private checkSilenceTimeout(): void {
    if (this.state !== 'recording') return;
    if (this.speechStartTime == null || this.lastSpeechTime == null) return;

    const now = performance.now();
    const silenceSoFar = now - this.lastSpeechTime;
    const totalDuration = now - this.speechStartTime;
    const speechDuration = this.lastSpeechTime - this.speechStartTime;

    const hitSilence =
      silenceSoFar >= this.silenceDuration && speechDuration >= this.minSpeechDuration;
    const hitCap = totalDuration >= this.maxRecordingDuration;

    if (hitSilence || hitCap) {
      this.state = 'processing';
      this.callbacks.onState?.(this.state);
      const wav = encodeWav(this.buffers, this.sampleRate);
      this.buffers = [];
      this.speechStartTime = null;
      this.lastSpeechTime = null;
      this.callbacks.onLevel?.(0);
      this.callbacks.onComplete?.(wav);
    }
  }
}

function computeRMS(samples: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / Math.max(samples.length, 1));
}

/** Float32 frames → 16-bit PCM WAV blob (mono, `sampleRate` Hz). */
function encodeWav(frames: Float32Array[], sampleRate: number): Blob {
  let totalSamples = 0;
  for (const f of frames) totalSamples += f.length;

  const dataSize = totalSamples * 2;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  writeAscii(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeAscii(view, 8, 'WAVE');
  writeAscii(view, 12, 'fmt ');
  view.setUint32(16, 16, true);          // PCM header size
  view.setUint16(20, 1, true);           // format = PCM
  view.setUint16(22, 1, true);           // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true);           // block align
  view.setUint16(34, 16, true);          // bits per sample
  writeAscii(view, 36, 'data');
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (const frame of frames) {
    for (let i = 0; i < frame.length; i++) {
      const s = Math.max(-1, Math.min(1, frame[i]));
      view.setInt16(offset, s * 0x7fff, true);
      offset += 2;
    }
  }

  return new Blob([buffer], { type: 'audio/wav' });
}

function writeAscii(view: DataView, offset: number, str: string): void {
  for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
}
