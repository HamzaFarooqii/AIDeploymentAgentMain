import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// `API_BASE_URL` in src/api/client.ts is resolved once, when its module is
// first evaluated, from `import.meta.env.VITE_API_BASE_URL`. Vite/Vitest
// resolve `import.meta.env.VITE_*` values once per test "project" server
// startup (see `loadEnv`) rather than live from `process.env`, so
// `vi.stubEnv` cannot change what this module sees mid-suite. This file only
// exercises the default (unset) case, which matches this project's actual
// env. The "VITE_API_BASE_URL is set" branches are covered by
// client.baseUrlCustom.test.ts and client.baseUrlWithSuffix.test.ts, which
// each run under a dedicated vitest "project" (see vitest.config.ts) that
// fixes the env var before that project's server starts.

function okJsonResponse(body: unknown = {}): Response {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe('api client - base URL resolution', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('falls back to http://localhost:8000/api when VITE_API_BASE_URL is unset', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJsonResponse());
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await apiClient.getProjects();

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/upload/projects',
      expect.any(Object)
    );
  });
});

describe('api client - Authorization header', () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('attaches "Authorization: Bearer <token>" when a token exists in localStorage', async () => {
    localStorage.setItem('auth_token', 'secret-token');
    const fetchMock = vi.fn().mockResolvedValue(okJsonResponse());
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await apiClient.getProjects();

    const [, options] = fetchMock.mock.calls[0];
    expect(options.headers.Authorization).toBe('Bearer secret-token');
  });

  it('omits the Authorization header when there is no token in localStorage', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJsonResponse());
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await apiClient.getProjects();

    const [, options] = fetchMock.mock.calls[0];
    expect(options.headers.Authorization).toBeUndefined();
  });

  it('stores the token from login() and includes it on subsequent requests', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        okJsonResponse({ access_token: 'new-token', token_type: 'bearer', user: {} })
      )
      .mockResolvedValueOnce(okJsonResponse());
    vi.stubGlobal('fetch', fetchMock);

    const { apiClient } = await import('./client');
    await apiClient.login({ username: 'bob', password: 'pw' });
    await apiClient.getProjects();

    expect(localStorage.getItem('auth_token')).toBe('new-token');
    const [, secondCallOptions] = fetchMock.mock.calls[1];
    expect(secondCallOptions.headers.Authorization).toBe('Bearer new-token');
  });
});
