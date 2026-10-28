import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  atrativoDetailSuccess,
  atrativosByRouting,
  atrativosCountByRouting,
  failureCardsSuccess,
  sampleAtrativoDetail,
} from "@/mocks/handlers/atrativos";
import { configGetSuccess } from "@/mocks/handlers/config";
import { costSuccess } from "@/mocks/handlers/cost";
import { dedupPairsEmpty } from "@/mocks/handlers/dedup";
import { engineStatus, taSessionStatus } from "@/mocks/handlers/engine";
import { runsListSuccess } from "@/mocks/handlers/runs";
import { server } from "@/mocks/server";

import { renderWithClient } from "../../../components/cms/__tests__/test-utils";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

import PainelPage from "../page";
import type { AtrativoListItem } from "@/lib/atrativos-api";

const row = (id: string, routing: string, name: string, uf = "BA"): AtrativoListItem => ({
  ...sampleAtrativoDetail,
  id,
  routing,
  name,
  uf,
});
const ROWS = [
  row("dlq-1", "dlq", "Mercado Modelo"),
  row("dlq-2", "dlq", "Elevador Lacerda"),
  row("dlq-3", "dlq", "Museu do Frevo", "PE"),
  row("mar-1", "mar", "Farol da Barra"),
];

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/painel");
  server.use(
    engineStatus(),
    taSessionStatus(),
    atrativosCountByRouting({ dlq: 3, mar: 7 }),
    failureCardsSuccess(),
    dedupPairsEmpty(),
    costSuccess(),
    configGetSuccess(),
  );
});

describe("/painel page", () => {
  it("opens on Visão geral and routes the Revisão/Mar/Falha links into Registros tabs", async () => {
    const user = userEvent.setup();
    renderWithClient(<PainelPage />);

    expect(await screen.findByTestId("painel-visao-geral")).toBeInTheDocument();
    expect(screen.getByTestId("painel-topbar")).toHaveTextContent("Visão geral");

    await user.click(screen.getByTestId("painel-nav-item-registros-mar"));
    expect(screen.getByTestId("painel-registros")).toHaveAttribute("data-tab", "mar");
    expect(screen.getByTestId("painel-topbar")).toHaveTextContent("Registros");

    await user.click(screen.getByTestId("painel-nav-item-registros-falha"));
    expect(screen.getByTestId("painel-registros")).toHaveAttribute("data-tab", "falha");
    expect(screen.getByTestId("painel-nav-item-registros-falha")).toHaveAttribute(
      "aria-current",
      "page",
    );

    // The old kanban is not reachable.
    expect(screen.queryByTestId("painel-view")).not.toBeInTheDocument();
  });
  it("Visão geral tiles open the Registros tab and attention actions open their view", async () => {
    server.use(
      engineStatus({
        mode: "PAUSADO",
        pause_reason: { reason: "provider_balance", provider: "google_places", action: "sweep", at: "2026-10-26T18:00:00Z" },
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelPage />);

    await user.click(await screen.findByTestId("tile-mar"));
    expect(screen.getByTestId("painel-registros")).toHaveAttribute("data-tab", "mar");

    await user.click(screen.getByTestId("painel-nav-item-visao"));
    const pause = await screen.findByTestId("attention-pause");
    await user.click(within(pause).getByRole("button"));
    expect(screen.getByTestId("painel-topbar")).toHaveTextContent("Custo & LLM");
  });
  it("restores view, tab, UF filter and the open record from the URL", async () => {
    server.use(
      atrativosByRouting(ROWS),
      atrativoDetailSuccess({ ...sampleAtrativoDetail, ...ROWS[3] }),
    );
    window.history.replaceState(null, "", "/painel?view=registros&tab=mar&uf=BA&id=mar-1");
    renderWithClient(<PainelPage />);

    expect(await screen.findByTestId("painel-registros")).toHaveAttribute("data-tab", "mar");
    expect(screen.getByLabelText("UF")).toHaveValue("BA");
    const aside = within(screen.getByRole("complementary", { name: "Detalhe do registro" }));
    expect(await aside.findByRole("heading", { name: "Farol da Barra" })).toBeInTheDocument();
  });

  it("ignores unknown URL values and opens on Visão geral", async () => {
    window.history.replaceState(null, "", "/painel?view=toString&tab=x&uf=ZZ");
    renderWithClient(<PainelPage />);
    expect(await screen.findByTestId("painel-visao-geral")).toBeInTheDocument();
  });

  it("writes the place to the URL: screens and tabs push, row moves and the UF filter replace", async () => {
    server.use(atrativosByRouting(ROWS), atrativoDetailSuccess());
    const user = userEvent.setup();
    renderWithClient(<PainelPage />);
    await screen.findByTestId("painel-visao-geral");
    const before = window.history.length;

    await user.click(screen.getByTestId("painel-nav-item-registros-revisao"));
    expect(window.location.search).toBe("?view=registros&tab=revisao");
    await screen.findByText("Mercado Modelo");

    fireEvent.keyDown(document.body, { key: "j" });
    fireEvent.keyDown(document.body, { key: "j" });
    expect(window.location.search).toBe("?view=registros&tab=revisao&id=dlq-2");

    fireEvent.change(screen.getByLabelText("UF"), { target: { value: "BA" } });
    expect(window.location.search).toBe("?view=registros&tab=revisao&uf=BA");

    await user.click(screen.getByTestId("painel-nav-item-registros-mar"));
    expect(window.location.search).toBe("?view=registros&tab=mar&uf=BA");
    // Two entries: Registros and the Mar tab. J/K and the UF filter added none.
    expect(window.history.length).toBe(before + 2);

    act(() => window.history.back());
    await waitFor(() =>
      expect(screen.getByTestId("painel-registros")).toHaveAttribute("data-tab", "revisao"),
    );
    expect(screen.getByLabelText("UF")).toHaveValue("BA");

    act(() => window.history.back());
    expect(await screen.findByTestId("painel-visao-geral")).toBeInTheDocument();
  });

  it("a cold Ligar opens Varreduras with the focus on the start panel", async () => {
    server.use(
      engineStatus({ mode: "DESLIGADO", editing_unlocked: true, state: "idle", enabled: false }),
      runsListSuccess(),
    );
    const user = userEvent.setup();
    renderWithClient(<PainelPage />);
    await screen.findByTestId("painel-visao-geral");
    await waitFor(() =>
      expect(screen.getByTestId("painel-motor-state")).toHaveTextContent("Desligado"),
    );

    await user.click(screen.getByTestId("painel-motor-btn"));
    await user.click(await screen.findByTestId("painel-motor-ligar"));

    expect(screen.getByTestId("painel-topbar")).toHaveTextContent("Varreduras");
    expect(window.location.search).toBe("?view=varreduras");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Todo o Brasil" })).toHaveFocus(),
    );
  });
});
