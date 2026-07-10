import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  PainelOrigem,
  parseTACurl,
  type OrigemSource,
} from "@/components/painel/PainelOrigem";
import { setOperatorToken } from "@/lib/api-client";
import { engineSetSourceSuccess, taSessionStatus } from "@/mocks/handlers/engine";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

const TA_INJECT_URL = "http://localhost:3000/api/api/v1/tripadvisor/session";
const ENGINE_SOURCE_URL = "http://localhost:3000/api/api/v1/engine/source";

/** A realistic "Copy as cURL" paste with a cookie jar, UA and a persisted-query id. */
const SAMPLE_CURL =
  "curl 'https://www.tripadvisor.com/data/graphql/ids' " +
  "-H 'cookie: TASID=abc123; datadome=zzz999; TAUnique=u-42' " +
  "-H 'user-agent: Mozilla/5.0 (Macintosh) Chrome/126.0' " +
  "--data-raw '[{\"variables\":{},\"extensions\":{\"preRegisteredQueryId\":\"a5cb7fa004b5e4b5\"}}]'";

beforeEach(() => {
  server.resetHandlers();
});

afterEach(() => {
  server.resetHandlers();
});

describe("PainelOrigem", () => {
  it("renders TripAdvisor as the sole surfaced source (mtur/default retired, Places is enrichment)", async () => {
    server.use(taSessionStatus());
    renderWithClient(<PainelOrigem open onClose={() => {}} />);

    expect(await screen.findByTestId("origem-radio-tripadvisor")).toBeInTheDocument();
    // The dormant Places lane (mtur/default) is no longer surfaced or activatable.
    expect(screen.queryByTestId("origem-radio-mtur")).toBeNull();
    expect(screen.queryByTestId("origem-radio-default")).toBeNull();
    // Google Places is ENRICHMENT, not a collection source — no radio row.
    expect(screen.queryByTestId("origem-radio-google_places")).toBeNull();
  });

  it("selects TripAdvisor (the sole source) when the modal opens", async () => {
    // The modal mounts (open=false) at app load BEFORE the engine status query
    // resolves; the open-edge sync re-latches the surfaced source (TripAdvisor).
    server.use(taSessionStatus());
    setOperatorToken("test-operator-token");
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });
    const ui = (src: OrigemSource, open: boolean) => (
      <QueryClientProvider client={client}>
        <PainelOrigem open={open} onClose={() => {}} initialSource={src} />
      </QueryClientProvider>
    );

    // Mount closed, then the operator opens the modal.
    const { rerender } = render(ui("tripadvisor", false));
    rerender(ui("tripadvisor", true));

    const ta = await screen.findByTestId("origem-radio-tripadvisor");
    expect(ta).toHaveAttribute("data-selected", "true");
  });

  it("renders nothing when closed", () => {
    server.use(taSessionStatus());
    renderWithClient(<PainelOrigem open={false} onClose={() => {}} />);
    expect(screen.queryByTestId("painel-origem")).toBeNull();
  });

  it("shows the cURL textarea on open (TripAdvisor is the sole, default-selected source)", async () => {
    server.use(taSessionStatus());
    renderWithClient(<PainelOrigem open onClose={() => {}} />);

    // TripAdvisor is now pre-selected on open, so its cURL textarea is revealed
    // immediately — no other source to switch away from.
    expect(await screen.findByTestId("origem-curl")).toBeInTheDocument();
    // Clicking the (already-selected) TripAdvisor row keeps it visible.
    fireEvent.click(screen.getByTestId("origem-radio-tripadvisor"));
    expect(screen.getByTestId("origem-curl")).toBeInTheDocument();
  });

  it("submits the parsed cURL body (cookies + query_ids + user_agent + acquired_at) to injectTASession", async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(
      taSessionStatus(),
      http.post(TA_INJECT_URL, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ status: "ready" });
      }),
      engineSetSourceSuccess(),
    );
    renderWithClient(<PainelOrigem open onClose={() => {}} />);

    fireEvent.click(screen.getByTestId("origem-radio-tripadvisor"));
    fireEvent.change(await screen.findByTestId("origem-curl"), {
      target: { value: SAMPLE_CURL },
    });
    fireEvent.click(screen.getByTestId("origem-submit"));

    await waitFor(() => expect(captured).not.toBeNull());
    const body = captured as unknown as {
      cookies: Record<string, string>;
      query_ids: Record<string, string>;
      user_agent: string;
      acquired_at: string;
    };
    expect(body.cookies.TASID).toBe("abc123");
    expect(body.cookies.datadome).toBe("zzz999");
    expect(Object.values(body.query_ids)).toContain("a5cb7fa004b5e4b5");
    expect(body.user_agent).toContain("Mozilla/5.0");
    expect(typeof body.acquired_at).toBe("string");
    expect(body.acquired_at.length).toBeGreaterThan(0);
  });

  it("surfaces the distinct 422 invalid-session error state", async () => {
    server.use(
      taSessionStatus(),
      http.post(TA_INJECT_URL, () =>
        HttpResponse.json({ detail: "invalid_session" }, { status: 422 }),
      ),
    );
    renderWithClient(<PainelOrigem open onClose={() => {}} />);

    fireEvent.click(screen.getByTestId("origem-radio-tripadvisor"));
    fireEvent.change(await screen.findByTestId("origem-curl"), {
      target: { value: SAMPLE_CURL },
    });
    fireEvent.click(screen.getByTestId("origem-submit"));

    expect(await screen.findByTestId("origem-error-422")).toBeInTheDocument();
    expect(screen.queryByTestId("origem-error-503")).toBeNull();
  });

  it("surfaces the distinct 503 canary-unverified error state", async () => {
    server.use(
      taSessionStatus(),
      http.post(TA_INJECT_URL, () =>
        HttpResponse.json({ detail: "canary_unverified" }, { status: 503 }),
      ),
    );
    renderWithClient(<PainelOrigem open onClose={() => {}} />);

    fireEvent.click(screen.getByTestId("origem-radio-tripadvisor"));
    fireEvent.change(await screen.findByTestId("origem-curl"), {
      target: { value: SAMPLE_CURL },
    });
    fireEvent.click(screen.getByTestId("origem-submit"));

    expect(await screen.findByTestId("origem-error-503")).toBeInTheDocument();
    expect(screen.queryByTestId("origem-error-422")).toBeNull();
  });

  it("saving TA (after inject success) fires POST /engine/source with {source: 'tripadvisor'}", async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(
      taSessionStatus(),
      http.post(TA_INJECT_URL, () => HttpResponse.json({ status: "ready" })),
      http.post(ENGINE_SOURCE_URL, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ source: "tripadvisor" });
      }),
    );
    renderWithClient(<PainelOrigem open onClose={() => {}} />);

    fireEvent.click(screen.getByTestId("origem-radio-tripadvisor"));
    fireEvent.change(await screen.findByTestId("origem-curl"), {
      target: { value: SAMPLE_CURL },
    });
    fireEvent.click(screen.getByTestId("origem-submit"));

    await waitFor(() => expect(captured).toMatchObject({ source: "tripadvisor" }));
  });
});

describe("parseTACurl", () => {
  it("parses a plain single-quoted cURL (cookies, UA, query id)", () => {
    const p = parseTACurl(SAMPLE_CURL);
    expect(Object.keys(p.cookies)).toEqual(["TASID", "datadome", "TAUnique"]);
    expect(p.user_agent).toContain("Mozilla/5.0");
    expect(Object.values(p.query_ids)).toEqual(["a5cb7fa004b5e4b5"]);
  });

  it("parses Chrome's ANSI-C $'...' quoting (regression: cookies/UA no longer empty)", () => {
    // Chrome "Copy as cURL (bash)" wraps values with special chars in $'...'.
    // Before the \$? fix this returned empty cookies → backend 422 "expired".
    const ansiC =
      "curl 'https://www.tripadvisor.com/data/graphql/ids' " +
      "-H $'user-agent: Mozilla/5.0 (Macintosh)' " +
      "-b $'TASID=abc123; datadome=zZ!9; TAUnique=u-42' " +
      "--data-raw $'[{\"extensions\":{\"preRegisteredQueryId\":\"79aaeeb847e55e58\"}}]'";
    const p = parseTACurl(ansiC);
    expect(Object.keys(p.cookies)).toEqual(["TASID", "datadome", "TAUnique"]);
    expect(p.user_agent).toBe("Mozilla/5.0 (Macintosh)");
    expect(Object.values(p.query_ids)).toEqual(["79aaeeb847e55e58"]);
  });

  it("decodes ANSI-C escape sequences inside $'...' values", () => {
    // \x3d → '=', \' → ' ; ensure the cookie value round-trips through the jar split.
    const curl =
      "curl 'https://x/data/graphql/ids' " +
      "-b $'TASID=a\\x62c; datadome=zzz' " +
      "--data-raw $'[{\"extensions\":{\"preRegisteredQueryId\":\"444040f131735091\"}}]'";
    const p = parseTACurl(curl);
    expect(p.cookies.TASID).toBe("abc"); // \x62 → 'b'
  });

  it("returns empty maps when the paste has no cookies or query id", () => {
    const p = parseTACurl("curl 'https://www.tripadvisor.com/SomePage'");
    expect(Object.keys(p.cookies)).toHaveLength(0);
    expect(Object.keys(p.query_ids)).toHaveLength(0);
  });
});
