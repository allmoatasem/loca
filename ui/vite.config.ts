import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import path from 'node:path';

/**
 * Vite config for Loca's second UI.
 *
 * Build output lands in the FastAPI static tree so the bundled Loca.app
 * can serve it with no runtime Node dependency. `base: '/ui/'` makes the
 * produced `index.html` resolve its assets against `/ui/assets/...`,
 * matching the FastAPI mount in `src/proxy.py`.
 */
export default defineConfig({
  plugins: [svelte()],
  base: '/ui/',
  build: {
    outDir: path.resolve(__dirname, '../src/static/ui'),
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      // Forward API calls to a locally-running Loca backend during `npm run dev`.
      '/v1': 'http://localhost:8000',
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/system-stats': 'http://localhost:8000',
    },
  },
});
