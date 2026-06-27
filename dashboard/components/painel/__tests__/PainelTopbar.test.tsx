import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelTopbar } from "@/components/painel/PainelTopbar";
import { server } from "@/mocks/server";
import {
  engineStartSuccess,
  engineStatus,
  engineStopSuccess,
  taSessionStatus,
} from "@/mocks/handlers/engine";

import { renderWithClient } from "../../cms/__tests__/test-utils";

beforeEach(() => {
  server.resetHandlers();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PainelTopbar", () => {
  it("renders the page title and subtitle", async () => {
    server.use(engineStatus(), taSessionStatus());
    renderWithClient(<PainelTopbar title="Painel" subtitle="Quadro de processamento" />);

    expect(await screen.findByTestId("painel-topbar")).toHaveTextContent("Painel");
    expect(screen.getByTestId("painel-topbar")).toHaveTextContent(
      "Quadro de processamento",
    );
  });

  it("motor switch reflects idle state (aria-checked=false)", async () => {
    server.use(engineStatus({ state: "idle" }), taSessionStatus());
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const sw = await screen.findByTestId("painel-motor-switch");
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));
  });

  it("motor switch reflects running state (aria-checked=true) and toggling calls stop", async () => {
    let stopCalled = false;
    server.use(
      engineStatus({ state: "running" }),
      taSessionStatus(),
      http.post("http://localhost:3000/api/api/v1/engine/stop", () => {
        stopCalled = true;
        return HttpResponse.json({ status: "stopping" }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const sw = await screen.findByTestId("painel-motor-switch");
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "true"));

    await user.click(sw);
    await waitFor(() => expect(stopCalled).toBe(true));
  });

  it("idle + confirmed toggle fires POST /start", async () => {
    let startCalled = false;
    server.use(
      engineStatus({ state: "idle" }),
      taSessionStatus(),
      http.post("http://localhost:3000/api/api/v1/engine/start", () => {
        startCalled = true;
        return HttpResponse.json({ status: "started", ufs_total: 27 }, { status: 202 });
      }),
      engineStopSuccess(),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const sw = await screen.findByTestId("painel-motor-switch");
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));

    await user.click(sw);
    await waitFor(() => expect(startCalled).toBe(true));
  });

  it("idle + cancelled confirm does NOT fire POST /start", async () => {
    let startCalled = false;
    server.use(
      engineStatus({ state: "idle" }),
      taSessionStatus(),
      http.post("http://localhost:3000/api/api/v1/engine/start", () => {
        startCalled = true;
        return HttpResponse.json({ status: "started" }, { status: 202 });
      }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const sw = await screen.findByTestId("painel-motor-switch");
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));

    await user.click(sw);
    // give the (non-)mutation a tick; assert it never fired
    await new Promise((r) => setTimeout(r, 50));
    expect(startCalled).toBe(false);
  });

  it("TA pill renders 'Pronta' when the session is present", async () => {
    server.use(engineStatus(), taSessionStatus({ present: true, reason: null }));
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    expect(await screen.findByTestId("painel-ta-pill")).toHaveTextContent("Pronta");
  });

  it("TA pill renders 'Precisa bootstrap' when needs_bootstrap", async () => {
    server.use(
      engineStatus(),
      taSessionStatus({ present: false, reason: "needs_bootstrap" }),
    );
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    expect(await screen.findByTestId("painel-ta-pill")).toHaveTextContent(
      "Precisa bootstrap",
    );
  });

  it("source label renders 'Padrão' by default", async () => {
    server.use(engineStatus({ source: null }), taSessionStatus());
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    expect(await screen.findByTestId("painel-source")).toHaveTextContent("Padrão");
  });

  it("source label renders 'TripAdvisor' when status.source=tripadvisor", async () => {
    server.use(engineStatus({ source: "tripadvisor" }), taSessionStatus());
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const src = await screen.findByTestId("painel-source");
    await waitFor(() => expect(src).toHaveTextContent("TripAdvisor"));
  });
});
