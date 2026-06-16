import { type NextRequest, NextResponse } from "next/server";

import { isAuthorizedBrowserToken } from "@/lib/auth";

/**
 * BFF proxy Route Handler (D-02, D-03).
 *
 * Browser → `Authorization: Bearer <operator-token>` → THIS handler → FastAPI
 * (`Authorization: Bearer <service-token>` injected server-side). The service
 * secret (`BRAVE_DASHBOARD_BEARER_TOKEN`) is read only here and NEVER reaches the
 * browser (T-04-06).
 *
 * Security contract:
 *  - 401 BEFORE any forward when the browser token is missing/invalid
 *    (T-04-05 spoofing, T-04-08 unauthenticated-mutation).
 *  - Forwards ONLY to the configured BRAVE_API_URL base — the catch-all `path`
 *    is appended to that fixed origin, so an attacker cannot redirect the proxy
 *    to an arbitrary host (T-04-07 SSRF / open-redirect).
 *  - The injected service secret never appears in any response body/header.
 *
 * This is a Node-runtime server module (uses lib/auth's node:crypto). It is never
 * shipped to the client.
 */

// Force Node runtime — lib/auth uses node:crypto (constant-time compare).
export const runtime = "nodejs";

const UNAUTHORIZED = NextResponse.json(
  // UI-SPEC 401 copy.
  { detail: "Sessão expirada ou token inválido" },
  { status: 401 },
);

function fastApiBase(): string {
  const base = process.env.BRAVE_API_URL;
  if (!base) {
    throw new Error("BRAVE_API_URL is not configured");
  }
  // Strip a trailing slash so path joining is unambiguous.
  return base.replace(/\/+$/, "");
}

async function proxy(
  request: NextRequest,
  segments: string[],
): Promise<NextResponse> {
  // 1. Validate the browser token BEFORE doing anything else (fail-closed).
  if (!isAuthorizedBrowserToken(request.headers.get("authorization"))) {
    return UNAUTHORIZED;
  }

  // 2. Build the upstream URL from the FIXED FastAPI base + the catch-all path.
  //    The path is taken from route segments (not from any user-controlled host),
  //    so the proxy can only ever reach BRAVE_API_URL (T-04-07).
  const base = fastApiBase();
  const path = segments.map(encodeURIComponent).join("/");
  const upstreamUrl = `${base}/${path}${request.nextUrl.search}`;

  // 3. Forward method/body, injecting the server-held service secret.
  const serviceToken = process.env.BRAVE_DASHBOARD_BEARER_TOKEN ?? "";
  const headers: Record<string, string> = {
    Authorization: `Bearer ${serviceToken}`,
  };
  const contentType = request.headers.get("content-type");
  if (contentType) headers["content-type"] = contentType;

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const body = hasBody ? await request.text() : undefined;

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body,
      // Never follow a redirect to a different host — relay it as-is.
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { detail: "Falha ao consultar a API (Brave indisponível)" },
      { status: 502 },
    );
  }

  // 4. Relay the FastAPI status + JSON. We re-serialize the body ourselves and
  //    forward only safe response metadata — we do NOT echo back any request
  //    Authorization header, so the service secret cannot leak (T-04-06).
  const text = await upstream.text();
  const responseContentType =
    upstream.headers.get("content-type") ?? "application/json";

  return new NextResponse(text, {
    status: upstream.status,
    headers: { "content-type": responseContentType },
  });
}

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, ctx: RouteContext) {
  const { path } = await ctx.params;
  return proxy(request, path);
}

export async function POST(request: NextRequest, ctx: RouteContext) {
  const { path } = await ctx.params;
  return proxy(request, path);
}

export async function PATCH(request: NextRequest, ctx: RouteContext) {
  const { path } = await ctx.params;
  return proxy(request, path);
}
