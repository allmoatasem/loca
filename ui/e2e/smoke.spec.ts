/**
 * Smoke tests for the Svelte UI. Covers the minimum set of flows
 * that have to work before any PR can merge. If these fail, the UI
 * is broken at a level where more detailed tests are moot.
 *
 * Everything is mocked via `page.route` — no real backend, no real
 * model. See `helpers.ts` for the stub surface.
 */

import { expect, test } from '@playwright/test';
import { mockBackend } from './helpers';

test.beforeEach(async ({ page }) => {
  await mockBackend(page);
});

test('sidebar renders with core controls', async ({ page }) => {
  await page.goto('/ui/');
  // Sidebar title is the New Conversation button.
  await expect(page.getByRole('button', { name: /New Conversation/i })).toBeVisible();
  // Footer primaries — Research + Memory are pinned, the rest live
  // behind the More menu.
  await expect(page.getByRole('button', { name: /Research/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /Memory/i })).toBeVisible();
});

test('no-model-loaded banner shows above composer', async ({ page }) => {
  await mockBackend(page, { activeModel: null });
  await page.goto('/ui/');
  await expect(page.getByText(/No model loaded/i)).toBeVisible();
  // Composer input stays active (Swift parity — friendly banner, not
  // a disabled textarea).
  const textarea = page.locator('textarea');
  await expect(textarea).toBeEnabled();
});

test('RAM indicator renders in sidebar footer', async ({ page }) => {
  await mockBackend(page, { ramUsedGb: 24.5, ramTotalGb: 96.0 });
  await page.goto('/ui/');
  // Format is `NN.N / NN GB` per the StatsBar fixture.
  await expect(page.getByText(/24\.5\s*\/\s*96\s*GB/i)).toBeVisible({ timeout: 12_000 });
});

test('Memory panel opens and lists memories', async ({ page }) => {
  await mockBackend(page, {
    memories: [
      { id: 'm1', content: 'user prefers fastapi', created: 1.71e9, type: 'user_fact' },
      { id: 'm2', content: 'deploys on fridays',    created: 1.71e9, type: 'user_fact' },
    ],
  });
  await page.goto('/ui/memory');
  await expect(page.getByText('user prefers fastapi')).toBeVisible();
  await expect(page.getByText('deploys on fridays')).toBeVisible();
});

test('Memory panel shows bulk-delete controls', async ({ page }) => {
  await mockBackend(page, {
    memories: [
      { id: 'm1', content: 'anything', created: 1.71e9, type: 'user_fact' },
    ],
  });
  await page.goto('/ui/memory');
  // Bulk-delete dropdown exists with the expected options.
  const bulk = page.locator('select').filter({ hasText: /Bulk delete/i });
  await expect(bulk).toBeVisible();
  await expect(bulk.locator('option', { hasText: /Wipe everything/i })).toHaveCount(1);
});

test('Manage Models panel opens', async ({ page }) => {
  await mockBackend(page, {
    localModels: [{ name: 'qwen-7b', format: 'mlx' }],
  });
  await page.goto('/ui/manage-models');
  await expect(page.getByText(/Manage Models/i).first()).toBeVisible();
});

test('Obsidian Watcher panel renders the empty state', async ({ page }) => {
  await mockBackend(page, { watchedVaults: [] });
  await page.goto('/ui/vault');
  await expect(page.getByText(/Obsidian Watcher/i).first()).toBeVisible();
});
