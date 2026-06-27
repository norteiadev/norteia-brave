import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { setOperatorToken } from "@/lib/api-client";
import type { AtrativoListItem } from "@/lib/atrativos-api";
import type { DestinoListItem } from "@/lib/destinos-api";
import {
  BR_UFS,
  COLUMN_DEFS,
  buildColumns,
  computeMetric,
  filterCards,
  routingToColumn,
  toPainelCards,
  usePainelBoard,
  usePainelMetrics,
  type PainelCard,
} from "@/lib/painel-data";
import {
  atrativosListSuccess,
  sampleAtrativos,
} from "@/mocks/handlers/atrativos";
import { destinosListSuccess, sampleDestinos } from "@/mocks/handlers/destinos";
import { engineStatus } from "@/mocks/handlers/engine";
import { server } from "@/mocks/server";

beforeEach(() => {
  server.resetHandlers();
});

// --- Fixtures: a mixed board (2 destinos: 1 mar 1 dlq; 2 atrativos: 1 in_progress 1 descarte) ---
const destinos: DestinoListItem[] = [
  {
    id: "d-mar",
    entity_type: "destination",
    uf: "RJ",
    routing: "mar",
    score: 91.2,
    name: "Copacabana",
    canonical_key: "rj:rio:copacabana",
    validation_pending: false,
    mar_id: "mar-1",
    published_at: "2026-06-01T10:00:00Z",
  },
  {
    id: "d-dlq",
    entity_type: "destination",
    uf: "BA",
    routing: "dlq",
    score: 72.4,
    name: "Pelourinho",
    canonical_key: "ba:salvador:pelourinho",
    validation_pending: true,
    mar_id: null,
    published_at: null,
  },
];

const atrativos: AtrativoListItem[] = [
  {
    id: "a-inprog",
    entity_type: "attraction",
    uf: "BA",
    routing: "in_progress",
    sub_state: "discovered",
    score: null,
    name: "Mercado Modelo",
    validation_pending: false,
    mar_id: null,
    parent_mar_id: "mar-1",
    contacts_summary: { phone_masked: "**1234", website: "https://x" },
  },
  {
    id: "a-descarte",
    entity_type: "attraction",
    uf: "SP",
    routing: "descarte",
    sub_state: null,
    score: 12.0,
    name: "Lugar Falso",
    validation_pending: true,
    mar_id: null,
    parent_mar_id: null,
    contacts_summary: { phone_masked: "**5678", website: null },
  },
];

// --- Pure selectors (RED-first) ---

describe("routingToColumn", () => {
  it("maps known routings to their column keys", () => {
    expect(routingToColumn("mar")).toBe("mar");
    expect(routingToColumn("descarte")).toBe("descarte");
    expect(routingToColumn("dlq")).toBe("dlq");
    expect(routingToColumn("in_progress")).toBe("in_progress");
  });

  it("falls back to 'nascente' for unknown/empty routing", () => {
    expect(routingToColumn("")).toBe("nascente");
    expect(routingToColumn("weird")).toBe("nascente");
    expect(routingToColumn("nascente")).toBe("nascente");
  });
});

describe("toPainelCards", () => {
  it("maps destinos + atrativos into a unified PainelCard[]", () => {
    const cards = toPainelCards(destinos, atrativos);
    expect(cards).toHaveLength(4);

    const mar = cards.find((c) => c.id === "d-mar")!;
    expect(mar.type).toBe("destino");
    expect(mar.column).toBe("mar");
    expect(mar.name).toBe("Copacabana");
    expect(mar.uf).toBe("RJ");
    expect(mar.score).toBe(91.2);
    // destino município derived from canonical_key last segment
    expect(mar.municipality).toBe("copacabana");

    const atr = cards.find((c) => c.id === "a-inprog")!;
    expect(atr.type).toBe("atrativo");
    expect(atr.column).toBe("in_progress");
    expect(atr.municipality).toBeNull();
  });

  it("derives `duplicate` from validation_pending for BOTH entity types", () => {
    const cards = toPainelCards(destinos, atrativos);
    expect(cards.find((c) => c.id === "d-dlq")!.duplicate).toBe(true);
    expect(cards.find((c) => c.id === "d-mar")!.duplicate).toBe(false);
    expect(cards.find((c) => c.id === "a-descarte")!.duplicate).toBe(true);
    expect(cards.find((c) => c.id === "a-inprog")!.duplicate).toBe(false);
  });

  it("sets source and error to null this slice", () => {
    const cards = toPainelCards(destinos, atrativos);
    for (const c of cards) {
      expect(c.source).toBeNull();
      expect(c.error).toBeNull();
    }
  });

  it("NEVER leaks PII — no phone field on any card (LGPD allow-list)", () => {
    const cards = toPainelCards(destinos, atrativos);
    for (const c of cards) {
      const keys = Object.keys(c);
      expect(keys).not.toContain("phone_e164");
      expect(keys).not.toContain("phone_masked");
      expect(keys).not.toContain("contacts_summary");
    }
    // Defensive: serialise the whole board and assert no phone substring escapes.
    expect(JSON.stringify(cards)).not.toContain("phone");
    expect(JSON.stringify(cards)).not.toContain("1234");
  });
});

describe("filterCards", () => {
  const cards = toPainelCards(destinos, atrativos);

  it("type 'all' keeps both entity types", () => {
    expect(filterCards(cards, { type: "all", ufs: [] })).toHaveLength(4);
  });

  it("type 'destino' / 'atrativo' filters by card.type", () => {
    expect(filterCards(cards, { type: "destino", ufs: [] })).toHaveLength(2);
    const atr = filterCards(cards, { type: "atrativo", ufs: [] });
    expect(atr).toHaveLength(2);
    expect(atr.every((c) => c.type === "atrativo")).toBe(true);
  });

  it("empty ufs keeps all UFs; non-empty keeps only cards whose uf ∈ ufs", () => {
    expect(filterCards(cards, { type: "all", ufs: [] })).toHaveLength(4);
    const ba = filterCards(cards, { type: "all", ufs: ["BA"] });
    expect(ba.every((c) => c.uf === "BA")).toBe(true);
    expect(ba).toHaveLength(2); // Pelourinho (destino) + Mercado Modelo (atrativo)
    const baRj = filterCards(cards, { type: "all", ufs: ["BA", "RJ"] });
    expect(baRj).toHaveLength(3);
  });
});

describe("buildColumns", () => {
  it("returns the 5 ordered stage columns with cards bucketed by column", () => {
    const cards = toPainelCards(destinos, atrativos);
    const cols = buildColumns(cards);
    expect(cols.map((c) => c.key)).toEqual([
      "nascente",
      "in_progress",
      "mar",
      "dlq",
      "descarte",
    ]);
    const byKey = Object.fromEntries(cols.map((c) => [c.key, c.cards.length]));
    expect(byKey.nascente).toBe(0);
    expect(byKey.in_progress).toBe(1);
    expect(byKey.mar).toBe(1);
    expect(byKey.dlq).toBe(1);
    expect(byKey.descarte).toBe(1);
  });

  it("uses COLUMN_DEFS labels in order", () => {
    expect(COLUMN_DEFS.map((c) => c.label)).toEqual([
      "Nascente",
      "Em processamento",
      "Sincronizado",
      "Revisão",
      "Descarte",
    ]);
  });
});

describe("computeMetric", () => {
  it("computes total/mar/falha and rounded pct", () => {
    expect(computeMetric(2, 1, 0)).toEqual({
      total: 2,
      mar: 1,
      falha: 0,
      pct: 50,
    });
  });

  it("guards division by zero (pct = 0 when total = 0)", () => {
    expect(computeMetric(0, 0, 0).pct).toBe(0);
  });

  it("rounds the percentage", () => {
    expect(computeMetric(3, 1, 0).pct).toBe(33); // 33.33 → 33
    expect(computeMetric(3, 2, 0).pct).toBe(67); // 66.67 → 67
  });
});

describe("BR_UFS", () => {
  it("exports the 27 BR UF codes", () => {
    expect(BR_UFS).toHaveLength(27);
    expect(BR_UFS).toContain("SP");
    expect(BR_UFS).toContain("DF");
  });
});

// --- Hooks (GREEN, over MSW) ---

function hookWrapper() {
  setOperatorToken("test-operator-token");
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return function HookWrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

describe("usePainelBoard", () => {
  it("loads destinos + atrativos lists and builds a unified card[]", async () => {
    server.use(destinosListSuccess(), atrativosListSuccess());
    const { result } = renderHook(() => usePainelBoard(), {
      wrapper: hookWrapper(),
    });

    await waitFor(() => expect(result.current.isPending).toBe(false));
    expect(result.current.isError).toBe(false);
    // sampleDestinos (2) + sampleAtrativos (2)
    expect(result.current.cards).toHaveLength(
      sampleDestinos.length + sampleAtrativos.length,
    );
    const types = result.current.cards.map((c: PainelCard) => c.type).sort();
    expect(types).toEqual(["atrativo", "atrativo", "destino", "destino"]);
  });
});

describe("usePainelMetrics", () => {
  it("derives truthful per-entity metrics from envelope totals + nascente from engine counts", async () => {
    server.use(
      destinosListSuccess(),
      atrativosListSuccess(),
      engineStatus({
        counts: {
          nascente: 9,
          rio: { in_progress: 0, mar: 0, dlq: 0, descarte: 0 },
          mar: 0,
          atrativos_by_sub_state: {},
        },
      }),
    );
    const { result } = renderHook(() => usePainelMetrics(), {
      wrapper: hookWrapper(),
    });

    await waitFor(() => expect(result.current.isPending).toBe(false));

    // sampleDestinos: 1 mar + 1 dlq → total 2, mar 1, descarte 0
    expect(result.current.destino.total).toBe(2);
    expect(result.current.destino.mar).toBe(1);
    expect(result.current.destino.falha).toBe(0);
    expect(result.current.destino.pct).toBe(50);

    // sampleAtrativos: 2 items (the list envelope total is the server count)
    expect(result.current.atrativo.total).toBe(sampleAtrativos.length);

    // Nascente COLUMN count comes from engine counts, not the lists
    expect(result.current.nascenteCount).toBe(9);
  });
});
