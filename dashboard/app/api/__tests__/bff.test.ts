import { http, HttpResponse } from "msw";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { server } from "@/mocks/server";

const OPERATOR_TOKEN = "operator-secret-token";
const SERVICE_TOKEN = "service-held-secret";
const FASTAPI_BASE = "http://fastapi.test";

beforeEach(() => {
  process.env.DASHBOARD_OPERATOR_TOKEN = OPERATOR_TOKEN;
  process.env.BRAVE_DASHBOARD_BEARER_TOKEN = SERVICE_TOKEN;
  process.env.BRAVE_API_URL = FASTAPI_BASE;
});

afterEach(() => {
  delete process.env.DASHBOARD_OPERATOR_TOKEN;
  delete process.env.BRAVE_DASHBOARD_BEARER_TOKEN;
  delete process.env.BRAVE_API_URL;
});

/** Import the handler fresh so it reads the env set in beforeEach. */
async function loadHandler() {
  // route.ts reads env at request time, so a single import is fine; vitest
  // module cache is acceptable here.
  return import("../[...path]/route");
}

function makeRequest(
  path: string,
  { token, method = "GET" }: { token?: string; method?: string } = {},
): { request: NextRequest; ctx: { params: Promise<{ path: string[] }> } } {
  const headers = new Headers();
  if (token) headers.set("authorization", `Bearer ${token}`);
  const request = new NextRequest(`http://localhost/api/${path}`, {
    method,
    headers,
  });
  return { request, ctx: { params: Promise.resolve({ path: path.split("/") }) } };
}

describe("BFF Route Handler — auth gate (T-04-05 / T-04-08)", () => {
  it("returns 401 with NO forward when the browser token is missing", async () => {
    let forwarded = false;
    server.use(
      http.get(`${FASTAPI_BASE}/*`, () => {
        forwarded = true;
        return HttpResponse.json({});
      }),
    );
    const { GET } = await loadHandler();
    const { request, ctx } = makeRequest("api/v1/health");

    const res = await GET(request, ctx);

    expect(res.status).toBe(401);
    expect(forwarded).toBe(false); // 401 BEFORE any forward
    const body = await res.json();
    expect(body.detail).toBe("Sessão expirada ou token inválido");
  });

  it("returns 401 with NO forward when the browser token is wrong", async () => {
    let forwarded = false;
    server.use(
      http.get(`${FASTAPI_BASE}/*`, () => {
        forwarded = true;
        return HttpResponse.json({});
      }),
    );
    const { GET } = await loadHandler();
    const { request, ctx } = makeRequest("api/v1/health", {
      token: "wrong-token",
    });

    const res = await GET(request, ctx);

    expect(res.status).toBe(401);
    expect(forwarded).toBe(false);
  });
});

describe("BFF Route Handler — forward injects the service secret (T-04-06)", () => {
  it("forwards a valid request injecting Authorization: Bearer <service-token>", async () => {
    let seenAuth: string | null = null;
    let seenUrl = "";
    server.use(
      http.get(`${FASTAPI_BASE}/api/v1/health`, ({ request }) => {
        seenAuth = request.headers.get("authorization");
        seenUrl = request.url;
        return HttpResponse.json({ status: "ok" });
      }),
    );
    const { GET } = await loadHandler();
    const { request, ctx } = makeRequest("api/v1/health", {
      token: OPERATOR_TOKEN,
    });

    const res = await GET(request, ctx);

    expect(res.status).toBe(200);
    // The BFF injected the SERVICE token (not the operator token) when calling FastAPI.
    expect(seenAuth).toBe(`Bearer ${SERVICE_TOKEN}`);
    // It forwarded ONLY to the configured FastAPI base (T-04-07).
    expect(seenUrl.startsWith(FASTAPI_BASE)).toBe(true);
    const body = await res.json();
    expect(body).toEqual({ status: "ok" });
  });

  it("never leaks the service secret into the browser-facing response", async () => {
    server.use(
      http.get(`${FASTAPI_BASE}/api/v1/health`, () =>
        HttpResponse.json({ status: "ok" }),
      ),
    );
    const { GET } = await loadHandler();
    const { request, ctx } = makeRequest("api/v1/health", {
      token: OPERATOR_TOKEN,
    });

    const res = await GET(request, ctx);

    // Body must not contain the secret.
    const text = await res.clone().text();
    expect(text).not.toContain(SERVICE_TOKEN);
    // No Authorization header echoed back to the browser.
    expect(res.headers.get("authorization")).toBeNull();
    // No header value anywhere contains the secret.
    for (const [, value] of res.headers.entries()) {
      expect(value).not.toContain(SERVICE_TOKEN);
    }
  });
});
