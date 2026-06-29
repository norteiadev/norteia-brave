import { fireEvent, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { PainelOrigem } from "@/components/painel/PainelOrigem";
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
  it("renders a radio row per source", async () => {
    server.use(taSessionStatus());
    renderWithClient(<PainelOrigem open onClose={() => {}} />);

    expect(await screen.findByTestId("origem-radio-mtur")).toBeInTheDocument();
    expect(screen.getByTestId("origem-radio-tripadvisor")).toBeInTheDocument();
    expect(screen.getByTestId("origem-radio-google_places")).toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    server.use(taSessionStatus());
    renderWithClient(<PainelOrigem open={false} onClose={() => {}} />);
    expect(screen.queryByTestId("painel-origem")).toBeNull();
  });

  it("reveals the cURL textarea only after TripAdvisor is selected", async () => {
    server.use(taSessionStatus());
    renderWithClient(<PainelOrigem open onClose={() => {}} />);

    expect(screen.queryByTestId("origem-curl")).toBeNull();
    fireEvent.click(screen.getByTestId("origem-radio-tripadvisor"));
    expect(await screen.findByTestId("origem-curl")).toBeInTheDocument();
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

  it("saving mtur fires POST /engine/source with {source: 'default'}", async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(
      taSessionStatus(),
      http.post(ENGINE_SOURCE_URL, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ source: "default" });
      }),
    );
    renderWithClient(<PainelOrigem open onClose={() => {}} />);

    // mtur is already selected by default — just click Salvar
    fireEvent.click(screen.getByTestId("origem-submit"));

    await waitFor(() => expect(captured).toMatchObject({ source: "default" }));
  });
});
