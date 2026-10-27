import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { PainelShell } from "@/components/painel/PainelShell";
import {
  NAV_GROUPS,
  type PainelViewKey,
  type RegistrosTab,
} from "@/components/painel/nav";
import {
  atrativosCountByRouting,
  failureCardsSuccess,
  sampleFailureCards,
} from "@/mocks/handlers/atrativos";
import { dedupPairsSuccess, sampleDedupPairs } from "@/mocks/handlers/dedup";
import { server } from "@/mocks/server";

import { renderWithClient } from "../../cms/__tests__/test-utils";

const ALL_ITEMS = NAV_GROUPS.flatMap((g) => g.items);

// Stateful harness: the page owns view + tab state; the shell only reports clicks.
function Harness() {
  const [nav, setNav] = useState<{ view: PainelViewKey; tab?: RegistrosTab }>({
    view: "visao",
  });
  return (
    <PainelShell
      active={nav.view}
      registrosTab={nav.tab}
      onSelect={(view, tab) => setNav({ view, tab })}
      topbar={<div data-testid="stub-topbar">topbar</div>}
    >
      <div data-testid="stub-body">body</div>
    </PainelShell>
  );
}

beforeEach(() => {
  window.localStorage.clear();
  server.use(
    atrativosCountByRouting({ dlq: 2859, mar: 412 }),
    failureCardsSuccess(),
    dedupPairsSuccess(),
  );
});

describe("PainelShell", () => {
  it("renders the Norteia lockup, the grouped nav and the topbar/content slots", () => {
    renderWithClient(<Harness />);

    expect(screen.getByRole("img", { name: "Norteia" })).toHaveAttribute(
      "data-testid",
      "painel-brand-logo",
    );
    for (const { group } of NAV_GROUPS) {
      expect(screen.getByRole("navigation", { name: group })).toBeInTheDocument();
    }
    expect(screen.getByTestId("stub-topbar")).toBeInTheDocument();
    expect(screen.getByTestId("stub-body")).toBeInTheDocument();
    // The kanban is gone from the navigation.
    expect(screen.queryByText("Painel de Processamento")).not.toBeInTheDocument();
  });

  it("reaches every view from the nav and highlights only the active item", async () => {
    const user = userEvent.setup();
    renderWithClient(<Harness />);

    // Default view: Visão geral.
    expect(screen.getByRole("button", { name: /Visão geral/ })).toHaveAttribute(
      "aria-current",
      "page",
    );

    for (const item of ALL_ITEMS) {
      const link = screen.getByTestId(`painel-nav-item-${item.id}`);
      await user.click(link);
      expect(link).toHaveAttribute("aria-current", "page");
      const current = ALL_ITEMS.filter(
        (it) =>
          screen.getByTestId(`painel-nav-item-${it.id}`).getAttribute("aria-current") ===
          "page",
      );
      expect(current.map((it) => it.id)).toEqual([item.id]);
    }
  });

  it("shows the work-waiting counts from the API envelopes", async () => {
    renderWithClient(<Harness />);

    expect(
      await screen.findByTestId("painel-nav-count-registros-revisao"),
    ).toHaveTextContent("2.859");
    expect(await screen.findByTestId("painel-nav-count-registros-mar")).toHaveTextContent(
      "412",
    );
    expect(
      await screen.findByTestId("painel-nav-count-registros-falha"),
    ).toHaveTextContent(String(sampleFailureCards.length));
    expect(await screen.findByTestId("painel-nav-count-duplicados")).toHaveTextContent(
      String(sampleDedupPairs.length),
    );
  });

  it("collapses to the symbol + icons, keeps accessible names, and remembers it across a remount", async () => {
    const user = userEvent.setup();
    const first = renderWithClient(<Harness />);

    await user.click(screen.getByRole("button", { name: "Recolher menu" }));

    expect(screen.queryByTestId("painel-brand-logo")).not.toBeInTheDocument();
    expect(screen.getByTestId("painel-brand-symbol")).toBeInTheDocument();
    const mar = screen.getByTestId("painel-nav-item-registros-mar");
    expect(mar).toHaveAccessibleName("Mar");
    expect(mar).toHaveAttribute("title", expect.stringContaining("Mar"));
    // Visible label text is gone; only the icon remains.
    expect(within(mar).queryByText("Mar")).not.toBeInTheDocument();

    first.unmount();
    renderWithClient(<Harness />);

    expect(screen.getByTestId("painel-brand-symbol")).toBeInTheDocument();
    expect(screen.getByTestId("painel-nav-item-config")).toHaveAccessibleName(
      "Configuração",
    );
    await user.click(screen.getByRole("button", { name: "Expandir menu" }));
    expect(screen.getByTestId("painel-brand-logo")).toBeInTheDocument();
    expect(screen.getByTestId("painel-nav-item-config")).toHaveTextContent("Configuração");
  });

  it("toggles the dark theme on the .painel-light wrapper and remembers it", async () => {
    const user = userEvent.setup();
    const first = renderWithClient(<Harness />);

    const shell = screen.getByTestId("painel-shell");
    expect(shell).toHaveClass("painel-light");
    expect(shell).toHaveAttribute("data-painel-theme", "light");

    await user.click(screen.getByRole("button", { name: "Tema escuro" }));
    expect(shell).toHaveAttribute("data-painel-theme", "dark");
    expect(screen.getByRole("button", { name: "Tema escuro" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    first.unmount();
    renderWithClient(<Harness />);
    expect(screen.getByTestId("painel-shell")).toHaveAttribute("data-painel-theme", "dark");
  });
});
