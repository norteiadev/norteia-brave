import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PainelVisaoGeral } from "@/components/painel/PainelVisaoGeral";
import type { EngineStatus } from "@/lib/engine-api";
import { failureCardsSuccess, sampleFailureCards } from "@/mocks/handlers/atrativos";
import { costSuccess } from "@/mocks/handlers/cost";
import { engineStatus, taSessionStatus } from "@/mocks/handlers/engine";
import { server } from "@/mocks/server";

import { renderWithClient } from "../../cms/__tests__/test-utils";

const counts: EngineStatus["counts"] = {
  nascente: 5120,
  rio: { in_progress: 37, mar: 0, dlq: 0, descarte: 211 },
  mar: 412,
  atrativos_by_sub_state: {},
};

// Healthy defaults: nothing should need attention.
const calm: Partial<EngineStatus> = {
  counts,
  norteia_api: { up: true, reason: null, pending: 0, rejected: 0 },
  beat_errors: [],
  pause_reason: null,
};

function setup(status: Partial<EngineStatus> = calm, ta = {}) {
  server.use(
    engineStatus(status),
    taSessionStatus(ta),
    failureCardsSuccess(),
    costSuccess(),
  );
  const onOpenRegistros = vi.fn();
  const onOpenView = vi.fn();
  renderWithClient(
    <PainelVisaoGeral onOpenRegistros={onOpenRegistros} onOpenView={onOpenView} />,
  );
  return { onOpenRegistros, onOpenView };
}

const attention = () => screen.getByRole("region", { name: "Precisa de atenção" });

beforeEach(() => window.localStorage.clear());

describe("PainelVisaoGeral", () => {
  it("renders the pipeline tiles from engine status and the failure cards", async () => {
    setup({ ...calm, counts: { ...counts, rio: { ...counts.rio, dlq: 2859 } } });

    const pipeline = await screen.findByRole("region", { name: "Pipeline" });
    await waitFor(() => expect(within(pipeline).getByTestId("tile-nascente")).toHaveTextContent("5.120"));
    expect(within(pipeline).getByTestId("tile-rio")).toHaveTextContent("37");
    expect(within(pipeline).getByTestId("tile-revisao")).toHaveTextContent("2.859");
    expect(within(pipeline).getByTestId("tile-mar")).toHaveTextContent("412");
    expect(within(pipeline).getByTestId("tile-descarte")).toHaveTextContent("211");
    await waitFor(() =>
      expect(within(pipeline).getByTestId("tile-falha")).toHaveTextContent(
        String(sampleFailureCards.length),
      ),
    );
  });

  it("opens the matching Registros tab from the clickable tiles only", async () => {
    const user = userEvent.setup();
    const { onOpenRegistros } = setup();
    const pipeline = await screen.findByRole("region", { name: "Pipeline" });
    await waitFor(() => expect(within(pipeline).getByTestId("tile-mar")).toHaveTextContent("412"));

    await user.click(within(pipeline).getByRole("button", { name: /^Revisão/ }));
    await user.click(within(pipeline).getByRole("button", { name: /^Mar/ }));
    await user.click(within(pipeline).getByRole("button", { name: /^Falha/ }));
    expect(onOpenRegistros.mock.calls).toEqual([["revisao"], ["mar"], ["falha"]]);

    // Nascente, Rio and Descarte have no Registros tab: not buttons.
    for (const name of [/^Nascente/, /^Rio/, /^Descarte/]) {
      expect(within(pipeline).queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("shows the empty state when nothing needs attention", async () => {
    setup();
    expect(await within(attention()).findByText("Nada precisa de atenção")).toBeInTheDocument();
  });

  it("flags pushes rejected by norteia-api and opens the Mar tab", async () => {
    const user = userEvent.setup();
    const { onOpenRegistros } = setup({
      ...calm,
      norteia_api: { up: true, reason: null, pending: 0, rejected: 3 },
    });

    const item = await within(attention()).findByTestId("attention-rejected");
    expect(item).toHaveTextContent("3 pushes rejeitados pela norteia-api");
    await user.click(within(item).getByRole("button"));
    expect(onOpenRegistros).toHaveBeenCalledWith("mar");
    expect(within(attention()).queryByText("Nada precisa de atenção")).not.toBeInTheDocument();
  });

  it("flags norteia-api down with its reason, and pending pushes", async () => {
    setup({
      ...calm,
      norteia_api: { up: false, reason: "ingest:401", pending: 12, rejected: 0 },
    });

    const down = await within(attention()).findByTestId("attention-api-down");
    expect(down).toHaveTextContent("recusou o token de ingestão");
    expect(down).toHaveTextContent("ingest:401");
    expect(within(attention()).getByTestId("attention-pending")).toHaveTextContent("12");
  });

  it("flags a failed beat with its task and error type", async () => {
    const user = userEvent.setup();
    const { onOpenView } = setup({
      ...calm,
      beat_errors: [
        { task: "brave.prune_record_events", at: "2026-10-26T19:30:00Z", error_type: "TimeoutError" },
      ],
    });

    const item = await within(attention()).findByTestId("attention-beat-brave.prune_record_events");
    expect(item).toHaveTextContent("brave.prune_record_events");
    expect(item).toHaveTextContent("TimeoutError");
    await user.click(within(item).getByRole("button"));
    expect(onOpenView).toHaveBeenCalledWith("monitor");
  });

  it("flags a reasoned pause and points to the cost caps", async () => {
    const user = userEvent.setup();
    const { onOpenView } = setup({
      ...calm,
      mode: "PAUSADO",
      pause_reason: { reason: "provider_balance", provider: "google_places", action: "sweep", at: "2026-10-26T18:00:00Z" },
    });

    const item = await within(attention()).findByTestId("attention-pause");
    expect(item).toHaveTextContent("sem saldo (google_places)");
    await user.click(within(item).getByRole("button"));
    expect(onOpenView).toHaveBeenCalledWith("custo");
  });

  it("flags an expired TripAdvisor session and opens Varreduras to renew it", async () => {
    const user = userEvent.setup();
    const { onOpenView } = setup(calm, { present: false, expires_in: undefined, reason: null });

    const item = await within(attention()).findByTestId("attention-ta");
    expect(item).toHaveTextContent("Sessão TripAdvisor expirada");
    await user.click(within(item).getByRole("button"));
    expect(onOpenView).toHaveBeenCalledWith("varreduras");
  });

  it("flags records waiting in Revisão and opens that tab", async () => {
    const user = userEvent.setup();
    const { onOpenRegistros } = setup({ ...calm, counts: { ...counts, rio: { ...counts.rio, dlq: 8 } } });

    const item = await within(attention()).findByTestId("attention-revisao");
    expect(item).toHaveTextContent("8 registros esperando revisão");
    await user.click(within(item).getByRole("button"));
    expect(onOpenRegistros).toHaveBeenCalledWith("revisao");
  });

  it("renders the run card from engine status", async () => {
    setup({
      ...calm,
      state: "running",
      mode: "LIGADO",
      editing_unlocked: false,
      current_uf: "RS",
      ufs_done: 3,
      ufs_total: 27,
      depth: "nascente_rio",
      source: "tripadvisor",
    });

    const run = await screen.findByRole("region", { name: "Run" });
    await waitFor(() => expect(run).toHaveTextContent("RS"));
    expect(run).toHaveTextContent("Ligado");
    expect(run).toHaveTextContent("TripAdvisor");
    expect(run).toHaveTextContent("Nascente → Rio");
    expect(within(run).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "3");
    expect(run).toHaveTextContent("3 de 27");
  });

  it("shows the cost by provider in the operator's currency", async () => {
    setup();
    const cost = await screen.findByRole("region", { name: "Custo" });
    // LLM 24 h = sum of the lane rows (7.6577); Places today 12.50 of 300.
    await waitFor(() => expect(cost).toHaveTextContent("US$ 7,6577"));
    expect(within(cost).getByTestId("cap-places-today")).toHaveTextContent("US$ 12,50");
    expect(within(cost).getByTestId("cap-apify-today")).toHaveTextContent("US$ 0,40");
  });
});
