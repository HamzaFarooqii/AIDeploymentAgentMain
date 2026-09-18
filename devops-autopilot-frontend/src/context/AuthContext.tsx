import React, { useState, useCallback, useEffect } from 'react';
import { User } from '../types/api';
import { apiClient } from '../api/client';
import { AuthContext, AuthContextType } from './useAuth';

interface AuthProviderProps {
  children: React.ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const initAuth = async () => {
      const token = apiClient.getToken();
      if (token) {
        try {
          const isValid = await apiClient.verifyToken();
          if (isValid) {
            const response = await apiClient.getCurrentUser();
            setUser(response.user);
          } else {
            apiClient.clearToken();
          }
        } catch (err) {
          apiClient.clearToken();
          setError(err instanceof Error ? err.message : 'Auth initialization failed');
        }
      }
      setIsLoading(false);
    };

    initAuth();
  }, []);

  const register = useCallback(async (username: string, email: string, password: string, fullName?: string) => {
    setIsLoading(true);
    setError(null);
    try {
      await apiClient.register({ username, email, password, full_name: fullName });
      setIsLoading(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Registration failed';
      setError(message);
      setIsLoading(false);
      throw err;
    }
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiClient.login({ username, password });
      setUser(response.user);
      setIsLoading(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Login failed';
      setError(message);
      setIsLoading(false);
      throw err;
    }
  }, []);

  const logout = useCallback(() => {
    setUser(null);
    apiClient.clearToken();
    setError(null);
  }, []);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  const value: AuthContextType = {
    user,
    isLoading,
    isAuthenticated: !!user,
    error,
    register,
    login,
    logout,
    clearError,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};