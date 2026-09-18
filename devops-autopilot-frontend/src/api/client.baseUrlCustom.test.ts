import { describe, it, expect, vi } from 'vitest';
import { apiClient } from './client';

// This file runs under the "base-url-custom" vitest project (see
// vitest.config.ts), which fixes VITE_API_BASE_URL to "https://example.com"
// (no trailing /api) before this project's server starts. That lets us
// exercise the "VITE_API_BASE_URL is set" branch of the base-URL resolution
// in src/api/client.ts, which import.meta.env-based stubbing cannot do
// mid-suite (see client.test.ts for why).
describe('api client - base URL resolution (custom VITE_API_BASE_URL)', () => {
  it('appends /api when VITE_API_BASE_URL has no /api suffix', async () => {
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
