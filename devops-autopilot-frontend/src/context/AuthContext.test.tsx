import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AuthProvider, useAuth } from './AuthContext';
import { apiClient } from '../api/client';
import type { User } from '../types/api';

// The AuthContext talks to the backend exclusively through `apiClient`, so we
// mock that module entirely and assert against the mocked calls. The
// `clearToken` mock mimics the real implementation's localStorage side
// effect so the logout test can assert against localStorage directly.
vi.mock('../api/client', () => ({
  apiClient: {
    getToken: vi.fn(),
    verifyToken: vi.fn(),
    getCurrentUser: vi.fn(),
    clearToken: vi.fn(() => localStorage.removeItem('auth_token')),
    register: vi.fn(),
    login: vi.fn(),
  },
}));

const mockedApiClient = vi.mocked(apiClient, { deep: true });

const testUser: User = {
  user_id: 'u1',
  username: 'bob',
  email: 'bob@example.com',
  is_active: true,
  created_at: '2024-01-01T00:00:00Z',
};

function TestConsumer() {
  const { user, isAuthenticated, isLoading, error, login, register, logout } = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(isLoading)}</span>
      <span data-testid="authed">{String(isAuthenticated)}</span>
      <span data-testid="username">{user?.username ?? 'none'}</span>
      <span data-testid="error">{error ?? 'none'}</span>
      {/* AuthContext's login/register intentionally rethrow after recording
          `error` state, so real callers (e.g. a login form) can also react
          to the rejection. Swallow it here the same way a real caller would,
          so a deliberately-failing login in a test doesn't surface as an
          unhandled promise rejection. */}
      <button onClick={() => { login('bob', 'pw').catch(() => {}); }}>do-login</button>
      <button onClick={() => { register('bob', 'bob@example.com', 'pw').catch(() => {}); }}>do-register</button>
      <button onClick={() => logout()}>do-logout</button>
    </div>
  );
}

async function renderAuth() {
  render(
    <AuthProvider>
      <TestConsumer />
    </AuthProvider>
  );
  // Wait out the initial `verifyToken`/`getCurrentUser` bootstrap effect.
  await waitFor(() => expect(screen.getByTestId('loading').textContent).toBe('false'));
}

describe('AuthContext', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mockedApiClient.getToken.mockReturnValue(null);
  });

  it('starts unauthenticated with no user when there is no stored token', async () => {
    await renderAuth();

    expect(screen.getByTestId('authed').textContent).toBe('false');
    expect(screen.getByTestId('username').textContent).toBe('none');
    expect(mockedApiClient.verifyToken).not.toHaveBeenCalled();
  });

  it('login() sets the authenticated user from the API response', async () => {
    mockedApiClient.login.mockResolvedValue({
      access_token: 'tok123',
      token_type: 'bearer',
      user: testUser,
    });
    const user = userEvent.setup();
    await renderAuth();

    await user.click(screen.getByText('do-login'));

    await waitFor(() => expect(screen.getByTestId('authed').textContent).toBe('true'));
    expect(screen.getByTestId('username').textContent).toBe('bob');
    expect(mockedApiClient.login).toHaveBeenCalledWith({ username: 'bob', password: 'pw' });
  });

  it('login() surfaces an error message and stays unauthenticated on failure', async () => {
    mockedApiClient.login.mockRejectedValue(new Error('Invalid credentials'));
    const user = userEvent.setup();
    await renderAuth();

    await user.click(screen.getByText('do-login'));

    await waitFor(() => expect(screen.getByTestId('error').textContent).toBe('Invalid credentials'));
    expect(screen.getByTestId('authed').textContent).toBe('false');
  });

  it('register() calls the API with the expected payload without authenticating', async () => {
    mockedApiClient.register.mockResolvedValue({ success: true, message: 'registered' });
    const user = userEvent.setup();
    await renderAuth();

    await user.click(screen.getByText('do-register'));

    await waitFor(() =>
      expect(mockedApiClient.register).toHaveBeenCalledWith({
        username: 'bob',
        email: 'bob@example.com',
        password: 'pw',
        full_name: undefined,
      })
    );
    expect(screen.getByTestId('authed').textContent).toBe('false');
  });

  it('logout() clears the user state and removes the stored token from localStorage', async () => {
    mockedApiClient.login.mockResolvedValue({
      access_token: 'tok123',
      token_type: 'bearer',
      user: testUser,
    });
    localStorage.setItem('auth_token', 'tok123');
    const user = userEvent.setup();
    await renderAuth();
    await user.click(screen.getByText('do-login'));
    await waitFor(() => expect(screen.getByTestId('authed').textContent).toBe('true'));

    await user.click(screen.getByText('do-logout'));

    expect(screen.getByTestId('authed').textContent).toBe('false');
    expect(screen.getByTestId('username').textContent).toBe('none');
    expect(mockedApiClient.clearToken).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('auth_token')).toBeNull();
  });
});
