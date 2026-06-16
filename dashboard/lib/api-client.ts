/**
 * Browser-side API client (D-04).
 *
 * EVERY call goes to the BFF at a RELATIVE `/api/...` URL — never to FastAPI
 * directly. The BFF (app/api/[...path]/route.ts) validates the operator token and
 * injects the server-held service secret. This module only ever holds the
 * OPERATOR token (the value the operator typed on the login gate), never the
 * service secret.
 *
 * The catch-all BFF maps `/api/<rest>` → FastAPI `/<rest>`, so to reach FastAPI's
 * `/api/v1/health` the browser calls `/api/api/v1/health`. We hide that with the
 * `bff(path)` helper: callers pass the FastAPI path (e.g. `api/v1/dlq`) and the
 * helper prefixes the BFF mount.
 */

const OPERATOR_TOKEN_KEY = "brave.operatorToken";

/** Persist the operator token the BFF will validate (browser storage only). */
export function setOperatorToken(token: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(OPERATOR_TOKEN_KEY, token);
}

export function getOperatorToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(OPERATOR_TOKEN_KEY);
}

export function clearOperatorToken(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(OPERATOR_TOKEN_KEY);
}

/** Map a FastAPI path (`api/v1/...`) onto the BFF mount (`/api/api/v1/...`). */
export function bff(fastApiPath: string): string {
  const clean = fastApiPath.replace(/^\/+/, "");
  return `/api/${clean}`;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Typed fetch against the BFF. Attaches the operator token as a Bearer header.
 * Throws `ApiError` on non-2xx (status 401 is the auth-fail the login gate uses
 * to redirect — RESEARCH §3, UI-SPEC 401 copy).
 */
export async function apiFetch<T>(
  fastApiPath: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getOperatorToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(bff(fastApiPath), { ...init, headers });
  if (!res.ok) {
    let detail = `Falha ao consultar a API (${res.status}).`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // non-JSON error body — keep the default message
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

/** TanStack Query key factory — later slices extend this. */
export const queryKeys = {
  health: ["health"] as const,
  dlq: (uf?: string) => ["dlq", { uf }] as const,
  dlqDetail: (rioId: string) => ["dlq", "detail", rioId] as const,
};
