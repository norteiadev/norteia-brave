import { describe, expect, it, vi } from "vitest";

import type { PainelCard, PainelColumnKey } from "@/lib/painel-data";

// Mock the API modules so runAction dispatch can be asserted without the
// network. mapDrop/mapRetry are pure and need no mocks, but runAction imports
// these fns at module load, so the mocks must be in place before the import.
vi.mock("@/lib/destinos-api", () => ({
  reprocessDestino: vi.fn(() => Promise.resolve({ status: "ok" })),
}));
vi.mock("@/lib/engine-api", () => ({
  transition: vi.fn(() => Promise.resolve({ status: "ok" })),
}));

import { reprocessDestino } from "@/lib/destinos-api";
import { transition } from "@/lib/engine-api";
import { mapDrop, mapRetry, runAction } from "@/lib/painel-actions";

function makeCard(overrides: Partial<PainelCard> = {}): PainelCard {
  return {
    id: "card-1",
    type: "destino",
    name: "Pelourinho",
    uf: "BA",
    municipality: "Salvador",
    routing: "in_progress",
    column: "rio",
    score: 91,
    source: null,
    duplicate: false,
    error: null,
    ...overrides,
  };
}

// The 6 RENDERED board columns — the only drag SOURCES/TARGETS a board drop can
// ever produce. (descarte is a non-rendered key reachable only via the drawer.)
const BOARD_COLUMNS: PainelColumnKey[] = [
  "nascente",
  "rio",
  "whatsapp",
  "mar",
  "dlq",
  "falha",
];

// The client allow-list — the EXACT twin of the server _ALLOWED_EDGES
// (brave/api/routers/cms.py) and _ATRATIVO_ALLOWED_EDGES (atrativos.py),
// restricted to board-reachable (expected, to) pairs. `${expected}>${to}`.
const DESTINO_ALLOWED = new Set([
  "rio>mar",
  "rio>descarte",
  "rio>dlq",
  "dlq>rio",
  "dlq>mar",
  "dlq>descarte",
]);
const ATRATIVO_ALLOWED = new Set([
  "rio>dlq",
  "dlq>rio",
  "rio>mar",
  "rio>descarte",
]);

describe("mapDrop — full-pipeline allow-list (server twin, no invented edges)", () => {
  it("atrativo dlq → rio (reopen/reprocess) is a transition action", () => {
    const card = makeCard({ type: "atrativo", column: "dlq" });
    expect(mapDrop(card, "rio")).toEqual({
      kind: "transition",
      entity: "atrativo",
      id: card.id,
      to: "rio",
      expected: "dlq",
    });
  });

  it("atrativo rio → dlq (force send-to-review) is a transition action", () => {
    const card = makeCard({ type: "atrativo", column: "rio" });
    expect(mapDrop(card, "dlq")).toEqual({
      kind: "transition",
      entity: "atrativo",
      id: card.id,
      to: "dlq",
      expected: "rio",
    });
  });

  it("destino rio → mar (promote) is a transition action", () => {
    const card = makeCard({ type: "destino", column: "rio" });
    expect(mapDrop(card, "mar")).toEqual({
      kind: "transition",
      entity: "destino",
      id: card.id,
      to: "mar",
      expected: "rio",
    });
  });

  it("destino dlq → rio (reprocess) is a transition action", () => {
    const card = makeCard({ type: "destino", column: "dlq" });
    expect(mapDrop(card, "rio")).toEqual({
      kind: "transition",
      entity: "destino",
      id: card.id,
      to: "rio",
      expected: "dlq",
    });
  });

  it("EVERY mar → X board drop returns null (mar can never be depublished)", () => {
    for (const target of BOARD_COLUMNS) {
      expect(mapDrop(makeCard({ type: "destino", column: "mar" }), target)).toBeNull();
      expect(mapDrop(makeCard({ type: "atrativo", column: "mar" }), target)).toBeNull();
    }
  });

  it("EXHAUSTIVE: every (source, target) board pair NOT in the server allow-list returns null", () => {
    for (const source of BOARD_COLUMNS) {
      for (const target of BOARD_COLUMNS) {
        for (const type of ["destino", "atrativo"] as const) {
          const card = makeCard({ type, column: source });
          const allowed = type === "destino" ? DESTINO_ALLOWED : ATRATIVO_ALLOWED;
          const action = mapDrop(card, target);
          if (source !== target && allowed.has(`${source}>${target}`)) {
            // Allowed edge → exactly one transition action mirroring the server.
            expect(action).toEqual({
              kind: "transition",
              entity: type,
              id: card.id,
              to: target,
              expected: source,
            });
          } else {
            // Same-column, into-nascente/whatsapp/falha, mar→*, falha→* → null.
            expect(action).toBeNull();
          }
        }
      }
    }
  });

  it("drop on the same column = null (no-op, never a mutation)", () => {
    for (const col of BOARD_COLUMNS) {
      expect(mapDrop(makeCard({ type: "destino", column: col }), col)).toBeNull();
      expect(mapDrop(makeCard({ type: "atrativo", column: col }), col)).toBeNull();
    }
  });

  it("drawer-reachable descarte edges (rio/dlq → descarte) are transitions; mar → descarte is null", () => {
    expect(mapDrop(makeCard({ type: "destino", column: "rio" }), "descarte")).toEqual({
      kind: "transition",
      entity: "destino",
      id: "card-1",
      to: "descarte",
      expected: "rio",
    });
    expect(mapDrop(makeCard({ type: "atrativo", column: "rio" }), "descarte")).toEqual({
      kind: "transition",
      entity: "atrativo",
      id: "card-1",
      to: "descarte",
      expected: "rio",
    });
    // mar → descarte must stay blocked (no depublish via the discard path).
    expect(mapDrop(makeCard({ type: "destino", column: "mar" }), "descarte")).toBeNull();
  });
});

describe("mapRetry — falha-card retry", () => {
  it("destino → reprocess/destino", () => {
    const card = makeCard({ type: "destino", column: "falha" });
    expect(mapRetry(card)).toEqual({
      kind: "reprocess",
      entity: "destino",
      id: card.id,
    });
  });

  it("atrativo → null (no atrativo reprocess)", () => {
    const card = makeCard({ type: "atrativo", column: "falha" });
    expect(mapRetry(card)).toBeNull();
  });
});

describe("runAction — dispatch by entity + kind", () => {
  it("transition/destino → engine-api transition(entity, id, {to, expected})", async () => {
    await runAction({
      kind: "transition",
      entity: "destino",
      id: "d1",
      to: "mar",
      expected: "rio",
    });
    expect(transition).toHaveBeenCalledWith("destino", "d1", {
      to: "mar",
      expected: "rio",
    });
  });

  it("transition/atrativo → engine-api transition(atrativo, …)", async () => {
    await runAction({
      kind: "transition",
      entity: "atrativo",
      id: "a1",
      to: "rio",
      expected: "dlq",
    });
    expect(transition).toHaveBeenCalledWith("atrativo", "a1", {
      to: "rio",
      expected: "dlq",
    });
  });

  it("reprocess/destino → reprocessDestino", async () => {
    await runAction({ kind: "reprocess", entity: "destino", id: "d3" });
    expect(reprocessDestino).toHaveBeenCalledWith("d3");
  });

  it("reprocess/atrativo → throws (must never be constructed)", () => {
    expect(() =>
      runAction({ kind: "reprocess", entity: "atrativo", id: "a3" }),
    ).toThrow();
  });
});
