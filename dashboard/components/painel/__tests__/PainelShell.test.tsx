import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PainelShell } from "@/components/painel/PainelShell";
import { PainelView } from "@/components/painel/PainelView";
import { atrativosListSuccess } from "@/mocks/handlers/atrativos";
import { destinosListSuccess } from "@/mocks/handlers/destinos";
import { dedupPairsEmpty } from "@/mocks/handlers/dedup";
import { engineStatus } from "@/mocks/handlers/engine";
import { server } from "@/mocks/server";

// PainelView (17-05) now loads real board data + metrics, so it must mount
// inside a QueryClient with the destinos/atrativos/engine handlers registered.
function renderShell(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  // usePainelBoard (mounted via <PainelView/>) now fires a dedup-pairs query for
  // the "possível duplicado" badge — register it so no request escapes the mock.
  server.use(
    destinosListSuccess(),
    atrativosListSuccess(),
    engineStatus(),
    dedupPairsEmpty(),
  );
});

describe("PainelShell", () => {
  it("renders all 5 nav items, both group headers and the operator footer", () => {
    renderShell(
      <PainelShell
        active="painel"
        onSelect={() => {}}
        topbar={<div data-testid="stub-topbar">topbar</div>}
      >
        <PainelView />
      </PainelShell>,
    );

    // 5 nav labels (pt-BR, from the design contract; Conversas WhatsApp retired)
    expect(screen.getByText("Painel de Processamento")).toBeInTheDocument();
    expect(screen.getByText("Duplicados")).toBeInTheDocument();
    expect(screen.getByText("Mapeamento")).toBeInTheDocument();
    expect(screen.getByText("Varreduras")).toBeInTheDocument();
    expect(screen.getByText("Custo & LLM")).toBeInTheDocument();

    // group headers
    expect(screen.getByText("Processamento")).toBeInTheDocument();
    expect(screen.getByText("Operação")).toBeInTheDocument();

    // operator footer
    expect(screen.getByText("Operador Brave")).toBeInTheDocument();
    expect(screen.getByText("CMS Territorial")).toBeInTheDocument();

    // topbar + content slots render
    expect(screen.getByTestId("stub-topbar")).toBeInTheDocument();
    expect(screen.getByTestId("painel-view")).toBeInTheDocument();
  });

  it("marks the active nav item with aria-current/data-active", () => {
    renderShell(
      <PainelShell active="painel" onSelect={() => {}} topbar={null}>
        <PainelView />
      </PainelShell>,
    );

    const activeBtn = screen.getByText("Painel de Processamento").closest("button");
    expect(activeBtn).toHaveAttribute("aria-current", "page");
    expect(activeBtn).toHaveAttribute("data-active", "true");

    const inactiveBtn = screen.getByText("Duplicados").closest("button");
    expect(inactiveBtn).not.toHaveAttribute("aria-current");
  });

  it("fires onSelect with the clicked view key", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    renderShell(
      <PainelShell active="painel" onSelect={onSelect} topbar={null}>
        <PainelView />
      </PainelShell>,
    );

    await user.click(screen.getByText("Duplicados"));
    expect(onSelect).toHaveBeenCalledWith("duplicados");
  });
});
