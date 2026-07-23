import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelBoard } from "@/components/painel/PainelBoard";
import type { PainelCard } from "@/lib/painel-data";

// jsdom has no IntersectionObserver — install a controllable mock that records
// each instance so a test can fire the "sentinel scrolled into view" callback.
type IOCallback = (entries: { isIntersecting: boolean }[]) => void;
const observers: { cb: IOCallback; target: Element | null }[] = [];

class MockIntersectionObserver {
  cb: IOCallback;
  target: Element | null = null;
  constructor(cb: IOCallback) {
    this.cb = cb;
    observers.push(this);
  }
  observe(el: Element) {
    this.target = el;
  }
  disconnect() {
    const i = observers.indexOf(this);
    if (i >= 0) observers.splice(i, 1);
  }
  unobserve() {}
  takeRecords() {
    return [];
  }
}

/** Fire the intersect callback on every live observer (simulate scroll-to-bottom). */
function triggerAllIntersections() {
  act(() => {
    for (const o of [...observers]) o.cb([{ isIntersecting: true }]);
  });
}

beforeEach(() => {
  observers.length = 0;
  vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

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

// The WhatsApp column is hidden (removed from COLUMN_DEFS) — 4 columns render.
const COLUMN_KEYS = [
  "nascente",
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
  it("renders all 4 columns and per-column counts from buildColumns", () => {
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

  it("renders 4 columns with an empty cards array without crashing", () => {
    renderBoard({ cards: [] });
    for (const key of COLUMN_KEYS) {
      expect(screen.getByTestId(`painel-col-${key}`)).toBeInTheDocument();
    }
    expect(screen.queryAllByTestId("record-card")).toHaveLength(0);
  });

  describe("per-column render windowing", () => {
    const bigColumn = (n: number): PainelCard[] =>
      Array.from({ length: n }, () =>
        makeCard({ column: "mar", routing: "mar" }),
      );

    it("renders only 100 cards initially for a >100-card column + a sentinel", () => {
      renderBoard({ cards: bigColumn(130) });
      expect(screen.getAllByTestId("record-card")).toHaveLength(100);
      // count pill shows the TRUE total, not the rendered window
      expect(screen.getByTestId("painel-col-count-mar")).toHaveTextContent("130");
      expect(
        screen.getByTestId("painel-col-sentinel-mar"),
      ).toBeInTheDocument();
    });

    it("reveals +50 more when the sentinel intersects (scroll-to-bottom)", () => {
      renderBoard({ cards: bigColumn(130) });
      expect(screen.getAllByTestId("record-card")).toHaveLength(100);

      triggerAllIntersections();
      // 100 + 50 capped at 130 → 130
      expect(screen.getAllByTestId("record-card")).toHaveLength(130);
      // once every card is shown, the sentinel is gone
      expect(
        screen.queryByTestId("painel-col-sentinel-mar"),
      ).not.toBeInTheDocument();
    });

    it("grows in 50-card steps across successive intersects", () => {
      renderBoard({ cards: bigColumn(230) });
      expect(screen.getAllByTestId("record-card")).toHaveLength(100);

      triggerAllIntersections(); // 150
      expect(screen.getAllByTestId("record-card")).toHaveLength(150);

      triggerAllIntersections(); // 200
      expect(screen.getAllByTestId("record-card")).toHaveLength(200);

      triggerAllIntersections(); // 230 (capped)
      expect(screen.getAllByTestId("record-card")).toHaveLength(230);
    });

    it("renders no sentinel for a column at/under the 100 cap", () => {
      renderBoard({ cards: bigColumn(100) });
      expect(screen.getAllByTestId("record-card")).toHaveLength(100);
      expect(
        screen.queryByTestId("painel-col-sentinel-mar"),
      ).not.toBeInTheDocument();
    });
  });
});
