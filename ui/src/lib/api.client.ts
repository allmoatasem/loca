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
