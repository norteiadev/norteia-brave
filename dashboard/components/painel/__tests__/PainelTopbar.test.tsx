import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelTopbar } from "@/components/painel/PainelTopbar";
import { ENGINE_REFETCH_INTERVAL_MS } from "@/lib/engine-api";
import { server } from "@/mocks/server";
import {
  engineStatus,
  engineStatusFixture,
  taSessionStatus,
} from "@/mocks/handlers/engine";

import { renderWithClient } from "../../cms/__tests__/test-utils";

const MODE_URL = "http://localhost:3000/api/api/v1/engine/mode";
const START_URL = "http://localhost:3000/api/api/v1/engine/start";

beforeEach(() => {
  server.resetHandlers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** The compact motor button shows the current mode once /status resolved. */
async function waitForMode(label: string) {
  await waitFor(() =>
    expect(screen.getByTestId("painel-motor-state")).toHaveTextContent(label),
  );
}

/** Open the motor menu and pick one mode item. */
async function pickMode(user: ReturnType<typeof userEvent.setup>, testid: string) {
  await user.click(await screen.findByTestId("painel-motor-btn"));
  await user.click(await screen.findByTestId(testid));
}

describe("PainelTopbar", () => {
  it("renders the page title and subtitle", async () => {
    server.use(engineStatus(), taSessionStatus());
    renderWithClient(
      <PainelTopbar title="Painel" subtitle="Quadro de processamento" />,
    );

    expect(await screen.findByTestId("painel-topbar")).toHaveTextContent(
      "Painel",
    );
    expect(screen.getByTestId("painel-topbar")).toHaveTextContent(
      "Quadro de processamento",
    );
  });

  // ---------------------------------------------------------------------------
  // Tri-state motor: Ligar / Pausar / Desligar (POST /api/v1/engine/mode)
  // ---------------------------------------------------------------------------

  it("marks the active mode from status.mode (PAUSADO → Pausar pressed, label 'Pausado')", async () => {
    server.use(
      engineStatus({ mode: "PAUSADO", editing_unlocked: true, enabled: true, state: "running" }),
      taSessionStatus(),
    );
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    await waitForMode("Pausado");
    await userEvent.setup().click(screen.getByTestId("painel-motor-btn"));
    expect(screen.getByTestId("painel-motor-pausar")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("painel-motor-ligar")).toHaveAttribute("aria-checked", "false");
  });

  it("Pausar POSTs engine mode PAUSADO", async () => {
    let modeBody: { mode?: string } | null = null;
    server.use(
      engineStatus({ mode: "LIGADO", editing_unlocked: false, enabled: true, state: "running" }),
      taSessionStatus(),
      http.post(MODE_URL, async ({ request }) => {
        modeBody = (await request.json()) as { mode?: string };
        return HttpResponse.json({ mode: "PAUSADO", editing_unlocked: true });
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    await waitForMode("Ligado");
    await pickMode(user, "painel-motor-pausar");
    await waitFor(() => expect(modeBody).toMatchObject({ mode: "PAUSADO" }));
  });

  it("Desligar POSTs engine mode DESLIGADO", async () => {
    let modeBody: { mode?: string } | null = null;
    server.use(
      engineStatus({ mode: "LIGADO", editing_unlocked: false, enabled: true, state: "running" }),
      taSessionStatus(),
      http.post(MODE_URL, async ({ request }) => {
        modeBody = (await request.json()) as { mode?: string };
        return HttpResponse.json({ mode: "DESLIGADO", editing_unlocked: true });
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    await waitForMode("Ligado");
    await pickMode(user, "painel-motor-desligar");
    await waitFor(() => expect(modeBody).toMatchObject({ mode: "DESLIGADO" }));
  });

  it("warm Ligar (mode PAUSADO, engine enabled) POSTs mode LIGADO and does NOT open Varreduras", async () => {
    let modeBody: { mode?: string } | null = null;
    const onStart = vi.fn();
    server.use(
      engineStatus({ mode: "PAUSADO", editing_unlocked: true, enabled: true, state: "running" }),
      taSessionStatus(),
      http.post(MODE_URL, async ({ request }) => {
        modeBody = (await request.json()) as { mode?: string };
        return HttpResponse.json({ mode: "LIGADO", editing_unlocked: false });
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" onStart={onStart} />);

    await waitForMode("Pausado");
    await pickMode(user, "painel-motor-ligar");
    await waitFor(() => expect(modeBody).toMatchObject({ mode: "LIGADO" }));
    // A warm resume never sends the operator to the start panel.
    expect(onStart).not.toHaveBeenCalled();
  });

  it("cold Ligar (engine off) opens Varreduras instead of a popover, and starts nothing", async () => {
    const onStart = vi.fn();
    let starts = 0;
    server.use(
      engineStatus({ mode: "DESLIGADO", editing_unlocked: true, state: "idle", enabled: false }),
      taSessionStatus(),
      http.post(START_URL, () => {
        starts += 1;
        return HttpResponse.json({ status: "started", ufs_total: 27 }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" onStart={onStart} />);

    await waitForMode("Desligado");
    await pickMode(user, "painel-motor-ligar");
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(starts).toBe(0);
  });

  it("desynced Ligar (mode LIGADO but engine idle+disabled) RECOVERS — opens Varreduras", async () => {
    // Regression: an R1 session-expiry auto-off (or an idle /stop) can leave
    // mode=LIGADO while enabled=false & state=idle. Clicking Ligar must fall through
    // to cold-start recovery, not no-op on the mode===LIGADO guard.
    const onStart = vi.fn();
    server.use(
      engineStatus({ mode: "LIGADO", editing_unlocked: false, state: "idle", enabled: false }),
      taSessionStatus(),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" onStart={onStart} />);

    await waitForMode("Ligado");
    await pickMode(user, "painel-motor-ligar");
    expect(onStart).toHaveBeenCalledTimes(1);
  });

  it("genuinely-running Ligar (mode LIGADO, enabled) stays a no-op", async () => {
    const onStart = vi.fn();
    server.use(
      engineStatus({ mode: "LIGADO", editing_unlocked: false, state: "running", enabled: true }),
      taSessionStatus(),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" onStart={onStart} />);

    await waitForMode("Ligado");
    await pickMode(user, "painel-motor-ligar");
    // A truly-on engine: Ligar must do nothing (no start panel, no restart).
    expect(onStart).not.toHaveBeenCalled();
  });

  // ---------------------------------------------------------------------------
  // TA session pill / source (unchanged by the tri-state)
  // ---------------------------------------------------------------------------

  describe("TA session", () => {
    it("ok: shows the remaining TTL as m:ss", async () => {
      server.use(engineStatus(), taSessionStatus({ present: true, reason: null, expires_in: 1200 }));
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      const item = await screen.findByTestId("painel-ta-pill");
      await waitFor(() => expect(item).toHaveAttribute("data-state", "ok"));
      expect(item).toHaveTextContent("20:00");
    });

    it("expiring: inside the 5-min band from the real expires_in", async () => {
      server.use(engineStatus(), taSessionStatus({ present: true, reason: null, expires_in: 120 }));
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      const item = await screen.findByTestId("painel-ta-pill");
      await waitFor(() => expect(item).toHaveAttribute("data-state", "expiring"));
      expect(item).toHaveTextContent("2:00");
    });

    it("expired: no session and no bootstrap reason", async () => {
      server.use(engineStatus(), taSessionStatus({ present: false, reason: null, expires_in: 0 }));
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      const item = await screen.findByTestId("painel-ta-pill");
      await waitFor(() => expect(item).toHaveAttribute("data-state", "expired"));
      expect(item).toHaveTextContent("Expirada");
    });

    it("needs_bootstrap: asks for a bootstrap", async () => {
      server.use(engineStatus(), taSessionStatus({ present: false, reason: "needs_bootstrap" }));
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      const item = await screen.findByTestId("painel-ta-pill");
      await waitFor(() => expect(item).toHaveAttribute("data-state", "needs_bootstrap"));
      expect(item).toHaveTextContent("Precisa bootstrap");
    });

    it("counts down locally between polls and turns amber at 5 min", async () => {
      // Only the interval + clock are faked: MSW and React Query keep their real timeouts.
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
      server.use(engineStatus(), taSessionStatus({ present: true, reason: null, expires_in: 302 }));
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      const item = await screen.findByTestId("painel-ta-pill");
      await waitFor(() => expect(item).toHaveTextContent("5:02"));
      expect(item).toHaveAttribute("data-state", "ok");

      act(() => vi.advanceTimersByTime(3_000));
      expect(item).toHaveTextContent("4:59");
      expect(item).toHaveAttribute("data-state", "expiring");
    });

    it("clamps the countdown at zero and shows the session as expired", async () => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
      server.use(engineStatus(), taSessionStatus({ present: true, reason: null, expires_in: 2 }));
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      const item = await screen.findByTestId("painel-ta-pill");
      await waitFor(() => expect(item).toHaveTextContent("0:02"));

      act(() => vi.advanceTimersByTime(5_000));
      expect(item).toHaveAttribute("data-state", "expired");
      expect(item).toHaveTextContent("Expirada");
    });
  });

  describe("norteia-api health", () => {
    it("up: shows the API as online", async () => {
      server.use(
        engineStatus({ norteia_api: { up: true, reason: null, pending: 0, rejected: 0 } }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);

      const item = await screen.findByTestId("painel-api-status");
      expect(item).toHaveAttribute("data-state", "up");
      expect(item).toHaveTextContent("no ar");
      expect(item).not.toHaveTextContent("pendente");
    });

    it("unreachable: shows the probe reason", async () => {
      server.use(
        engineStatus({ norteia_api: { up: false, reason: "unreachable", pending: 0 } }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);

      const item = await screen.findByTestId("painel-api-status");
      expect(item).toHaveAttribute("data-state", "down");
      expect(item).toHaveTextContent("inacessível");
      expect(item).toHaveAttribute("title", expect.stringContaining("unreachable"));
    });

    it("an ingest refusal names the status and explains it", async () => {
      server.use(
        engineStatus({ norteia_api: { up: false, reason: "ingest:401", pending: 4 } }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);

      const item = await screen.findByTestId("painel-api-status");
      expect(item).toHaveTextContent("ingest 401");
      expect(item).toHaveAttribute("title", expect.stringContaining("token de ingestão"));
      expect(item).toHaveTextContent("4 pendentes");
    });

    it("disabled: externals off (up null) is its own state", async () => {
      server.use(
        engineStatus({ norteia_api: { up: null, reason: null, pending: 0 } }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);

      const item = await screen.findByTestId("painel-api-status");
      expect(item).toHaveAttribute("data-state", "disabled");
      expect(item).toHaveTextContent("desligada");
    });

    it("shows pending and rejected counts only when > 0", async () => {
      server.use(
        engineStatus({ norteia_api: { up: true, reason: null, pending: 1, rejected: 3 } }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);

      const item = await screen.findByTestId("painel-api-status");
      expect(item).toHaveTextContent("1 pendente");
      expect(item).toHaveTextContent("3 rejeitados");
    });

    it("hides the item when the server does not report norteia_api", async () => {
      server.use(engineStatus(), taSessionStatus());
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);

      await screen.findByTestId("painel-ta-pill");
      expect(screen.queryByTestId("painel-api-status")).toBeNull();
    });
  });

  it("status bar carries the currency switch and no simulated spend chip", async () => {
    server.use(engineStatus(), taSessionStatus());
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);

    const bar = await screen.findByRole("group", { name: "Status do serviço" });
    const moeda = screen.getByRole("group", { name: "Moeda" });
    expect(bar).toContainElement(moeda);
    // "Hoje" needs the Phase 1 /cost today block — never a made-up number.
    expect(bar).not.toHaveTextContent("Hoje");
  });

  it("polls engine + TA status on the existing interval", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    let statusHits = 0;
    let sessionHits = 0;
    server.use(
      http.get("http://localhost:3000/api/api/v1/engine/status", () => {
        statusHits += 1;
        return HttpResponse.json(engineStatusFixture());
      }),
      http.get("http://localhost:3000/api/api/v1/tripadvisor/session/status", () => {
        sessionHits += 1;
        return HttpResponse.json({ present: true, expires_in: 1200, reason: null });
      }),
    );
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);
    await waitFor(() => expect(statusHits).toBe(1));
    await waitFor(() => expect(sessionHits).toBe(1));

    act(() => vi.advanceTimersByTime(ENGINE_REFETCH_INTERVAL_MS - 1_000));
    expect(statusHits).toBe(1);
    act(() => vi.advanceTimersByTime(1_000));
    await waitFor(() => expect(statusHits).toBe(2));
    await waitFor(() => expect(sessionHits).toBe(2));
  });

  it("source label renders 'TripAdvisor' by default", async () => {
    server.use(engineStatus({ source: null }), taSessionStatus());
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    expect(await screen.findByTestId("painel-source")).toHaveTextContent(
      "TripAdvisor",
    );
  });

  it("source label renders 'TripAdvisor' when status.source=tripadvisor", async () => {
    server.use(engineStatus({ source: "tripadvisor" }), taSessionStatus());
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const src = await screen.findByTestId("painel-source");
    await waitFor(() => expect(src).toHaveTextContent("TripAdvisor"));
  });

  // ---------------------------------------------------------------------------
  // R2 client gate — cold Ligar honors the TripAdvisor session gate
  // ---------------------------------------------------------------------------


  it("surfaces a 422 invalid-mode error via toast", async () => {
    const spy = vi.spyOn(toast, "error");
    server.use(
      engineStatus({ mode: "LIGADO", enabled: true, state: "running" }),
      taSessionStatus(),
      http.post(MODE_URL, () =>
        HttpResponse.json(
          { detail: "mode must be 'LIGADO', 'PAUSADO', or 'DESLIGADO'" },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);
    await waitForMode("Ligado");
    await pickMode(user, "painel-motor-pausar");
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(expect.stringContaining("mode must be")),
    );
  });

  // ---------------------------------------------------------------------------
  // Logs icon button + sync indicator (unchanged)
  // ---------------------------------------------------------------------------

  it("logs icon button opens the logs sidebar", async () => {
    server.use(
      engineStatus({ enabled: true, state: "running", source: "tripadvisor" }),
      taSessionStatus(),
      http.get("http://localhost:3000/api/api/v1/logs", () =>
        HttpResponse.json({ source: "tripadvisor", lines: [], cursor: 0 }),
      ),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);
    const btn = await screen.findByTestId("logs-icon-btn");
    await user.click(btn);
    await screen.findByTestId("painel-logs-panel");
  });

  describe("live indicator", () => {
    it("motor LIGADO + syncing → 'Coletando' with the UF progress", async () => {
      server.use(
        engineStatus({
          mode: "LIGADO",
          sync_phase: "syncing",
          enabled: true,
          state: "running",
          source: "tripadvisor",
          ufs_done: 3,
          ufs_total: 27,
          current_uf: "SP",
        }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      const ind = await screen.findByTestId("sync-indicator");
      await waitFor(() => {
        expect(ind).toHaveTextContent("Coletando");
        expect(ind).toHaveTextContent("3/27");
        expect(ind).toHaveTextContent("SP");
        expect(ind).toHaveAttribute("title", expect.stringContaining("TripAdvisor"));
      });
      expect(screen.getByTestId("sync-indicator-label")).toHaveAttribute("data-phase", "syncing");
    });

    it("falls back to enabled when the server omits sync_phase", async () => {
      server.use(
        engineStatus({ mode: "LIGADO", enabled: true, state: "running", ufs_done: 1, ufs_total: 2 }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      const ind = await screen.findByTestId("sync-indicator");
      await waitFor(() => expect(ind).toHaveTextContent("Coletando"));
    });

    it("syncing while PAUSADO → paused, not live", async () => {
      server.use(
        engineStatus({ mode: "PAUSADO", sync_phase: "syncing", enabled: true, state: "running", ufs_done: 3, ufs_total: 27 }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      const ind = await screen.findByTestId("sync-indicator");
      await waitFor(() => expect(ind).toHaveTextContent("Coleta pausada"));
      expect(ind).not.toHaveTextContent("Coletando");
    });

    it("sync_phase='synced' → 'Sincronizado'", async () => {
      server.use(
        engineStatus({ sync_phase: "synced", enabled: false, state: "idle" }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      await waitFor(() => {
        const label = screen.getByTestId("sync-indicator-label");
        expect(label).toHaveAttribute("data-phase", "synced");
        expect(label).toHaveTextContent("Sincronizado");
      });
    });

    it("idle runtime → 'Coleta parada'", async () => {
      server.use(
        engineStatus({ sync_phase: "idle", enabled: false, state: "idle" }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      await waitFor(() => {
        const label = screen.getByTestId("sync-indicator-label");
        expect(label).toHaveAttribute("data-phase", "idle");
        expect(label).toHaveTextContent("Coleta parada");
      });
    });
  });

  // ---------------------------------------------------------------------------
  // Compact motor button + menu (keyboard, outside click)
  // ---------------------------------------------------------------------------

  describe("motor menu", () => {
    it("lists the three modes with what each does, checked on the current one", async () => {
      server.use(engineStatus({ mode: "LIGADO", enabled: true, state: "running" }), taSessionStatus());
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      await waitForMode("Ligado");

      const btn = screen.getByTestId("painel-motor-btn");
      expect(btn).toHaveAttribute("aria-haspopup", "menu");
      expect(btn).toHaveAttribute("aria-expanded", "false");
      await user.click(btn);
      expect(btn).toHaveAttribute("aria-expanded", "true");

      const menu = screen.getByRole("menu", { name: "Modo do motor" });
      expect(screen.getAllByRole("menuitemradio")).toHaveLength(3);
      expect(menu).toHaveTextContent("Pausado");
      expect(menu).toHaveTextContent("Desligado");
      const ligado = screen.getByRole("menuitemradio", { name: /^Ligado/ });
      expect(ligado).toHaveAccessibleName(expect.stringContaining("travad"));
      expect(ligado).toHaveAttribute("aria-checked", "true");
      // Opening focuses the current mode.
      expect(ligado).toHaveFocus();
    });

    it("ArrowDown/ArrowUp move (wrapping) and Enter selects", async () => {
      let modeBody: { mode?: string } | null = null;
      server.use(
        engineStatus({ mode: "LIGADO", enabled: true, state: "running" }),
        taSessionStatus(),
        http.post(MODE_URL, async ({ request }) => {
          modeBody = (await request.json()) as { mode?: string };
          return HttpResponse.json({ mode: "DESLIGADO", editing_unlocked: true });
        }),
      );
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      await waitForMode("Ligado");

      await user.click(screen.getByTestId("painel-motor-btn"));
      await user.keyboard("{ArrowDown}");
      expect(screen.getByTestId("painel-motor-pausar")).toHaveFocus();
      await user.keyboard("{ArrowDown}{ArrowDown}");
      expect(screen.getByTestId("painel-motor-ligar")).toHaveFocus();
      await user.keyboard("{ArrowUp}");
      expect(screen.getByTestId("painel-motor-desligar")).toHaveFocus();
      await user.keyboard("{Enter}");

      await waitFor(() => expect(modeBody).toMatchObject({ mode: "DESLIGADO" }));
      expect(screen.queryByRole("menu", { name: "Modo do motor" })).toBeNull();
    });

    it("Space selects the focused mode", async () => {
      let modeBody: { mode?: string } | null = null;
      server.use(
        engineStatus({ mode: "LIGADO", enabled: true, state: "running" }),
        taSessionStatus(),
        http.post(MODE_URL, async ({ request }) => {
          modeBody = (await request.json()) as { mode?: string };
          return HttpResponse.json({ mode: "PAUSADO", editing_unlocked: true });
        }),
      );
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      await waitForMode("Ligado");

      await user.click(screen.getByTestId("painel-motor-btn"));
      await user.keyboard("{ArrowDown}[Space]");
      await waitFor(() => expect(modeBody).toMatchObject({ mode: "PAUSADO" }));
    });

    it("Escape closes the menu, returns focus to the button and sends nothing", async () => {
      const modeSpy = vi.fn();
      server.use(
        engineStatus({ mode: "LIGADO", enabled: true, state: "running" }),
        taSessionStatus(),
        http.post(MODE_URL, () => {
          modeSpy();
          return HttpResponse.json({ mode: "PAUSADO", editing_unlocked: true });
        }),
      );
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      await waitForMode("Ligado");

      const btn = screen.getByTestId("painel-motor-btn");
      await user.click(btn);
      await user.keyboard("{ArrowDown}{Escape}");
      expect(screen.queryByRole("menu", { name: "Modo do motor" })).toBeNull();
      expect(btn).toHaveFocus();
      expect(btn).toHaveAttribute("aria-expanded", "false");
      expect(modeSpy).not.toHaveBeenCalled();
    });

    it("an outside click closes the menu", async () => {
      server.use(engineStatus({ mode: "LIGADO", enabled: true, state: "running" }), taSessionStatus());
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="Painel" subtitle="s" />);
      await waitForMode("Ligado");

      await user.click(screen.getByTestId("painel-motor-btn"));
      expect(screen.getByRole("menu", { name: "Modo do motor" })).toBeInTheDocument();
      await user.click(screen.getByText("Painel"));
      expect(screen.queryByRole("menu", { name: "Modo do motor" })).toBeNull();
    });
  });

  describe("auto-off toast", () => {
    const SESSION_TOAST =
      "Motor TripAdvisor desligado — sessão expirada. Injete um cURL para reiniciar.";

    async function turnOff(session: Parameters<typeof taSessionStatus>[0]) {
      const warn = vi.spyOn(toast, "warning");
      server.use(
        engineStatus({ mode: "LIGADO", editing_unlocked: false, enabled: true, state: "running" }),
        taSessionStatus(session),
        http.post(MODE_URL, () =>
          HttpResponse.json({ mode: "DESLIGADO", editing_unlocked: true }),
        ),
      );
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);
      await waitForMode("Ligado");
      // The run ends: the next status refetch reports the motor off.
      server.use(engineStatus({ mode: "DESLIGADO", editing_unlocked: true, enabled: false }));
      await pickMode(user, "painel-motor-desligar");
      await waitForMode("Desligado");
      return warn;
    }

    it("a run that ends with a live TA session (e.g. Gerar descrições) raises no session-expired toast", async () => {
      const warn = await turnOff({});
      expect(warn).not.toHaveBeenCalledWith(SESSION_TOAST);
    });

    it("an auto-off with the TA session gone still warns", async () => {
      const warn = await turnOff({ present: false, expires_in: 0 });
      await waitFor(() => expect(warn).toHaveBeenCalledWith(SESSION_TOAST));
    });
  });

  // ---------------------------------------------------------------------------
  // Reasoned-pause banner + Continuar (quick-260918-ohm)
  // ---------------------------------------------------------------------------

  describe("pause_reason banner", () => {
    it("renders no banner when pause_reason is null", async () => {
      server.use(engineStatus({ pause_reason: null }), taSessionStatus());
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      await screen.findByTestId("painel-topbar");
      expect(screen.queryByTestId("painel-pause-banner")).not.toBeInTheDocument();
    });

    it("shows a provider_balance banner with the provider name", async () => {
      server.use(
        engineStatus({
          mode: "PAUSADO",
          pause_reason: { reason: "provider_balance", provider: "tavily", action: "sweep", at: "2026-09-18T00:00:00Z" },
        }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      expect(await screen.findByTestId("painel-pause-banner")).toHaveTextContent(
        "Motor pausado: sem saldo (tavily)",
      );
    });

    it("shows a daily_budget banner", async () => {
      server.use(
        engineStatus({
          mode: "PAUSADO",
          pause_reason: { reason: "daily_budget", provider: null, action: "describe", at: "2026-09-18T00:00:00Z" },
        }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      expect(await screen.findByTestId("painel-pause-banner")).toHaveTextContent(
        "Motor pausado: orçamento diário atingido",
      );
    });

    it("Continuar confirms then resumes the paused describe action", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(true);
      let startBody: unknown = null;
      server.use(
        engineStatus({
          mode: "PAUSADO",
          pause_reason: { reason: "provider_balance", provider: "anthropic", action: "describe", at: "2026-09-18T00:00:00Z" },
        }),
        taSessionStatus(),
        http.post(START_URL, async ({ request }) => {
          startBody = await request.json();
          return HttpResponse.json(
            { status: "started", lane: "atrativos" },
            { status: 202 },
          );
        }),
      );
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      await user.click(await screen.findByTestId("painel-pause-continuar"));

      await waitFor(() =>
        expect(startBody).toMatchObject({ action: "describe" }),
      );
    });

    it("Continuar resumes a paused backfill (Places key swapped)", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(true);
      let startBody: unknown = null;
      server.use(
        engineStatus({
          mode: "PAUSADO",
          pause_reason: { reason: "provider_balance", provider: "google_places", action: "backfill", at: "2026-10-17T00:00:00Z" },
        }),
        taSessionStatus(),
        http.post(START_URL, async ({ request }) => {
          startBody = await request.json();
          return HttpResponse.json({ status: "started", run_id: "r1" }, { status: 202 });
        }),
      );
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      await user.click(await screen.findByTestId("painel-pause-continuar"));

      await waitFor(() => expect(startBody).toEqual({ action: "backfill" }));
    });

    it("Continuar after a manual pause mid-backfill restarts the backfill", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(true);
      let startBody: unknown = null;
      server.use(
        engineStatus({
          mode: "PAUSADO",
          pause_reason: { reason: "manual", provider: null, action: "backfill", at: "2026-10-17T00:00:00Z" },
        }),
        taSessionStatus(),
        http.post(START_URL, async ({ request }) => {
          startBody = await request.json();
          return HttpResponse.json({ status: "started", run_id: "r1" }, { status: 202 });
        }),
      );
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      expect(await screen.findByTestId("painel-pause-banner")).toHaveTextContent(
        "Motor pausado: backfill interrompido",
      );
      await user.click(screen.getByTestId("painel-pause-continuar"));
      await waitFor(() => expect(startBody).toEqual({ action: "backfill" }));
    });

    it("Continuar after a Places wall tells the operator to recreate the worker", async () => {
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
      server.use(
        engineStatus({
          mode: "PAUSADO",
          pause_reason: { reason: "provider_balance", provider: "google_places", action: "backfill", at: "2026-10-17T00:00:00Z" },
        }),
        taSessionStatus(),
      );
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      await user.click(await screen.findByTestId("painel-pause-continuar"));
      expect(confirm).toHaveBeenCalledWith(expect.stringContaining("docker compose up -d worker api"));
    });

    it("Continuar resumes a paused sweep with the last-known depth", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(true);
      let startBody: unknown = null;
      server.use(
        engineStatus({
          mode: "PAUSADO",
          depth: "nascente_rio_mar",
          pause_reason: { reason: "provider_balance", provider: "tavily", action: "sweep", at: "2026-09-18T00:00:00Z" },
        }),
        taSessionStatus(),
        http.post(START_URL, async ({ request }) => {
          startBody = await request.json();
          return HttpResponse.json(
            { status: "started", ufs_total: 27, lane: "both", depth: "nascente_rio_mar", source: "tripadvisor" },
            { status: 202 },
          );
        }),
      );
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      await user.click(await screen.findByTestId("painel-pause-continuar"));

      await waitFor(() =>
        expect(startBody).toMatchObject({ depth: "nascente_rio_mar" }),
      );
    });

    it("Continuar with a null action only lifts the pause (mode LIGADO, no run)", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(true);
      let modeBody: { mode?: string } | null = null;
      const startSpy = vi.fn();
      server.use(
        engineStatus({
          mode: "PAUSADO",
          pause_reason: { reason: "provider_balance", provider: "google_places", action: null, at: "2026-09-24T00:00:00Z" },
        }),
        taSessionStatus(),
        http.post(MODE_URL, async ({ request }) => {
          modeBody = (await request.json()) as { mode?: string };
          return HttpResponse.json({ mode: "LIGADO", editing_unlocked: false });
        }),
        http.post(START_URL, () => {
          startSpy();
          return HttpResponse.json({ status: "started" }, { status: 202 });
        }),
      );
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      await user.click(await screen.findByTestId("painel-pause-continuar"));

      await waitFor(() => expect(modeBody).toMatchObject({ mode: "LIGADO" }));
      expect(startSpy).not.toHaveBeenCalled();
    });

    it("Continuar does nothing when the confirm dialog is cancelled", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(false);
      const startSpy = vi.fn();
      server.use(
        engineStatus({
          mode: "PAUSADO",
          pause_reason: { reason: "daily_budget", provider: null, action: "describe", at: "2026-09-18T00:00:00Z" },
        }),
        taSessionStatus(),
        http.post(START_URL, () => {
          startSpy();
          return HttpResponse.json({ status: "started" }, { status: 202 });
        }),
      );
      const user = userEvent.setup();
      renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

      await user.click(await screen.findByTestId("painel-pause-continuar"));

      expect(startSpy).not.toHaveBeenCalled();
    });
  });
});
