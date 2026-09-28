/**
 * API client for DEDAN Remote.
 *
 * - Bearer-token auth (token persisted in localStorage)
 * - Structured error normalization (never leaks raw exceptions)
 * - Request timeout + cancellation support
 */

import type {
  ApiErrorBody,
  ApplicationOut,
  ApplicationStatus,
  AssistantResponse,
  CategoryStat,
  JobDetail,
  JobFilters,
  NotificationsResponse,
  Page,
  ProfileOut,
  RecommendationPage,
  SavedItem,
  SearchResponse,
  SourceStat,
  StatsResponse,
  StatusResponse,
  User,
} from "../types";

const TOKEN_KEY = "dedan_token";
const BASE = "/api/v1"; // same-origin (vite proxy in dev, FastAPI in prod)
const TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable — session stays in-memory only */
  }
}

function buildQuery(filters: JobFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body) headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  try {
    const res = await fetch(`${BASE}${path}`, {
      ...options,
      headers,
      signal: controller.signal,
    });

    if (res.status === 204) return undefined as T;

    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }

    if (!res.ok) {
      const err = body as ApiErrorBody | null;
      const code = err?.error?.code ?? "request_failed";
      const message =
        err?.error?.message ??
        (res.status >= 500
          ? "The service is temporarily unavailable."
          : "Request could not be completed.");
      throw new ApiError(res.status, code, message);
    }
    return body as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(408, "timeout", "The request timed out.");
    }
    throw new ApiError(0, "network_error",
      "Could not reach DEDAN Remote. Check your connection.");
  } finally {
    clearTimeout(timer);
  }
}

// ── Public endpoints ──────────────────────────────────────────────────────

export const api = {
  jobs: (filters: JobFilters = {}) =>
    request<Page>(`/jobs${buildQuery(filters)}`),

  job: (idOrSlug: string) => request<JobDetail>(`/jobs/${idOrSlug}`),

  search: (query: string, limit = 6) =>
    request<SearchResponse>(
      `/api/search?q=${encodeURIComponent(query)}&limit=${limit}`,
    ),

  stats: () => request<StatsResponse>("/api/stats"),

  status: () => request<StatusResponse>("/api/status"),

  sources: () => request<SourceStat[]>("/api/sources"),

  categories: () => request<CategoryStat[]>("/api/categories"),

  // ── Auth ────────────────────────────────────────────────────────────────

  register: async (email: string, password: string,
                    displayName?: string) => {
    const out = await request<{ token: string; user: User }>(
      "/api/auth/register",
      {
        method: "POST",
        body: JSON.stringify({
          email, password, display_name: displayName || null,
        }),
      },
    );
    setToken(out.token);
    return out;
  },

  login: async (email: string, password: string) => {
    const out = await request<{ token: string; user: User }>(
      "/api/auth/login",
      { method: "POST", body: JSON.stringify({ email, password }) },
    );
    setToken(out.token);
    return out;
  },

  logout: async () => {
    try {
      await request<{ logged_out: boolean }>("/api/auth/logout",
        { method: "POST" });
    } finally {
      setToken(null);
    }
  },

  me: () => request<{ user: User }>("/api/auth/me"),

  // ── Saved ───────────────────────────────────────────────────────────────

  saveJob: (jobId: string, note?: string) =>
    request<{ job_id: string; saved: boolean }>(
      `/api/jobs/${jobId}/save`,
      { method: "POST", body: JSON.stringify({ note: note ?? null }) },
    ),

  unsaveJob: (jobId: string) =>
    request<{ job_id: string; saved: boolean }>(
      `/api/jobs/${jobId}/save`,
      { method: "DELETE" },
    ),

  saved: () => request<SavedItem[]>("/api/saved"),

  // ── Applications ────────────────────────────────────────────────────────

  applications: () => request<ApplicationOut[]>("/api/applications"),

  createApplication: (jobId: string,
                      status: ApplicationStatus = "application_started") =>
    request<ApplicationOut>("/api/applications", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId, status }),
    }),

  patchApplication: (appId: string,
                     patch: { status?: ApplicationStatus; note?: string }) =>
    request<ApplicationOut>(`/api/applications/${appId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  notifications: (limit = 30) =>
    request<NotificationsResponse>(`/api/notifications?limit=${limit}`),

  // ── Profile / preferences ───────────────────────────────────────────────

  profile: () => request<ProfileOut>("/api/profile"),

  patchProfile: (patch: {
    display_name?: string;
    categories?: string[];
    experience?: "beginner" | "intermediate" | "advanced";
    regions?: string[];
  }) =>
    request<ProfileOut>("/api/profile", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  recommendations: (limit = 8) =>
    request<RecommendationPage>(`/api/recommendations?limit=${limit}`),

  // ── Assistant ───────────────────────────────────────────────────────────
  assistant: (message: string) =>
    request<AssistantResponse>(`/assistant`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  assistantStream: (message: string, onData: (chunk: string) => void) => {
    // Server-sent events streaming implementation
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

    return fetch(`${BASE}/assistant/stream`, {
      method: "POST",
      headers: {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      },
      body: JSON.stringify({ message }),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) {
          throw new ApiError(
            res.status,
            "request_failed",
            res.status >= 500
              ? "The service is temporarily unavailable."
              : "Request could not be completed."
          );
        }

        const reader = res.body?.getReader();
        if (!reader) {
          throw new ApiError(0, "network_error", "Response body unavailable");
        }

        const decoder = new TextDecoder();
        let done = false;

        while (!done) {
          const { value, done: doneReading } = await reader.read();
          done = doneReading;
          if (value) {
            const chunk = decoder.decode(value, { stream: true });
            // Parse Server-Sent Events format
            const lines = chunk.split("\n");
            for (const line of lines) {
              if (line.startsWith("data: ")) {
                const data = line.slice(6);
                try {
                  const parsed = JSON.parse(data);
                  if (parsed.type === "content") {
                    onData(parsed.content);
                  } else if (parsed.type === "complete") {
                    break;
                  }
                } catch (e) {
                  // If not JSON, treat as plain text
                  onData(data);
                }
              }
            }
          }
        }
      })
      .finally(() => {
        clearTimeout(timer);
      });
  },
};
