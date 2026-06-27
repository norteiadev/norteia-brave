import { describe, expect, it, vi } from "vitest";

import type { PainelCard, PainelColumnKey } from "@/lib/painel-data";

// Mock the four API modules so runAction dispatch can be asserted without the
// network. mapDrop/mapRetry are pure and need no mocks, but runAction imports
// these fns at module load, so the mocks must be in place before the import.
vi.mock("@/lib/destinos-api", () => ({
  promoteDestino: vi.fn(() => Promise.resolve({ status: "ok" })),
  descarteDestino: vi.fn(() => Promise.resolve({ status: "ok" })),
  reprocessDestino: vi.fn(() => Promise.resolve({ status: "ok" })),
}));
vi.mock("@/lib/atrativos-api", () => ({
  descartarAtrativo: vi.fn(() => Promise.resolve({ status: "ok" })),
}));
vi.mock("@/lib/mar-ready-api", () => ({
  promoteAtrativo: vi.fn(() => Promise.resolve({ status: "ok" })),
}));

import { descartarAtrativo } from "@/lib/atrativos-api";
import {
  descarteDestino,
  promoteDestino,
  reprocessDestino,
} from "@/lib/destinos-api";
import { promoteAtrativo } from "@/lib/mar-ready-api";
import { mapDrop, mapRetry, runAction } from "@/lib/painel-actions";

function makeCard(overrides: Partial<PainelCard> = {}): PainelCard {
  return {
    id: "card-1",
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

const ALL_COLUMNS: PainelColumnKey[] = [
  "nascente",
  "in_progress",
  "mar",
  "dlq",
  "descarte",
];

describe("mapDrop — closed allow-list (no invented transitions)", () => {
  it("destino → mar (Sincronizado) = promote/destino", () => {
    const card = makeCard({ type: "destino", column: "dlq" });
    expect(mapDrop(card, "mar")).toEqual({
      kind: "promote",
      entity: "destino",
      id: card.id,
    });
  });

  it("atrativo → mar (Sincronizado) = promote/atrativo (mar-ready)", () => {
    const card = makeCard({ type: "atrativo", column: "in_progress" });
    expect(mapDrop(card, "mar")).toEqual({
      kind: "promote",
      entity: "atrativo",
      id: card.id,
    });
  });

  it("destino → descarte = descarte/destino", () => {
    const card = makeCard({ type: "destino", column: "dlq" });
    expect(mapDrop(card, "descarte")).toEqual({
      kind: "descarte",
      entity: "destino",
      id: card.id,
    });
  });

  it("atrativo → descarte = descarte/atrativo", () => {
    const card = makeCard({ type: "atrativo", column: "in_progress" });
    expect(mapDrop(card, "descarte")).toEqual({
      kind: "descarte",
      entity: "atrativo",
      id: card.id,
    });
  });

  it("destino → dlq (Revisão) = reprocess/destino", () => {
    const card = makeCard({ type: "destino", column: "mar" });
    expect(mapDrop(card, "dlq")).toEqual({
      kind: "reprocess",
      entity: "destino",
      id: card.id,
    });
  });

  it("atrativo → dlq (Revisão) = null (atrativos have no reprocess)", () => {
    const card = makeCard({ type: "atrativo", column: "in_progress" });
    expect(mapDrop(card, "dlq")).toBeNull();
  });

  it("any card → nascente = null", () => {
    expect(mapDrop(makeCard({ type: "destino", column: "mar" }), "nascente")).toBeNull();
    expect(
      mapDrop(makeCard({ type: "atrativo", column: "in_progress" }), "nascente"),
    ).toBeNull();
  });

  it("any card → in_progress = null", () => {
    expect(mapDrop(makeCard({ type: "destino", column: "mar" }), "in_progress")).toBeNull();
    expect(
      mapDrop(makeCard({ type: "atrativo", column: "dlq" }), "in_progress"),
    ).toBeNull();
  });

  it("drop on the same column = null (no-op, never a mutation)", () => {
    for (const col of ALL_COLUMNS) {
      expect(mapDrop(makeCard({ type: "destino", column: col }), col)).toBeNull();
      expect(mapDrop(makeCard({ type: "atrativo", column: col }), col)).toBeNull();
    }
  });
});

describe("mapRetry — falha-card retry", () => {
  it("destino → reprocess/destino", () => {
    const card = makeCard({ type: "destino", column: "descarte" });
    expect(mapRetry(card)).toEqual({
      kind: "reprocess",
      entity: "destino",
      id: card.id,
    });
  });

  it("atrativo → null (no atrativo reprocess)", () => {
    const card = makeCard({ type: "atrativo", column: "descarte" });
    expect(mapRetry(card)).toBeNull();
  });
});

describe("runAction — dispatch by entity + kind", () => {
  it("promote/destino → promoteDestino", async () => {
    await runAction({ kind: "promote", entity: "destino", id: "d1" });
    expect(promoteDestino).toHaveBeenCalledWith("d1");
  });

  it("promote/atrativo → promoteAtrativo (mar-ready)", async () => {
    await runAction({ kind: "promote", entity: "atrativo", id: "a1" });
    expect(promoteAtrativo).toHaveBeenCalledWith("a1");
  });

  it("descarte/destino → descarteDestino", async () => {
    await runAction({ kind: "descarte", entity: "destino", id: "d2" });
    expect(descarteDestino).toHaveBeenCalledWith("d2");
  });

  it("descarte/atrativo → descartarAtrativo", async () => {
    await runAction({ kind: "descarte", entity: "atrativo", id: "a2" });
    expect(descartarAtrativo).toHaveBeenCalledWith("a2");
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
