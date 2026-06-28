import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { EngineControl } from "@/components/engine/EngineControl";
import type { EngineStatus } from "@/lib/engine-api";
import { server } from "@/mocks/server";
import {
  engineStatus,
  engineUnauthorized,
  taSessionStatus,
} from "@/mocks/handlers/engine";

import { renderWithClient } from "../../cms/__tests__/test-utils";

function buildStatus(overrides: Partial<EngineStatus>): EngineStatus {
  return {
    state: "idle",
    current_uf: null,
    ufs_done: 0,
    ufs_total: 0,
    enabled: false,
    counts: {
      nascente: 0,
      rio: { in_progress: 0, mar: 0, dlq: 0, descarte: 0 },
      mar: 0,
      atrativos_by_sub_state: {},
    },
    ...overrides,
  };
}

beforeEach(() => {
  server.resetHandlers();
});

describe("EngineControl", () => {
  it("shows idle state and the 'Ligar motor' button by default", async () => {
    server.use(engineStatus());
    renderWithClient(<EngineControl />);

    expect(await screen.findByTestId("engine-start")).toHaveTextContent(
      "Ligar motor",
    );
    await waitFor(() =>
      expect(screen.getByTestId("engine-state")).toHaveTextContent("Parado"),
    );
  });

  it("renders live pipeline counts from the status payload", async () => {
    server.use(
      engineStatus({
        counts: {
          nascente: 42,
          rio: { in_progress: 0, mar: 7, dlq: 30, descarte: 5 },
          mar: 7,
          atrativos_by_sub_state: { discovered: 12, contacts_found: 3 },
        },
      }),
    );
    renderWithClient(<EngineControl />);

    const counts = await screen.findByTestId("engine-counts");
    expect(counts.textContent).toContain("42"); // nascente
    expect(counts.textContent).toContain("30"); // dlq
    expect(counts.textContent).toContain("15"); // atrativos total (12+3)
  });

  it("running state shows current UF, progress and the 'Parar motor' button", async () => {
    server.use(
      engineStatus({
        state: "running",
        enabled: true,
        current_uf: "BA",
        ufs_done: 5,
        ufs_total: 27,
      }),
    );
    renderWithClient(<EngineControl />);

    expect(await screen.findByTestId("engine-stop")).toHaveTextContent(
      "Parar motor",
    );
    expect(screen.getByTestId("engine-state")).toHaveTextContent("Varrendo");
    expect(screen.getByTestId("engine-state")).toHaveTextContent("UF BA");
    expect(screen.getByTestId("engine-progress").textContent).toContain("5/27");
  });

  it("start → posts and refetches status (now running)", async () => {
    let startCalled = false;
    // /status returns running only AFTER /start has been POSTed.
    server.use(
      http.post(
        "http://localhost:3000/api/api/v1/engine/start",
        () => {
          startCalled = true;
          return HttpResponse.json({ status: "started", ufs_total: 27 }, { status: 202 });
        },
      ),
      http.get("http://localhost:3000/api/api/v1/engine/status", () =>
        HttpResponse.json(
          startCalled
            ? buildStatus({ state: "running", enabled: true, ufs_total: 27 })
            : buildStatus({}),
        ),
      ),
    );

    const user = userEvent.setup();
    renderWithClient(<EngineControl />);

    // Required-selection: pick a depth before the start button is enabled.
    await user.click(await screen.findByTestId("engine-depth-nascente"));
    await user.click(screen.getByTestId("engine-start"));

    await waitFor(() =>
      expect(screen.getByTestId("engine-state")).toHaveTextContent("Varrendo"),
    );
  });

  it("stopping state disables the button and shows 'Parando…'", async () => {
    server.use(engineStatus({ state: "stopping" }));
    renderWithClient(<EngineControl />);

    const btn = await screen.findByTestId("engine-stop");
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("Parando…");
  });

  it("surfaces 401 without throwing (session-expired)", async () => {
    server.use(...engineUnauthorized());
    renderWithClient(<EngineControl />);
    // Renders the idle fallback (no status) without crashing.
    expect(await screen.findByTestId("engine-control")).toBeInTheDocument();
  });

  it("renders all three depth options with PT-BR labels on idle", async () => {
    server.use(engineStatus());
    renderWithClient(<EngineControl />);

    const group = await screen.findByTestId("engine-depth");
    expect(group.textContent).toContain("Apenas nascente");
    expect(group.textContent).toContain("Nascente → Rio");
    expect(group.textContent).toContain("Nascente → Rio → Mar");
    // Each option carries its enum value.
    expect(screen.getByTestId("engine-depth-nascente")).toBeInTheDocument();
    expect(screen.getByTestId("engine-depth-nascente_rio")).toBeInTheDocument();
    expect(
      screen.getByTestId("engine-depth-nascente_rio_mar"),
    ).toBeInTheDocument();
  });

  it("disables 'Ligar motor' until a depth is selected (ENG-01)", async () => {
    server.use(engineStatus());
    const user = userEvent.setup();
    renderWithClient(<EngineControl />);

    const startBtn = await screen.findByTestId("engine-start");
    expect(startBtn).toBeDisabled();

    await user.click(screen.getByTestId("engine-depth-nascente_rio"));
    expect(startBtn).toBeEnabled();
  });

  it("sends the selected depth in the POST /start body (ENG-02)", async () => {
    let capturedDepth: string | undefined;
    let startCalled = false;
    server.use(
      http.post(
        "http://localhost:3000/api/api/v1/engine/start",
        async ({ request }) => {
          const body = (await request.json()) as { depth?: string };
          capturedDepth = body.depth;
          startCalled = true;
          return HttpResponse.json(
            { status: "started", ufs_total: 27, depth: body.depth },
            { status: 202 },
          );
        },
      ),
      http.get("http://localhost:3000/api/api/v1/engine/status", () =>
        HttpResponse.json(
          startCalled
            ? buildStatus({ state: "running", enabled: true, depth: "nascente_rio_mar" })
            : buildStatus({}),
        ),
      ),
    );

    const user = userEvent.setup();
    renderWithClient(<EngineControl />);

    await user.click(await screen.findByTestId("engine-depth-nascente_rio_mar"));
    await user.click(screen.getByTestId("engine-start"));

    await waitFor(() => expect(capturedDepth).toBe("nascente_rio_mar"));
  });

  it("reads back the active depth when running (ENG-02 status→UI)", async () => {
    server.use(
      engineStatus({
        state: "running",
        enabled: true,
        current_uf: "BA",
        ufs_done: 5,
        ufs_total: 27,
        depth: "nascente_rio",
      }),
    );
    renderWithClient(<EngineControl />);

    const readback = await screen.findByTestId("engine-active-depth");
    expect(readback).toHaveTextContent("Nascente → Rio");
  });

  it("enabled=true state=idle shows stop button (not start controls)", async () => {
    server.use(
      engineStatus({ state: "idle", enabled: true }),
    );
    renderWithClient(<EngineControl />);

    // Stop button must be visible
    expect(await screen.findByTestId("engine-stop")).toBeInTheDocument();
    // Start controls must be absent
    expect(screen.queryByTestId("engine-start")).toBeNull();
  });
});

describe("EngineControl — session health pill (TA-13)", () => {
  it("shows 'Pronta' pill when source=tripadvisor and session is present", async () => {
    server.use(
      engineStatus({ state: "idle" }),
      taSessionStatus({ present: true, expires_in: 900, reason: null }),
    );
    const user = userEvent.setup();
    renderWithClient(<EngineControl />);

    // Select tripadvisor source
    const taBtn = await screen.findByTestId("engine-source-tripadvisor");
    await user.click(taBtn);

    // Pill should appear with "Pronta"
    const pill = await screen.findByTestId("ta-session-status");
    expect(pill).toHaveTextContent("Pronta");
  });

  it("shows 'Precisa bootstrap' pill when reason='needs_bootstrap'", async () => {
    server.use(
      engineStatus({ state: "idle" }),
      taSessionStatus({ present: false, reason: "needs_bootstrap" }),
    );
    const user = userEvent.setup();
    renderWithClient(<EngineControl />);

    await user.click(await screen.findByTestId("engine-source-tripadvisor"));

    const pill = await screen.findByTestId("ta-session-status");
    expect(pill).toHaveTextContent("Precisa bootstrap");
  });

  it("shows 'Expirada' pill when present=false and reason=null", async () => {
    server.use(
      engineStatus({ state: "idle" }),
      taSessionStatus({ present: false, reason: null }),
    );
    const user = userEvent.setup();
    renderWithClient(<EngineControl />);

    await user.click(await screen.findByTestId("engine-source-tripadvisor"));

    const pill = await screen.findByTestId("ta-session-status");
    expect(pill).toHaveTextContent("Expirada");
  });

  it("does NOT render the session-health pill when source=default is selected", async () => {
    server.use(engineStatus({ state: "idle" }), taSessionStatus());
    renderWithClient(<EngineControl />);

    // Default source is "default" — pill should not be present
    await screen.findByTestId("engine-start"); // wait for idle render
    expect(screen.queryByTestId("ta-session-status")).toBeNull();
  });

  it("shows pill when engine is running with source=tripadvisor", async () => {
    server.use(
      engineStatus({
        state: "running",
        current_uf: "BA",
        ufs_done: 1,
        ufs_total: 27,
        source: "tripadvisor",
      }),
      taSessionStatus({ present: false, reason: "needs_bootstrap" }),
    );
    renderWithClient(<EngineControl />);

    // Engine is running with tripadvisor — pill should render without selecting source
    const pill = await screen.findByTestId("ta-session-status");
    expect(pill).toHaveTextContent("Precisa bootstrap");
  });
});
