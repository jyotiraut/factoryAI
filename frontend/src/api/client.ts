import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "./tokenStore";
import type { TokenResponse } from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let onSessionExpired: (() => void) | null = null;

/** Registered once by AuthProvider, so a refresh failure anywhere can force a re-login. */
export function setSessionExpiredHandler(handler: () => void): void {
  onSessionExpired = handler;
}

let refreshInFlight: Promise<string | null> | null = null;

/**
 * Exchange the stored refresh token for a new access token.
 *
 * Coalesced behind a single in-flight promise: several requests racing a 401 at once
 * must not each fire their own `/auth/refresh` call, which would race to revoke the same
 * refresh token against the backend's own single-use rotation.
 */
async function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;

  refreshInFlight = (async () => {
    const response = await fetch(`${BASE_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!response.ok) {
      clearTokens();
      return null;
    }
    const body = (await response.json()) as TokenResponse;
    setTokens(body.access_token, body.refresh_token);
    return body.access_token;
  })();

  try {
    return await refreshInFlight;
  } finally {
    refreshInFlight = null;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | undefined>;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(path, BASE_URL);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }
  return url.toString();
}

/**
 * The one place every dashboard view fetches through — no view reaches for `fetch`
 * directly, so the retry-once-on-401 behaviour below applies uniformly (Phase 13's own
 * exit criterion: every dashboard number comes from the public API, authenticated the
 * same way every time).
 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = buildUrl(path, options.query);
  const doFetch = (token: string | null) =>
    fetch(url, {
      method: options.method ?? "GET",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });

  let response = await doFetch(getAccessToken());

  if (response.status === 401 && getRefreshToken()) {
    const newToken = await refreshAccessToken();
    if (newToken) {
      response = await doFetch(newToken);
    } else {
      onSessionExpired?.();
      throw new ApiError(401, "session expired");
    }
  }

  if (response.status === 401) {
    onSessionExpired?.();
  }

  if (!response.ok) {
    const detail = await response
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined);
    throw new ApiError(response.status, detail ?? response.statusText);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * `/predict` takes `multipart/form-data`, not JSON — a separate function rather than
 * teaching `apiRequest` a body-type union, since every other endpoint in this app is JSON
 * and the browser must set the multipart boundary itself (an explicit `Content-Type`
 * header here would omit it and the backend would fail to parse the form).
 */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const url = buildUrl(path);
  const doFetch = (token: string | null) =>
    fetch(url, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      body: form,
    });

  let response = await doFetch(getAccessToken());

  if (response.status === 401 && getRefreshToken()) {
    const newToken = await refreshAccessToken();
    if (newToken) {
      response = await doFetch(newToken);
    } else {
      onSessionExpired?.();
      throw new ApiError(401, "session expired");
    }
  }

  if (response.status === 401) {
    onSessionExpired?.();
  }

  if (!response.ok) {
    const detail = await response
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined);
    throw new ApiError(response.status, detail ?? response.statusText);
  }

  return (await response.json()) as T;
}
