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

export async function fetchConversations(): Promise<ConversationMeta[]> {
  const data = await jsonGet<{ conversations: ConversationMeta[] }>('/api/conversations');
  return data.conversations ?? [];
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
