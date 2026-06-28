import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PainelBoard } from "@/components/painel/PainelBoard";
import type { PainelCard } from "@/lib/painel-data";

function makeCard(overrides: Partial<PainelCard> = {}): PainelCard {
  return {
    id: crypto.randomUUID(),
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

const COLUMN_KEYS = [
  "nascente",
  "rio",
  "whatsapp",
  "mar",
  "dlq",
  "falha",
] as const;

function renderBoard(props: Partial<React.ComponentProps<typeof PainelBoard>> = {}) {
  return render(
    <PainelBoard
      cards={[]}
      onDropToColumn={noop}
      onCardDragStart={noop}
      onCardRetry={noop}
      {...props}
    />,
  );
}

describe("PainelBoard", () => {
  it("renders all 6 columns and per-column counts from buildColumns", () => {
    renderBoard({
      cards: [
        makeCard({ column: "mar", routing: "mar" }),
        makeCard({ column: "dlq", routing: "dlq" }),
        makeCard({ column: "dlq", routing: "dlq" }),
      ],
    });

    for (const key of COLUMN_KEYS) {
      expect(screen.getByTestId(`painel-col-${key}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId("painel-col-count-mar")).toHaveTextContent("1");
    expect(screen.getByTestId("painel-col-count-dlq")).toHaveTextContent("2");
    expect(screen.getByTestId("painel-col-count-falha")).toHaveTextContent("0");
  });

  it("uses the nascenteCount prop for the Nascente column count", () => {
    renderBoard({
      cards: [makeCard({ column: "mar", routing: "mar" })],
      nascenteCount: 9,
    });
    expect(screen.getByTestId("painel-col-count-nascente")).toHaveTextContent("9");
  });

  it("renders a RecordCard per card", () => {
    renderBoard({
      cards: [
        makeCard({ column: "mar", routing: "mar" }),
        makeCard({ column: "dlq", routing: "dlq" }),
      ],
    });
    expect(screen.getAllByTestId("record-card")).toHaveLength(2);
  });

  it("calls onDropToColumn(key) when a card is dropped on a column body", () => {
    const onDropToColumn = vi.fn();
    renderBoard({ onDropToColumn });

    fireEvent.drop(screen.getByTestId("painel-col-falha"));
    expect(onDropToColumn).toHaveBeenCalledWith("falha");
  });

  it("renders 6 columns with an empty cards array without crashing", () => {
    renderBoard({ cards: [] });
    for (const key of COLUMN_KEYS) {
      expect(screen.getByTestId(`painel-col-${key}`)).toBeInTheDocument();
    }
    expect(screen.queryAllByTestId("record-card")).toHaveLength(0);
  });
});
