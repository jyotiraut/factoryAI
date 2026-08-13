import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { setSessionExpiredHandler } from "../api/client";
import * as api from "../api/endpoints";
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "../api/tokenStore";
import type { CurrentUser } from "../api/types";

interface AuthState {
  user: CurrentUser | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const logout = useCallback(() => {
    const refreshToken = getRefreshToken();
    clearTokens();
    setUser(null);
    if (refreshToken) {
      // Best-effort: the session is already over client-side regardless of whether the
      // server-side revocation round trip succeeds.
      api.logout(refreshToken).catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    setSessionExpiredHandler(() => {
      clearTokens();
      setUser(null);
    });
  }, []);

  useEffect(() => {
    if (!getAccessToken()) {
      setIsLoading(false);
      return;
    }
    api
      .getCurrentUser()
      .then(setUser)
      .catch(() => clearTokens())
      .finally(() => setIsLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await api.login(email, password);
    setTokens(tokens.access_token, tokens.refresh_token);
    const me = await api.getCurrentUser();
    setUser(me);
  }, []);

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}

const ROLE_RANK: Record<CurrentUser["role"], number> = {
  viewer: 0,
  operator: 1,
  ml_engineer: 2,
  administrator: 3,
};

/** Mirrors UserRole.can_act_as (domain/value_objects/enums.py) — a linear hierarchy. */
export function roleAtLeast(user: CurrentUser | null, minimum: CurrentUser["role"]): boolean {
  if (!user) return false;
  return ROLE_RANK[user.role] >= ROLE_RANK[minimum];
}
