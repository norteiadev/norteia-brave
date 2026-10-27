import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { atrativosCountByRouting, failureCardsSuccess } from "@/mocks/handlers/atrativos";
import { configGetSuccess } from "@/mocks/handlers/config";
import { costSuccess } from "@/mocks/handlers/cost";
import { dedupPairsEmpty } from "@/mocks/handlers/dedup";
import { engineStatus, taSessionStatus } from "@/mocks/handlers/engine";
import { server } from "@/mocks/server";

import { renderWithClient } from "../../../components/cms/__tests__/test-utils";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

import PainelPage from "../page";

beforeEach(() => {
  window.localStorage.clear();
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
});
