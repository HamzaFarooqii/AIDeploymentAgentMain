import { describe, it, expect, vi } from 'vitest';
import { apiClient } from './client';

// This file runs under the "base-url-with-suffix" vitest project (see
// vitest.config.ts), which fixes VITE_API_BASE_URL to
// "https://example.com/api" (already has the /api suffix) before this
// project's server starts. That lets us exercise the "don't duplicate /api"
// branch of the base-URL resolution in src/api/client.ts.
describe('api client - base URL resolution (VITE_API_BASE_URL already has /api)', () => {
  it('does not duplicate /api when VITE_API_BASE_URL already ends with it', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => '{}',
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    await apiClient.getProjects();

    expect(fetchMock).toHaveBeenCalledWith(
      'https://example.com/api/upload/projects',
      expect.any(Object)
    );

    vi.unstubAllGlobals();
  });
});
