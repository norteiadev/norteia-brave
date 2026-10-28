import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { Toaster } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelVarreduras } from "@/components/painel/PainelVarreduras";
import { engineStatus, taSessionStatus } from "@/mocks/handlers/engine";
import {
  runsListEmpty,
  runsListSuccess,
  runsReprocessSuccess,
  sampleRuns,
} from "@/mocks/handlers/runs";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

const START_URL = "http://localhost:3000/api/api/v1/engine/start";

const requests: { method: string; url: string }[] = [];
let starts: unknown[] = [];

function renderVarreduras() {
  return renderWithClient(
    <>
      <Toaster />
      <PainelVarreduras />
    </>,
  );
}

beforeEach(() => {
  requests.length = 0;
  starts = [];
  server.events.on("request:start", ({ request }) => {
    requests.push({ method: request.method, url: request.url });
  });
  server.use(
    runsListSuccess(),
    runsReprocessSuccess(),
    engineStatus(),
    taSessionStatus(),
    http.post(START_URL, async ({ request }) => {
      starts.push(await request.json());
      return HttpResponse.json({ status: "started", ufs_total: 2 }, { status: 202 });
    }),
  );
});

afterEach(() => {
  server.events.removeAllListeners();
});

describe("PainelVarreduras", () => {
  it("renders one history row per run with labeled source/depth and a status pill", async () => {
    renderVarreduras();

    const rows = await screen.findAllByTestId("runs-row");
    expect(rows).toHaveLength(sampleRuns.length);
    expect(rows[0]).toHaveTextContent("TripAdvisor");
    expect(rows[0]).toHaveTextContent("Nascente → Rio → Mar");
    expect(rows[0]).toHaveTextContent("138");
    expect(rows[0]).toHaveTextContent("CE");
    expect(rows[0]).toHaveTextContent("1 h"); // duration from started/ended

    const pills = screen.getAllByTestId("runs-status-pill");
    expect(pills[0]).toHaveAttribute("data-status", "concluido");
    expect(pills[0]).toHaveTextContent("Concluído");
    expect(pills[1]).toHaveAttribute("data-status", "parcial");
    expect(pills[2]).toHaveAttribute("data-status", "falha");
    // No cost column: the API has no per-run cost yet.
    expect(screen.queryByText("Custo")).toBeNull();
  });

  it("summarizes the last 7 days in KPIs", async () => {
    renderVarreduras();
    await screen.findAllByTestId("runs-row");

    expect(screen.getByTestId("runs-stat-total")).toHaveTextContent("3");
    expect(screen.getByText("1 parcial")).toBeInTheDocument();
    // synced = 138 + 71 + 0 ; failed = 4 + 17 + 20 ; total = 142 + 88 + 20
    expect(screen.getByTestId("runs-stat-synced")).toHaveTextContent("209");
    expect(screen.getByText("de 250 processados")).toBeInTheDocument();
    expect(screen.getByTestId("runs-stat-failed")).toHaveTextContent("41");
  });

  it("Reexecutar fires the run reprocess PATCH", async () => {
    renderVarreduras();

    const buttons = await screen.findAllByTestId("runs-reprocess");
    fireEvent.click(buttons[0]);

    await waitFor(() =>
      expect(
        requests.some(
          (r) => r.method === "PATCH" && r.url.includes("/api/api/v1/runs/run-ce-0001/reprocess"),
        ),
      ).toBe(true),
    );
  });

  it("renders the empty history state", async () => {
    server.use(runsListEmpty());
    renderVarreduras();

    await screen.findByTestId("runs-empty");
    expect(screen.queryAllByTestId("runs-row")).toHaveLength(0);
  });

  it("starts a multi-UF sweep with the chosen depth", async () => {
    renderVarreduras();
    const start = await screen.findByRole("button", { name: "Iniciar varredura" });
    await waitFor(() => expect(screen.getByTestId("ta-session")).toHaveTextContent("Ativa"));
    expect(start).toBeDisabled(); // no UF yet

    const ufs = screen.getByRole("group", { name: "UFs" });
    fireEvent.click(within(ufs).getByRole("button", { name: "CE" }));
    fireEvent.click(within(ufs).getByRole("button", { name: "BA" }));
    fireEvent.click(screen.getByRole("button", { name: "Nascente → Rio → Mar" }));
    fireEvent.click(start);

    await waitFor(() => expect(starts).toHaveLength(1));
    expect(starts[0]).toEqual({
      depth: "nascente_rio_mar",
      source: "tripadvisor",
      ufs: ["BA", "CE"],
    });
    expect(await screen.findByText("Varredura de BA, CE iniciada.")).toBeInTheDocument();
  });

  it("surfaces the server's 409 reason when a run is already active", async () => {
    server.use(
      http.post(START_URL, () =>
        HttpResponse.json({ detail: "Engine already running" }, { status: 409 }),
      ),
    );
    renderVarreduras();
    await waitFor(() => expect(screen.getByTestId("ta-session")).toHaveTextContent("Ativa"));

    fireEvent.click(screen.getByRole("button", { name: "SE" }));
    fireEvent.click(screen.getByRole("button", { name: "Iniciar varredura" }));

    expect(await screen.findByText("Engine already running")).toBeInTheDocument();
  });

  it("blocks the start without a TA session and explains why", async () => {
    server.use(taSessionStatus({ present: false, expires_in: undefined, reason: "needs_bootstrap" }));
    renderVarreduras();

    await waitFor(() =>
      expect(screen.getByTestId("ta-session")).toHaveTextContent("Precisa bootstrap"),
    );
    fireEvent.click(screen.getByRole("button", { name: "SE" }));
    expect(screen.getByRole("button", { name: "Iniciar varredura" })).toBeDisabled();
    expect(
      screen.getByText("Injete uma sessão do TripAdvisor para varrer ou retomar o backfill."),
    ).toBeInTheDocument();
  });

  it("Todo o Brasil starts without ufs, so the server sweeps all 27", async () => {
    renderVarreduras();
    await waitFor(() => expect(screen.getByTestId("ta-session")).toHaveTextContent("Ativa"));
    const ufs = screen.getByRole("group", { name: "UFs" });
    fireEvent.click(within(ufs).getByRole("button", { name: "CE" }));
    fireEvent.click(within(ufs).getByRole("button", { name: "Todo o Brasil" }));
    // One scope at a time: the whole country replaces the picked UFs.
    expect(within(ufs).getByRole("button", { name: "CE" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "Iniciar varredura" }));

    await waitFor(() => expect(starts).toHaveLength(1));
    expect(starts[0]).toEqual({ depth: "nascente_rio", source: "tripadvisor" });
    expect(await screen.findByText("Varredura de todo o Brasil iniciada.")).toBeInTheDocument();
  });

  it("a per-UF cap goes in the body as max_atrativos_per_uf", async () => {
    renderVarreduras();
    await waitFor(() => expect(screen.getByTestId("ta-session")).toHaveTextContent("Ativa"));
    fireEvent.click(screen.getByRole("button", { name: "SE" }));
    fireEvent.change(screen.getByLabelText("Máx. atrativos por UF"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Iniciar varredura" }));

    await waitFor(() => expect(starts).toHaveLength(1));
    expect(starts[0]).toEqual({
      depth: "nascente_rio",
      source: "tripadvisor",
      ufs: ["SE"],
      max_atrativos_per_uf: 5,
    });
  });

  it("Gerar descrições needs a scope but no TA session, and sends no depth", async () => {
    server.use(taSessionStatus({ present: false, expires_in: undefined, reason: null }));
    renderVarreduras();
    await waitFor(() => expect(screen.getByTestId("ta-session")).toHaveTextContent("Expirada"));
    const describe = screen.getByRole("button", { name: "Gerar descrições" });
    expect(describe).toBeDisabled(); // no scope yet

    fireEvent.click(screen.getByRole("button", { name: "SP" }));
    fireEvent.change(screen.getByLabelText("Máx. atrativos por UF"), { target: { value: "3" } });
    // The sweep and the backfill need the session; the descriptions do not.
    expect(screen.getByRole("button", { name: "Iniciar varredura" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Retomar backfill" })).toBeDisabled();
    fireEvent.click(describe);

    await waitFor(() => expect(starts).toHaveLength(1));
    expect(starts[0]).toEqual({ action: "describe", ufs: ["SP"], max_atrativos_per_uf: 3 });
    expect(await screen.findByText("Gerando descrições de SP.")).toBeInTheDocument();
  });

  it("Retomar backfill resumes the TripAdvisor backfill run", async () => {
    renderVarreduras();
    await waitFor(() => expect(screen.getByTestId("ta-session")).toHaveTextContent("Ativa"));
    fireEvent.click(screen.getByRole("button", { name: "Retomar backfill" }));

    await waitFor(() => expect(starts).toHaveLength(1));
    expect(starts[0]).toEqual({ action: "backfill" });
    expect(await screen.findByText("Backfill retomado.")).toBeInTheDocument();
  });

  it("puts the focus on the start panel when opened from a cold Ligar", async () => {
    renderWithClient(<PainelVarreduras focusStart={1} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Todo o Brasil" })).toHaveFocus(),
    );
  });

  it("shows the session TTL and warns when it is about to expire", async () => {
    server.use(taSessionStatus({ expires_in: 240 }));
    renderVarreduras();

    const card = screen.getByTestId("ta-session");
    await waitFor(() => expect(card).toHaveTextContent("Expirando"));
    expect(card).toHaveTextContent("4:00");
  });

  it("counts the TTL down locally between polls, like the status bar", async () => {
    server.use(taSessionStatus({ expires_in: 240 }));
    // The 1 s ticker starts with the card: fake it (and the clock) from the start.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    try {
      renderVarreduras();
      const card = screen.getByTestId("ta-session");
      await waitFor(() => expect(card).toHaveTextContent("4:00"));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_000);
      });
      expect(card).toHaveTextContent("3:57");
    } finally {
      vi.useRealTimers();
    }
  });

  it("opens the cURL injection dialog from the session card", async () => {
    renderVarreduras();

    fireEvent.click(screen.getByRole("button", { name: "Atualizar sessão (cURL)" }));

    expect(await screen.findByRole("dialog", { name: "Origem dos dados" })).toBeInTheDocument();
  });
});
