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
  taSessionStatus,
} from "@/mocks/handlers/engine";

import { renderWithClient } from "../../cms/__tests__/test-utils";

const MODE_URL = "http://localhost:3000/api/api/v1/engine/mode";
const START_URL = "http://localhost:3000/api/api/v1/engine/start";

beforeEach(() => {
  server.resetHandlers();
});

afterEach(() => {
  vi.restoreAllMocks();
});

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

    const pausar = await screen.findByTestId("painel-motor-pausar");
    await waitFor(() => expect(pausar).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByTestId("painel-motor-ligar")).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByTestId("painel-motor-state")).toHaveTextContent(
      "Pausado",
    );
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

    const ligar = await screen.findByTestId("painel-motor-ligar");
    await waitFor(() => expect(ligar).toHaveAttribute("aria-pressed", "true"));
    await user.click(screen.getByTestId("painel-motor-pausar"));
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

    await screen.findByTestId("painel-motor-desligar");
    await user.click(screen.getByTestId("painel-motor-desligar"));
    await waitFor(() => expect(modeBody).toMatchObject({ mode: "DESLIGADO" }));
  });

  it("warm Ligar (mode PAUSADO, engine enabled) POSTs mode LIGADO and does NOT open the depth menu", async () => {
    let modeBody: { mode?: string } | null = null;
    server.use(
      engineStatus({ mode: "PAUSADO", editing_unlocked: true, enabled: true, state: "running" }),
      taSessionStatus(),
      http.post(MODE_URL, async ({ request }) => {
        modeBody = (await request.json()) as { mode?: string };
        return HttpResponse.json({ mode: "LIGADO", editing_unlocked: false });
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);

    const pausar = await screen.findByTestId("painel-motor-pausar");
    await waitFor(() => expect(pausar).toHaveAttribute("aria-pressed", "true"));
    await user.click(screen.getByTestId("painel-motor-ligar"));
    await waitFor(() => expect(modeBody).toMatchObject({ mode: "LIGADO" }));
    // A warm resume never opens the depth picker.
    expect(screen.queryByTestId("painel-depth-menu")).toBeNull();
  });

  it("desynced Ligar (mode LIGADO but engine idle+disabled) RECOVERS — opens the depth picker", async () => {
    // Regression: an R1 session-expiry auto-off (or an idle /stop) can leave
    // mode=LIGADO while enabled=false & state=idle. Clicking Ligar must fall through
    // to cold-start recovery, not no-op on the mode===LIGADO guard.
    let startBody: { depth?: string } | null = null;
    server.use(
      engineStatus({ mode: "LIGADO", editing_unlocked: false, state: "idle", enabled: false }),
      taSessionStatus(),
      http.post(START_URL, async ({ request }) => {
        startBody = (await request.json()) as { depth?: string };
        return HttpResponse.json({ status: "started", ufs_total: 27 }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const ligar = await screen.findByTestId("painel-motor-ligar");
    await user.click(ligar);
    // Recovery path: the depth picker opens (was a no-op before the guard fix).
    await screen.findByTestId("painel-depth-menu");
    await user.click(screen.getByTestId("painel-depth-nascente"));
    await waitFor(() => expect(startBody).not.toBeNull());
    expect(startBody).toMatchObject({ depth: "nascente" });
  });

  it("genuinely-running Ligar (mode LIGADO, enabled) stays a no-op — no depth picker", async () => {
    server.use(
      engineStatus({ mode: "LIGADO", editing_unlocked: false, state: "running", enabled: true }),
      taSessionStatus(),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const ligar = await screen.findByTestId("painel-motor-ligar");
    await waitFor(() => expect(ligar).toHaveAttribute("aria-pressed", "true"));
    await user.click(ligar);
    // A truly-on engine: Ligar must do nothing (no picker, no restart).
    expect(screen.queryByTestId("painel-depth-menu")).toBeNull();
  });

  it("cold Ligar (engine off) opens the depth menu; picking a depth fires POST /start WITH that depth", async () => {
    let startBody: { depth?: string } | null = null;
    server.use(
      engineStatus({ mode: "DESLIGADO", editing_unlocked: true, state: "idle", enabled: false }),
      taSessionStatus(),
      http.post(START_URL, async ({ request }) => {
        startBody = (await request.json()) as { depth?: string };
        return HttpResponse.json(
          { status: "started", ufs_total: 27 },
          { status: 202 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const ligar = await screen.findByTestId("painel-motor-ligar");
    await waitFor(() => expect(ligar).toHaveAttribute("aria-pressed", "false"));
    // Cold Ligar opens the depth menu — it must NOT start without a depth.
    await user.click(ligar);
    await screen.findByTestId("painel-depth-menu");

    await user.click(screen.getByTestId("painel-depth-nascente_rio"));
    await waitFor(() => expect(startBody).not.toBeNull());
    expect(startBody).toMatchObject({ depth: "nascente_rio" });
  });

  it("cold Ligar defaults to Todo o Brasil — no ufs in the /start body", async () => {
    let startBody: Record<string, unknown> | null = null;
    server.use(
      engineStatus({ mode: "DESLIGADO", editing_unlocked: true, state: "idle", enabled: false }),
      taSessionStatus(),
      http.post(START_URL, async ({ request }) => {
        startBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { status: "started", ufs_total: 27 },
          { status: 202 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const ligar = await screen.findByTestId("painel-motor-ligar");
    await user.click(ligar);
    await screen.findByTestId("painel-depth-menu");
    // Abrangência defaults to "" (Todo o Brasil) → ufs omitted (backend uses 27).
    expect(screen.getByTestId("painel-uf-select")).toHaveValue("");
    await user.click(screen.getByTestId("painel-depth-nascente"));
    await waitFor(() => expect(startBody).not.toBeNull());
    expect(startBody).not.toHaveProperty("ufs");
  });

  it("cold Ligar scoped to a UF fires POST /start with ufs=[UF] (source-independent)", async () => {
    let startBody: { depth?: string; ufs?: string[] } | null = null;
    server.use(
      engineStatus({ mode: "DESLIGADO", editing_unlocked: true, state: "idle", enabled: false }),
      taSessionStatus(),
      http.post(START_URL, async ({ request }) => {
        startBody = (await request.json()) as { depth?: string; ufs?: string[] };
        return HttpResponse.json(
          { status: "started", ufs_total: 1 },
          { status: 202 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const ligar = await screen.findByTestId("painel-motor-ligar");
    await user.click(ligar);
    await screen.findByTestId("painel-depth-menu");
    await user.selectOptions(screen.getByTestId("painel-uf-select"), "SP");
    await user.click(screen.getByTestId("painel-depth-nascente_rio"));
    await waitFor(() => expect(startBody).not.toBeNull());
    expect(startBody).toMatchObject({ depth: "nascente_rio", ufs: ["SP"] });
  });

  it("typing a per-UF cap sends max_atrativos_per_uf in the /start body", async () => {
    let startBody: Record<string, unknown> | null = null;
    server.use(
      engineStatus({ mode: "DESLIGADO", editing_unlocked: true, state: "idle", enabled: false }),
      taSessionStatus(),
      http.post(START_URL, async ({ request }) => {
        startBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { status: "started", ufs_total: 27 },
          { status: 202 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const ligar = await screen.findByTestId("painel-motor-ligar");
    await user.click(ligar);
    await screen.findByTestId("painel-depth-menu");
    await user.type(screen.getByTestId("painel-max-per-uf"), "5");
    await user.click(screen.getByTestId("painel-depth-nascente"));
    await waitFor(() => expect(startBody).not.toBeNull());
    expect(startBody).toMatchObject({ depth: "nascente", max_atrativos_per_uf: 5 });
  });

  it("empty per-UF cap omits max_atrativos_per_uf from the /start body", async () => {
    let startBody: Record<string, unknown> | null = null;
    server.use(
      engineStatus({ mode: "DESLIGADO", editing_unlocked: true, state: "idle", enabled: false }),
      taSessionStatus(),
      http.post(START_URL, async ({ request }) => {
        startBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { status: "started", ufs_total: 27 },
          { status: 202 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const ligar = await screen.findByTestId("painel-motor-ligar");
    await user.click(ligar);
    await screen.findByTestId("painel-depth-menu");
    // Leave the cap input empty → full sweep, field must not be sent.
    await user.click(screen.getByTestId("painel-depth-nascente"));
    await waitFor(() => expect(startBody).not.toBeNull());
    expect(startBody).not.toHaveProperty("max_atrativos_per_uf");
  });

  it("clicking outside the motor wrapper closes the open depth menu", async () => {
    server.use(
      engineStatus({ source: "tripadvisor", enabled: false, state: "idle", mode: "DESLIGADO" }),
      taSessionStatus({ present: true, expires_in: 1200 }),
      engineStartSuccess(),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);

    const ligar = await screen.findByTestId("painel-motor-ligar");
    await user.click(ligar);
    await screen.findByTestId("painel-depth-menu");

    // Click somewhere outside the motor wrapper (the page title) → menu closes.
    await user.click(screen.getByTestId("painel-topbar"));
    await waitFor(() =>
      expect(screen.queryByTestId("painel-depth-menu")).toBeNull(),
    );
  });

  it("opening the depth menu without picking does NOT fire POST /start", async () => {
    let startCalled = false;
    server.use(
      engineStatus({ mode: "DESLIGADO", state: "idle", enabled: false }),
      taSessionStatus(),
      http.post(START_URL, () => {
        startCalled = true;
        return HttpResponse.json({ status: "started" }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="Painel" subtitle="x" />);

    const ligar = await screen.findByTestId("painel-motor-ligar");
    await user.click(ligar);
    await screen.findByTestId("painel-depth-menu");
    await new Promise((r) => setTimeout(r, 50));
    expect(startCalled).toBe(false);
  });

  // ---------------------------------------------------------------------------
  // TA session pill / source (unchanged by the tri-state)
  // ---------------------------------------------------------------------------

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

    expect(await screen.findByTestId("painel-ta-pill")).toHaveTextContent(
      "Pronta",
    );
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

  it("source=tripadvisor + no valid session blocks the depth menu on cold Ligar", async () => {
    server.use(
      engineStatus({ source: "tripadvisor", enabled: false, state: "idle", mode: "DESLIGADO" }),
      taSessionStatus({ present: false, reason: null }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);
    const ligar = await screen.findByTestId("painel-motor-ligar");
    await user.click(ligar);
    await new Promise((r) => setTimeout(r, 50));
    // taBlocked → depth menu must NOT open
    expect(screen.queryByTestId("painel-depth-menu")).toBeNull();
  });

  it("source=tripadvisor + valid session → cold Ligar opens the depth menu", async () => {
    server.use(
      engineStatus({ source: "tripadvisor", enabled: false, state: "idle", mode: "DESLIGADO" }),
      taSessionStatus({ present: true, expires_in: 1200 }),
      engineStartSuccess(),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);
    const ligar = await screen.findByTestId("painel-motor-ligar");
    await user.click(ligar);
    await screen.findByTestId("painel-depth-menu");
  });

  it("source=tripadvisor: picking a depth fires POST /start with {depth, source: 'tripadvisor'}", async () => {
    let startBody: { depth?: string; source?: string } | null = null;
    server.use(
      engineStatus({ source: "tripadvisor", state: "idle", enabled: false, mode: "DESLIGADO" }),
      taSessionStatus({ present: true, expires_in: 1200 }),
      http.post(START_URL, async ({ request }) => {
        startBody = (await request.json()) as { depth?: string; source?: string };
        return HttpResponse.json(
          { status: "started", ufs_total: 27 },
          { status: 202 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);
    const ligar = await screen.findByTestId("painel-motor-ligar");
    await user.click(ligar);
    await screen.findByTestId("painel-depth-menu");
    await user.click(screen.getByTestId("painel-depth-nascente_rio"));
    await waitFor(() => expect(startBody).not.toBeNull());
    expect(startBody).toMatchObject({
      depth: "nascente_rio",
      source: "tripadvisor",
    });
  });

  it("409 from startEngine with TA detail message shows the backend message", async () => {
    const TA_MSG =
      "Motor TripAdvisor requer uma sessão com TTL válido — injete um cURL primeiro.";
    const spy = vi.spyOn(toast, "error");
    server.use(
      engineStatus({ source: "tripadvisor", enabled: false, state: "idle", mode: "DESLIGADO" }),
      taSessionStatus({ present: true, expires_in: 1200 }),
      http.post(START_URL, () => HttpResponse.json({ detail: TA_MSG }, { status: 409 })),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelTopbar title="P" subtitle="s" />);
    const ligar = await screen.findByTestId("painel-motor-ligar");
    await user.click(ligar);
    await screen.findByTestId("painel-depth-menu");
    await user.click(screen.getByTestId("painel-depth-nascente"));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(expect.stringContaining("TTL")),
    );
  });

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
    await screen.findByTestId("painel-motor-pausar");
    await user.click(screen.getByTestId("painel-motor-pausar"));
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

  describe("sync indicator", () => {
    it("shows Sincronizando + source + UF progress when motor is running", async () => {
      server.use(
        engineStatus({
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
        expect(ind).toHaveTextContent("Sincronizando");
        expect(ind).toHaveTextContent("TripAdvisor");
        expect(ind).toHaveTextContent("3/27");
      });
    });

    it("shows 'Execução parada' when the runtime is idle", async () => {
      server.use(
        engineStatus({ enabled: false, state: "idle" }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      const ind = await screen.findByTestId("sync-indicator");
      await waitFor(() => expect(ind).toHaveTextContent("Execução parada"));
    });

    // -------------------------------------------------------------------------
    // Tri-state sync_phase (idle · syncing · synced), driven by /engine/status.
    // Asserted via the sync-indicator-label span + its data-phase attribute.
    // -------------------------------------------------------------------------

    it("sync_phase='idle' → gray 'Execução parada' label with data-phase=idle", async () => {
      server.use(
        engineStatus({ sync_phase: "idle", enabled: false, state: "idle" }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      // Re-query INSIDE waitFor: each phase is a separate JSX branch, so when the
      // status query resolves React unmounts the pre-resolution span and mounts a
      // new one — a reference captured before resolution goes stale.
      await waitFor(() => {
        const label = screen.getByTestId("sync-indicator-label");
        expect(label).toHaveAttribute("data-phase", "idle");
        expect(label).toHaveTextContent("Execução parada");
        expect(label).toHaveStyle({ color: "var(--painel-muted)" });
      });
    });

    it("sync_phase='syncing' → yellow 'Sincronizando' label with data-phase=syncing", async () => {
      server.use(
        engineStatus({
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
      await waitFor(() => {
        const label = screen.getByTestId("sync-indicator-label");
        expect(label).toHaveAttribute("data-phase", "syncing");
        expect(label).toHaveTextContent("Sincronizando");
        expect(label).toHaveTextContent("3/27");
        expect(label).toHaveStyle({ color: "var(--status-dlq)" });
      });
    });

    it("sync_phase='synced' → green 'Sincronizado' label with data-phase=synced", async () => {
      server.use(
        engineStatus({ sync_phase: "synced", enabled: false, state: "idle" }),
        taSessionStatus(),
      );
      renderWithClient(<PainelTopbar title="P" subtitle="s" />);
      await waitFor(() => {
        const label = screen.getByTestId("sync-indicator-label");
        expect(label).toHaveAttribute("data-phase", "synced");
        expect(label).toHaveTextContent("Sincronizado");
        expect(label).toHaveStyle({ color: "var(--status-mar)" });
      });
    });
  });
});
