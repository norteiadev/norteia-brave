import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
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
      engineStatus({ state: "running", enabled: true }),
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

  it("idle toggle opens the depth menu; picking a depth fires POST /start WITH that depth", async () => {
    let startBody: { depth?: string } | null = null;
    server.use(
      engineStatus({ state: "idle" }),
      taSessionStatus(),
      http.post("http://localhost:3000/api/api/v1/engine/start", async ({ request }) => {
        startBody = (await request.json()) as { depth?: string };
        return HttpResponse.json({ status: "started", ufs_total: 27 }, { status: 202 });
      }),
      engineStopSuccess(),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const sw = await screen.findByTestId("painel-motor-switch");
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));

    // Toggling idle opens the depth menu — it must NOT start without a depth.
    await user.click(sw);
    await screen.findByTestId("painel-depth-menu");

    await user.click(screen.getByTestId("painel-depth-nascente_rio"));
    await waitFor(() => expect(startBody).not.toBeNull());
    expect(startBody).toEqual({ depth: "nascente_rio" });
  });

  it("opening the depth menu without picking does NOT fire POST /start", async () => {
    let startCalled = false;
    server.use(
      engineStatus({ state: "idle" }),
      taSessionStatus(),
      http.post("http://localhost:3000/api/api/v1/engine/start", () => {
        startCalled = true;
        return HttpResponse.json({ status: "started" }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const sw = await screen.findByTestId("painel-motor-switch");
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));

    await user.click(sw);
    await screen.findByTestId("painel-depth-menu");
    // No depth picked — assert start never fired.
    await new Promise((r) => setTimeout(r, 50));
    expect(startCalled).toBe(false);
  });

  it("TA pill warns from the real expires_in (inside the 5-min band)", async () => {
    server.use(
      engineStatus(),
      taSessionStatus({ present: true, reason: null, expires_in: 120 }),
    );
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const pill = await screen.findByTestId("painel-ta-pill");
    await waitFor(() => expect(pill).toHaveTextContent("Expira em"));
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

  it("enabled=true with state=idle keeps switch ON and stop fires", async () => {
    let stopCalled = false;
    server.use(
      engineStatus({ state: "idle", enabled: true }),
      taSessionStatus(),
      http.post("http://localhost:3000/api/api/v1/engine/stop", () => {
        stopCalled = true;
        return HttpResponse.json({ status: "stopping" }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const sw = await screen.findByTestId("painel-motor-switch");
    // Switch must be ON (enabled=true overrides state=idle)
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "true"));

    // Motor label should show "Ligado" (not "Desligado") when enabled=true
    const label = screen.getByTestId("painel-motor-state");
    expect(label).toHaveTextContent("Ligado");

    // Clicking fires stop (not depth menu)
    await user.click(sw);
    await waitFor(() => expect(stopCalled).toBe(true));
    expect(screen.queryByTestId("painel-depth-menu")).toBeNull();
  });

  it("enabled=false with state=idle toggle opens depth menu (not stop)", async () => {
    let stopCalled = false;
    server.use(
      engineStatus({ state: "idle", enabled: false }),
      taSessionStatus(),
      http.post("http://localhost:3000/api/api/v1/engine/stop", () => {
        stopCalled = true;
        return HttpResponse.json({ status: "stopping" }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const sw = await screen.findByTestId("painel-motor-switch");
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));

    // Clicking idle+disabled switch opens depth menu, never calls stop
    await user.click(sw);
    await screen.findByTestId("painel-depth-menu");
    await new Promise((r) => setTimeout(r, 50));
    expect(stopCalled).toBe(false);
  });

  // ---------------------------------------------------------------------------
  // R2 client gate tests (260629-e69)
  // ---------------------------------------------------------------------------

  it("source=tripadvisor + no valid session blocks depth menu on switch click", async () => {
    server.use(
      engineStatus({ source: "tripadvisor", enabled: false, state: "idle" }),
      taSessionStatus({ present: false, reason: null }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);
    const sw = await screen.findByTestId("painel-motor-switch");
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));
    await user.click(sw);
    await new Promise((r) => setTimeout(r, 50));
    // taBlocked → depth menu must NOT open
    expect(screen.queryByTestId("painel-depth-menu")).toBeNull();
  });

  it("source=tripadvisor + valid session → switch click opens depth menu", async () => {
    server.use(
      engineStatus({ source: "tripadvisor", enabled: false, state: "idle" }),
      taSessionStatus({ present: true, expires_in: 1200 }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);
    const sw = await screen.findByTestId("painel-motor-switch");
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));
    await user.click(sw);
    // Valid session → depth menu opens normally
    await screen.findByTestId("painel-depth-menu");
  });

  it("409 from startEngine with TA detail message shows the backend message", async () => {
    const TA_MSG =
      "Motor TripAdvisor requer uma sessão com TTL válido — injete um cURL primeiro.";
    const spy = vi.spyOn(toast, "error");
    server.use(
      engineStatus({ source: "tripadvisor", enabled: false, state: "idle" }),
      taSessionStatus({ present: true, expires_in: 1200 }),
      http.post("http://localhost:3000/api/api/v1/engine/start", () =>
        HttpResponse.json({ detail: TA_MSG }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);
    const sw = await screen.findByTestId("painel-motor-switch");
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));
    await user.click(sw);
    await screen.findByTestId("painel-depth-menu");
    await user.click(screen.getByTestId("painel-depth-nascente"));
    // 409 detail must surface via toast.error (not the hardcoded "Motor já está em execução.")
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(expect.stringContaining("TTL")),
    );
  });
});
