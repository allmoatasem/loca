/**
 * Shared test helpers — every spec starts by calling `mockBackend()`
 * which intercepts the backend routes the Svelte UI calls on load +
 * during interaction. We never hit a real FastAPI server or model
 * from Playwright: tests are fast (no inference) and deterministic
 * (no depending-on-data-in-your-memory-store).
 */

import { type Page, type Route } from '@playwright/test';

/** Canned payloads the smoke suite expects; swap inside a test via
 *  `page.route` if you need a different response for one check. */
export interface MockState {
  conversations?: unknown[];
  memories?: { id: string; content: string; created: number; type: string }[];
  localModels?: { name: string; format?: string }[];
  activeModel?: { name: string; adapter?: string | null } | null;
  voiceConfig?: object | null;
  projects?: unknown[];
  watchedVaults?: unknown[];
  ramUsedGb?: number;
  ramTotalGb?: number;
}

export async function mockBackend(page: Page, state: MockState = {}): Promise<void> {
  const json = (route: Route, body: unknown): Promise<void> =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });

  // Conversations
  await page.route('**/api/conversations', (route) =>
    json(route, { conversations: state.conversations ?? [] }),
  );
  await page.route('**/api/conversations/*', (route) =>
    json(route, { id: 'mock', title: 'Mock', messages: [] }),
  );
  // Memories
  await page.route('**/api/memories?**', (route) => {
    const mems = state.memories ?? [];
    return json(route, {
      memories: mems,
      total: mems.length,
      limit: 50,
      offset: 0,
    });
  });
  // Models
  await page.route('**/api/local-models', (route) =>
    json(route, { models: state.localModels ?? [] }),
  );
  await page.route('**/api/models/active', (route) =>
    json(route, state.activeModel ?? null),
  );
  await page.route('**/api/adapters?**', (route) =>
    json(route, { adapters: [] }),
  );
  // Voice
  await page.route('**/api/voice/config', (route) =>
    json(route, state.voiceConfig ?? {
      stt_model: '', tts_model: '', tts_voice: '', tts_speed: 1.0,
      auto_tts: false, models: [],
    }),
  );
  // Projects
  await page.route('**/api/projects', (route) =>
    json(route, { projects: state.projects ?? [] }),
  );
  // Obsidian Watcher
  await page.route('**/api/obsidian/watched', (route) =>
    json(route, { vaults: state.watchedVaults ?? [] }),
  );
  await page.route('**/api/vault/detect', (route) =>
    json(route, { vaults: [] }),
  );
  // System stats
  await page.route('**/system-stats', (route) =>
    json(route, {
      ram_used_gb: state.ramUsedGb ?? 12.3,
      ram_total_gb: state.ramTotalGb ?? 96.0,
    }),
  );
  // Anything else the UI polls we don't care about — let it 404 so
  // flakes show up in the trace rather than being silently resolved.
}
