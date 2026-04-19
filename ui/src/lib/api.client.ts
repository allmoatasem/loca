/**
 * Thin fetch wrappers over Loca's FastAPI endpoints. Types come from
 * api.ts (generated from openapi.json via `make openapi`). Kept close to
 * the SwiftUI BackendClient so parity bugs surface early.
 */
import type { components } from './api';

export type Model = components['schemas']['Model'] extends never
  ? { name: string; format?: string; size_bytes?: number; path?: string }
  : components['schemas']['Model'];

export interface LocalModel {
  name: string;
  format?: string;        // 'mlx' | 'gguf' | …
  path?: string;
  size_bytes?: number;
  size_gb?: number;
  is_loaded?: boolean;
  context_length?: number | null;
  param_label?: string | null;
  supports_vision?: boolean;
}

export interface ConversationMeta {
  id: string;
  title: string;
  updated?: number;
  created?: number;
  model?: string;
  folder?: string | null;
  starred?: boolean;
}

export interface ConversationDetail extends ConversationMeta {
  messages: Array<{ role: 'user' | 'assistant' | 'system'; content: string }>;
}

async function jsonGet<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

export async function fetchLocalModels(): Promise<LocalModel[]> {
  const data = await jsonGet<{ models: LocalModel[] }>('/api/local-models');
  return data.models ?? [];
}

export async function fetchActiveModel(): Promise<string | null> {
  try {
    const data = await jsonGet<{ model?: string | null }>('/api/models/active');
    return data.model ?? null;
  } catch {
    return null;
  }
}

// ── LoRA adapters ─────────────────────────────────────────────────────────────

export interface Adapter {
  name: string;
  path: string;
  base_model: string;
  size_mb: number;
  rank?: number | null;
  alpha?: number | null;
  trained_at?: number | null;
}

export interface ActiveModelDetail {
  name: string | null;
  backend: string | null;
  api_base: string;
  running: boolean;
  adapter: string | null;
}

export async function fetchActiveModelDetail(): Promise<ActiveModelDetail | null> {
  try {
    return await jsonGet<ActiveModelDetail>('/api/models/active');
  } catch {
    return null;
  }
}

export async function fetchAdapters(modelName: string): Promise<Adapter[]> {
  const data = await jsonGet<{ adapters: Adapter[] }>(
    `/api/adapters?model=${encodeURIComponent(modelName)}`,
  );
  return data.adapters ?? [];
}

/** Activate a LoRA adapter on the given model (or pass `null` to clear). */
export async function activateAdapter(
  model: string, adapter: string | null,
): Promise<void> {
  const r = await fetch('/api/adapters/activate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, adapter }),
  });
  if (!r.ok) {
    let msg = `activate adapter → HTTP ${r.status}`;
    try {
      const body = await r.json() as { error?: string };
      if (body?.error) msg = body.error;
    } catch { /* swallow */ }
    throw new Error(msg);
  }
}

/** Apply a project's stored adapter binding to the currently loaded model.
 *  Server looks up `projects.adapter_name`, resolves the adapter path,
 *  and restarts mlx_lm.server. Pass-through error on incompatible
 *  adapter or missing adapter directory. No-op when no model is loaded. */
export async function activateProjectAdapter(projectId: string): Promise<void> {
  const r = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/activate-adapter`,
    { method: 'POST' },
  );
  if (!r.ok) {
    let msg = `project-adapter activate → HTTP ${r.status}`;
    try {
      const body = await r.json() as { error?: string };
      if (body?.error) msg = body.error;
    } catch { /* swallow */ }
    throw new Error(msg);
  }
}

export async function fetchConversations(): Promise<ConversationMeta[]> {
  const data = await jsonGet<{ conversations: ConversationMeta[] }>('/api/conversations');
  return data.conversations ?? [];
}

export async function fetchConversation(id: string): Promise<ConversationDetail> {
  return jsonGet<ConversationDetail>(`/api/conversations/${encodeURIComponent(id)}`);
}

export async function searchConversations(q: string): Promise<ConversationMeta[]> {
  const data = await jsonGet<{ conversations: ConversationMeta[] }>(
    `/api/search/conversations?q=${encodeURIComponent(q)}`,
  );
  return data.conversations ?? [];
}

export async function deleteConversation(id: string): Promise<void> {
  const r = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!r.ok) throw new Error(`delete ${id} → HTTP ${r.status}`);
}

export async function patchConversation(
  id: string,
  patch: { folder?: string | null; starred?: boolean },
): Promise<void> {
  const r = await fetch(`/api/conversations/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`patch ${id} → HTTP ${r.status}`);
}

export async function unloadModel(): Promise<void> {
  const r = await fetch('/api/models/unload', { method: 'POST' });
  if (!r.ok) throw new Error(`unload → HTTP ${r.status}`);
}

export async function loadModel(name: string, ctxSize: number): Promise<void> {
  const r = await fetch('/api/models/load', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, ctx_size: ctxSize }),
  });
  if (!r.ok) throw new Error(`load ${name} → HTTP ${r.status}`);
}

export async function deleteModel(name: string): Promise<void> {
  const r = await fetch(`/api/models/${encodeURIComponent(name)}`, { method: 'DELETE' });
  if (!r.ok) throw new Error(`delete model ${name} → HTTP ${r.status}`);
}

// ── Research Projects ────────────────────────────────────────────────────────

export type ProjectItemKind =
  | 'conv' | 'memory' | 'vault_chunk' | 'web_url' | 'quote' | 'vault_sync';

export interface Project {
  id: string;
  title: string;
  scope: string;
  notes: string;
  created: number;
  updated: number;
  item_count?: number;
  conv_count?: number;
  /** Preferred LoRA adapter to activate when this project becomes active. */
  adapter_name?: string | null;
}

export interface ProjectItem {
  id: string;
  project_id: string;
  kind: ProjectItemKind;
  ref_id: string | null;
  title: string;
  body: string;
  url: string | null;
  content_hash: string;
  created: number;
}

export interface ProjectWatch {
  id: string;
  project_id: string;
  sub_scope: string;
  schedule_minutes: number;
  last_run: number | null;
  last_snapshot_hash: string | null;
  created: number;
}

export interface ProjectDetail extends Project {
  items_count: number;
  conversations: ConversationMeta[];
  watches: ProjectWatch[];
}

export interface RelatedItem {
  kind: 'vault_chunk' | 'memory';
  title: string;
  snippet: string;
  score: number;
  vault_path?: string;
  rel_path?: string;
  memory_id?: string;
}

export async function fetchProjects(): Promise<Project[]> {
  const data = await jsonGet<{ projects: Project[] }>('/api/projects');
  return data.projects ?? [];
}

export async function fetchProject(id: string): Promise<ProjectDetail> {
  return jsonGet<ProjectDetail>(`/api/projects/${encodeURIComponent(id)}`);
}

export async function createProject(title: string, scope: string): Promise<Project> {
  const r = await fetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, scope }),
  });
  if (!r.ok) throw new Error(`create project → HTTP ${r.status}`);
  const data = await r.json();
  return data.project;
}

export async function patchProject(
  id: string,
  patch: { title?: string; scope?: string; notes?: string; adapter?: string | null },
): Promise<void> {
  const r = await fetch(`/api/projects/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`patch project ${id} → HTTP ${r.status}`);
}

export async function deleteProject(id: string): Promise<void> {
  const r = await fetch(`/api/projects/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!r.ok) throw new Error(`delete project ${id} → HTTP ${r.status}`);
}

export async function listProjectItems(
  id: string, kind?: ProjectItemKind,
): Promise<{ items: ProjectItem[]; total: number }> {
  const url = kind
    ? `/api/projects/${encodeURIComponent(id)}/items?kind=${kind}`
    : `/api/projects/${encodeURIComponent(id)}/items`;
  return jsonGet(url);
}

export async function addProjectItem(
  id: string,
  item: {
    kind: ProjectItemKind;
    title?: string; body?: string; url?: string; ref_id?: string;
  },
): Promise<{ id?: string; duplicate?: boolean }> {
  const r = await fetch(`/api/projects/${encodeURIComponent(id)}/items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(item),
  });
  if (!r.ok) throw new Error(`add project item → HTTP ${r.status}`);
  return r.json();
}

export async function deleteProjectItem(projectId: string, itemId: string): Promise<void> {
  const r = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/items/${encodeURIComponent(itemId)}`,
    { method: 'DELETE' },
  );
  if (!r.ok) throw new Error(`delete project item → HTTP ${r.status}`);
}

export async function attachConversationToProject(
  projectId: string, convId: string,
): Promise<void> {
  const r = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/attach-conversation`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_id: convId }),
    },
  );
  if (!r.ok) throw new Error(`attach conversation → HTTP ${r.status}`);
}

export async function detachConversationFromProject(
  projectId: string, convId: string,
): Promise<void> {
  const r = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/detach-conversation`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_id: convId }),
    },
  );
  if (!r.ok) throw new Error(`detach conversation → HTTP ${r.status}`);
}

export async function fetchRelated(
  projectId: string, limit = 10,
): Promise<RelatedItem[]> {
  const data = await jsonGet<{ items: RelatedItem[] }>(
    `/api/projects/${encodeURIComponent(projectId)}/related?limit=${limit}`,
  );
  return data.items ?? [];
}

export async function digDeeper(
  projectId: string, subScope: string, maxResults = 5,
): Promise<{ bookmarks: Array<{ id?: string; url: string; title: string; duplicate: boolean }>; total: number }> {
  const r = await fetch(`/api/projects/${encodeURIComponent(projectId)}/dig-deeper`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sub_scope: subScope, max_results: maxResults }),
  });
  if (!r.ok) throw new Error(`dig deeper → HTTP ${r.status}`);
  return r.json();
}

export async function syncVault(
  projectId: string, path: string,
): Promise<{ stored: number; skipped: number; total: number; synced_at: string }> {
  const r = await fetch(`/api/projects/${encodeURIComponent(projectId)}/sync-vault`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) throw new Error(`sync vault → HTTP ${r.status}`);
  return r.json();
}

export async function createWatch(
  projectId: string, subScope: string, scheduleMinutes: number,
): Promise<void> {
  const r = await fetch(`/api/projects/${encodeURIComponent(projectId)}/watches`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sub_scope: subScope, schedule_minutes: scheduleMinutes }),
  });
  if (!r.ok) throw new Error(`create watch → HTTP ${r.status}`);
}

export async function deleteWatch(projectId: string, watchId: string): Promise<void> {
  const r = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/watches/${encodeURIComponent(watchId)}`,
    { method: 'DELETE' },
  );
  if (!r.ok) throw new Error(`delete watch → HTTP ${r.status}`);
}

// Discover — HF search, repo files, downloads
export interface HFSearchHit { repo_id: string; downloads: number; likes: number }
export interface RepoFile    { name: string; size_gb: number }

export async function searchHF(q: string, format: 'gguf' | 'mlx', limit = 8): Promise<HFSearchHit[]> {
  const data = await jsonGet<{ models: HFSearchHit[] }>(
    `/api/hf-search?q=${encodeURIComponent(q)}&format=${format}&limit=${limit}`,
  );
  return data.models ?? [];
}

export async function listRepoFiles(repoId: string, format: 'gguf' | 'mlx'): Promise<RepoFile[]> {
  const data = await jsonGet<{ files: RepoFile[] }>(
    `/api/repo-files?repo_id=${encodeURIComponent(repoId)}&format=${format}`,
  );
  return data.files ?? [];
}

export async function startDownload(
  repoId: string,
  format: 'gguf' | 'mlx',
  filename?: string,
): Promise<string> {
  const r = await fetch('/api/models/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_id: repoId, format, filename }),
  });
  if (!r.ok) throw new Error(`download ${repoId} → HTTP ${r.status}`);
  const data = await r.json();
  if (!data.download_id) throw new Error('no download_id in response');
  return data.download_id as string;
}

export async function cancelDownload(id: string): Promise<void> {
  await fetch(`/api/models/download/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
}

// Vault Analyser
export interface DetectedVault { path: string; name: string }
export interface VaultStats {
  note_count: number; link_count: number; total_words: number;
  tag_count: number; top_tags: Array<{ tag: string; count: number }>;
  folder_count: number; daily_note_count: number;
  open_tasks: number; done_tasks: number;
}
export interface OrphanNote { rel_path: string; title: string; modified?: number }
export interface BrokenLink { source: string; target: string }
export interface DeadEnd    { rel_path: string; title: string }
export interface TagOrphan  { tag: string; count: number }
export interface LinkSuggestion { source: string; target: string; score: number }
export interface VaultAnalysis {
  stats: VaultStats;
  orphans: OrphanNote[];
  dead_ends: DeadEnd[];
  broken_links: BrokenLink[];
  tag_orphans: TagOrphan[];
  link_suggestions: LinkSuggestion[];
}
export interface VaultSearchHit { rel_path: string; title: string; snippet: string; score?: number }

export async function detectVaults(): Promise<DetectedVault[]> {
  const data = await jsonGet<{ vaults: DetectedVault[] }>('/api/vault/detect');
  return data.vaults ?? [];
}

export async function scanVault(path: string): Promise<{ ok: boolean } & Partial<VaultStats>> {
  const r = await fetch('/api/vault/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) throw new Error(`scan ${path} → HTTP ${r.status}`);
  return r.json();
}

export async function fetchVaultAnalysis(path: string): Promise<VaultAnalysis> {
  return jsonGet<VaultAnalysis>(`/api/vault/analysis?path=${encodeURIComponent(path)}`);
}

export async function searchVault(path: string, q: string, limit = 30): Promise<VaultSearchHit[]> {
  const data = await jsonGet<{ results: VaultSearchHit[] }>(
    `/api/vault/semantic-search?path=${encodeURIComponent(path)}&q=${encodeURIComponent(q)}&limit=${limit}`,
  );
  return data.results ?? [];
}

// ── Voice (STT + TTS + end-to-end voice chat) ────────────────────────────────
// Kept deliberately close to BackendClient.swift's voice methods; the
// Python routes (src/proxy.py §voice) are OpenAI-compatible so both clients
// hit the same endpoints.

export interface VoiceModel {
  name: string;
  repo_id: string;
  model_type: 'stt' | 'tts';
  downloaded: boolean;
  size_gb: number;
}

export interface VoiceConfig {
  stt_model: string;
  tts_model: string;
  tts_voice: string;
  tts_speed: number;
  auto_tts: boolean;
  models: VoiceModel[];
}

export interface VoiceChatResult {
  transcription: string;
  response: string;
  audio?: string | null;   // base64 WAV
  model?: string | null;
}

export async function fetchVoiceConfig(): Promise<VoiceConfig> {
  return jsonGet<VoiceConfig>('/api/voice/config');
}

export async function fetchVoiceModels(): Promise<VoiceModel[]> {
  const data = await jsonGet<{ models: VoiceModel[] }>('/api/voice/models');
  return data.models ?? [];
}

/** POST /v1/audio/transcriptions — multipart upload, returns text. */
export async function transcribeAudio(audio: Blob, filename = 'recording.wav'): Promise<string> {
  const form = new FormData();
  form.append('file', audio, filename);
  const r = await fetch('/v1/audio/transcriptions', { method: 'POST', body: form });
  if (!r.ok) throw new Error(`transcribe → HTTP ${r.status}`);
  const data = await r.json() as { text: string };
  return data.text;
}

/** POST /v1/audio/speech — returns a Blob of WAV bytes. */
export async function synthesizeSpeech(
  text: string,
  opts: { voice?: string; speed?: number } = {},
): Promise<Blob> {
  const payload: Record<string, unknown> = { input: text, response_format: 'wav' };
  if (opts.voice) payload.voice = opts.voice;
  if (opts.speed != null) payload.speed = opts.speed;
  const r = await fetch('/v1/audio/speech', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    let msg = `synthesize → HTTP ${r.status}`;
    try {
      const body = await r.json() as { error?: string };
      if (body?.error) msg = `TTS failed: ${body.error}`;
    } catch { /* swallow */ }
    throw new Error(msg);
  }
  return r.blob();
}

/** POST /api/voice/chat — end-to-end audio-in / audio-out turn. */
export async function voiceChat(
  audio: Blob,
  messages: Array<{ role: string; content: string }>,
): Promise<VoiceChatResult> {
  const form = new FormData();
  form.append('file', audio, 'recording.wav');
  form.append('messages', JSON.stringify(messages));
  const r = await fetch('/api/voice/chat', { method: 'POST', body: form });
  if (!r.ok) throw new Error(`voiceChat → HTTP ${r.status}`);
  return r.json() as Promise<VoiceChatResult>;
}
