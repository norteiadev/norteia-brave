import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PainelShell } from "@/components/painel/PainelShell";
import { PainelView } from "@/components/painel/PainelView";

describe("PainelShell", () => {
  it("renders all 6 nav items, both group headers and the operator footer", () => {
    render(
      <PainelShell
        active="painel"
        onSelect={() => {}}
        topbar={<div data-testid="stub-topbar">topbar</div>}
      >
        <PainelView />
      </PainelShell>,
    );

    // 6 nav labels (pt-BR, from the design contract)
    expect(screen.getByText("Painel (Kanban)")).toBeInTheDocument();
    expect(screen.getByText("Duplicados")).toBeInTheDocument();
    expect(screen.getByText("Mapeamento")).toBeInTheDocument();
    expect(screen.getByText("Varreduras")).toBeInTheDocument();
    expect(screen.getByText("Conversas WhatsApp")).toBeInTheDocument();
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
    render(
      <PainelShell active="painel" onSelect={() => {}} topbar={null}>
        <PainelView />
      </PainelShell>,
    );

    const activeBtn = screen.getByText("Painel (Kanban)").closest("button");
    expect(activeBtn).toHaveAttribute("aria-current", "page");
    expect(activeBtn).toHaveAttribute("data-active", "true");

    const inactiveBtn = screen.getByText("Duplicados").closest("button");
    expect(inactiveBtn).not.toHaveAttribute("aria-current");
  });

  it("fires onSelect with the clicked view key", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <PainelShell active="painel" onSelect={onSelect} topbar={null}>
        <PainelView />
      </PainelShell>,
    );

    await user.click(screen.getByText("Duplicados"));
    expect(onSelect).toHaveBeenCalledWith("duplicados");
  });
});
