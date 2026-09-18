import { configDefaults, defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Standalone vitest config (kept separate from vite.config.js/.ts to avoid
// ambiguity between those two files). Mirrors the React plugin setup used by
// the app's Vite config so JSX/TSX resolves the same way under test.
//
// Vite/Vitest resolve `import.meta.env.VITE_*` once per test "project" when
// that project's server starts, via Vite's `loadEnv(mode, envDir)` reading
// `.env.<mode>` files — NOT re-read at runtime. So `vi.stubEnv` (and even a
// project-level `test.env`, which only patches `process.env` after that
// resolution already happened) cannot change what src/api/client.ts sees for
// a custom `VITE_API_BASE_URL` mid-suite. To still exercise both branches of
// its base-URL resolution, the two tests that need a non-default
// `VITE_API_BASE_URL` live in their own dedicated files, each run under a
// small extra "project" below with its own `mode`, matching a `.env.<mode>`
// fixture file (`.env.base-url-custom` / `.env.base-url-with-suffix`) at the
// package root that actually sets the var before that project's server
// starts.
const CUSTOM_BASE_URL_FILE = 'src/api/client.baseUrlCustom.test.ts';
const BASE_URL_WITH_SUFFIX_FILE = 'src/api/client.baseUrlWithSuffix.test.ts';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    projects: [
      {
        extends: true,
        test: {
          name: 'default',
          exclude: [...configDefaults.exclude, CUSTOM_BASE_URL_FILE, BASE_URL_WITH_SUFFIX_FILE],
        },
      },
      {
        extends: true,
        mode: 'base-url-custom',
        test: {
          name: 'base-url-custom',
          include: [CUSTOM_BASE_URL_FILE],
        },
      },
      {
        extends: true,
        mode: 'base-url-with-suffix',
        test: {
          name: 'base-url-with-suffix',
          include: [BASE_URL_WITH_SUFFIX_FILE],
        },
      },
    ],
  },
});
