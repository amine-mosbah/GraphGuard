/**
 * HTTP client for the GraphGuard API.
 *
 * Owns two things the rest of the app should never think about: attaching the access
 * token, and transparently refreshing it when it expires.
 *
 * Access tokens are short-lived by design (see DECISIONS.md D21), which means every
 * long-lived screen — the triage queue an analyst leaves open for an hour, the network
 * map — *will* hit a 401 in normal use. Handling that here rather than at call sites
 * means a working session never dies mid-investigation because a timer elapsed.
 *
 * Concurrent 401s share one refresh. Without that, ten parallel widget requests would
 * fire ten refreshes, and since refresh tokens rotate on every exchange (D21) nine of
 * them would be rejected as replays and log the analyst out.
 */

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_at: string;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: "analyst" | "admin";
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

const ACCESS_KEY = "gg.access";
const REFRESH_KEY = "gg.refresh";

export class ApiError extends Error {
  // Declared and assigned explicitly rather than as a constructor parameter property:
  // `erasableSyntaxOnly` rejects the shorthand, since it is TypeScript syntax that emits
  // runtime code rather than being erased.
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const tokens = {
  access: () => localStorage.getItem(ACCESS_KEY),
  refresh: () => localStorage.getItem(REFRESH_KEY),
  set(pair: TokenPair) {
    localStorage.setItem(ACCESS_KEY, pair.access_token);
    localStorage.setItem(REFRESH_KEY, pair.refresh_token);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

/** In-flight refresh, shared by every request that finds itself unauthenticated. */
let refreshing: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const token = tokens.refresh();
  if (!token) return false;

  const response = await fetch("/api/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: token }),
  });

  if (!response.ok) {
    tokens.clear();
    return false;
  }
  tokens.set((await response.json()) as TokenPair);
  return true;
}

async function ensureRefreshed(): Promise<boolean> {
  refreshing ??= refreshTokens().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

async function send(path: string, init: RequestInit, retry: boolean): Promise<Response> {
  const access = tokens.access();
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (access) headers.set("Authorization", `Bearer ${access}`);

  const response = await fetch(path, { ...init, headers });

  if (response.status === 401 && retry && tokens.refresh()) {
    if (await ensureRefreshed()) return send(path, init, false);
  }
  return response;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await send(path, init, true);

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      // FastAPI puts a string in `detail` for HTTPException and an array for
      // validation errors; both should reach the analyst as readable text.
      detail =
        typeof body.detail === "string"
          ? body.detail
          : Array.isArray(body.detail)
            ? body.detail.map((d: { msg?: string }) => d.msg ?? "invalid").join("; ")
            : detail;
    } catch {
      /* body was not JSON — keep the status text */
    }
    throw new ApiError(response.status, detail);
  }

  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),

  /**
   * POST and return the raw Response for server-sent-event bodies. The caller reads
   * the stream; auth attachment and the shared 401-refresh path are identical to
   * every other request.
   */
  async stream(path: string, body: unknown): Promise<Response> {
    const response = await send(path, { method: "POST", body: JSON.stringify(body) }, true);
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const parsed = await response.json();
        if (typeof parsed.detail === "string") detail = parsed.detail;
      } catch {
        /* body was not JSON — keep the status text */
      }
      throw new ApiError(response.status, detail);
    }
    return response;
  },

  async login(email: string, password: string): Promise<TokenPair> {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(response.status, body.detail ?? "Sign-in failed");
    }
    const pair = (await response.json()) as TokenPair;
    tokens.set(pair);
    return pair;
  },

  async logout(): Promise<void> {
    const refresh = tokens.refresh();
    // Best-effort: the server revokes the refresh token, but the local session must
    // end regardless of whether that call succeeds.
    if (refresh) {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      }).catch(() => undefined);
    }
    tokens.clear();
  },

  me: () => request<User>("/api/auth/me"),
};
