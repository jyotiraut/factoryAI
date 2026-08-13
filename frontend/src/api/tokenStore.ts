// A thin wrapper around localStorage, not a security boundary: an XSS-vulnerable page can
// read localStorage regardless of what wraps it. Isolated here so the rest of the app never
// touches storage keys directly, and so a future move to an httpOnly-cookie refresh flow
// only touches this one file.

const ACCESS_TOKEN_KEY = "factoryai.access_token";
const REFRESH_TOKEN_KEY = "factoryai.refresh_token";

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setTokens(accessToken: string, refreshToken?: string | null): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  if (refreshToken) {
    localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  }
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}
