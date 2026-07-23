import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RecordCard } from "@/components/painel/RecordCard";
import type { PainelCard } from "@/lib/painel-data";

function makeCard(overrides: Partial<PainelCard> = {}): PainelCard {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    type: "destino",
    name: "Pelourinho",
    uf: "BA",
    municipality: "Salvador",
    routing: "mar",
    column: "mar",
    score: 91,
    source: null,
    duplicate: false,
    error: null,
    ...overrides,
  };
}

const noop = () => {};

describe("RecordCard", () => {
  it("renders a destino card with the StageBadge score band (91.0)", () => {
    render(
      <RecordCard card={makeCard()} onDragStart={noop} onRetry={noop} />,
    );
    expect(screen.getByText("Destino")).toBeInTheDocument();
    // StageBadge formats the band with toFixed(1)
    expect(screen.getByText("91.0")).toBeInTheDocument();
    expect(screen.getByText("Pelourinho")).toBeInTheDocument();
    expect(screen.getByText("BA")).toBeInTheDocument();
    expect(screen.getByText("Salvador")).toBeInTheDocument();
  });

  it("renders the município next to the UF chip when municipality is set", () => {
    render(
      <RecordCard
        card={makeCard({ uf: "ES", municipality: "Vila Velha" })}
        onDragStart={noop}
        onRetry={noop}
      />,
    );
    expect(screen.getByText("ES")).toBeInTheDocument();
    expect(screen.getByText("Vila Velha")).toBeInTheDocument();
  });

  it("does NOT render a município (UF-only) when municipality is null", () => {
    render(
      <RecordCard
        card={makeCard({ uf: "ES", municipality: null })}
        onDragStart={noop}
        onRetry={noop}
      />,
    );
    // UF chip still shows; no município text leaks in
    expect(screen.getByText("ES")).toBeInTheDocument();
    expect(screen.queryByText("Salvador")).not.toBeInTheDocument();
    expect(screen.queryByText("Vila Velha")).not.toBeInTheDocument();
  });

  it("renders the atrativo chip from card.type", () => {
    render(
      <RecordCard
        card={makeCard({ type: "atrativo" })}
        onDragStart={noop}
        onRetry={noop}
      />,
    );
    expect(screen.getByText("Atrativo")).toBeInTheDocument();
  });

  it("renders the 'Possível duplicado' flag when duplicate is true", () => {
    render(
      <RecordCard
        card={makeCard({ duplicate: true })}
        onDragStart={noop}
        onRetry={noop}
      />,
    );
    expect(screen.getByText("Possível duplicado")).toBeInTheDocument();
  });

  it("does NOT render a '—' placeholder when source is null (L-2)", () => {
    render(
      <RecordCard
        card={makeCard({ source: null })}
        onDragStart={noop}
        onRetry={noop}
      />,
    );
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("renders the source label when source is set", () => {
    render(
      <RecordCard
        card={makeCard({ source: "places_discovery" })}
        onDragStart={noop}
        onRetry={noop}
      />,
    );
    expect(screen.getByText("places_discovery")).toBeInTheDocument();
  });

  it("renders ⚠ falha + a working ↺ Reprocessar on a falha card", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const card = makeCard({
      column: "falha",
      routing: "falha",
      error: "Falha na geocodificação",
    });
    render(<RecordCard card={card} onDragStart={noop} onRetry={onRetry} />);

    expect(screen.getByText(/Falha na geocodificação/)).toBeInTheDocument();
    await user.click(screen.getByTestId("record-card-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onRetry).toHaveBeenCalledWith(card);
  });

  it("falls back to a generic falha label when error is null", () => {
    render(
      <RecordCard
        card={makeCard({ column: "falha", routing: "falha", error: null })}
        onDragStart={noop}
        onRetry={noop}
      />,
    );
    expect(screen.getByText(/Falha no processamento/)).toBeInTheDocument();
  });

  it("is draggable and forwards onDragStart(card)", () => {
    const onDragStart = vi.fn();
    const card = makeCard();
    render(<RecordCard card={card} onDragStart={onDragStart} onRetry={noop} />);

    const root = screen.getByTestId("record-card");
    expect(root).toHaveAttribute("draggable", "true");
    expect(root).toHaveAttribute("data-id", card.id);

    root.dispatchEvent(new Event("dragstart", { bubbles: true }));
    expect(onDragStart).toHaveBeenCalledWith(card);
  });

  // --- Phase H: edit-lock ---

  it("is NOT draggable when editingUnlocked is false (edit-lock)", () => {
    const onDragStart = vi.fn();
    render(
      <RecordCard
        card={makeCard()}
        onDragStart={onDragStart}
        onRetry={noop}
        editingUnlocked={false}
      />,
    );
    const root = screen.getByTestId("record-card");
    expect(root).toHaveAttribute("draggable", "false");
    // The drag handler is detached, so a native dragstart fires nothing.
    root.dispatchEvent(new Event("dragstart", { bubbles: true }));
    expect(onDragStart).not.toHaveBeenCalled();
  });
});
