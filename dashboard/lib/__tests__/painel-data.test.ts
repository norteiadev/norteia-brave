import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { setOperatorToken } from "@/lib/api-client";
import type { AtrativoListItem } from "@/lib/atrativos-api";
import type { DestinoListItem } from "@/lib/destinos-api";
import type { FailureItem } from "@/lib/engine-api";
import type { NascenteListItem } from "@/lib/nascente-api";
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
import { dedupPairsEmpty } from "@/mocks/handlers/dedup";
import { nascenteList } from "@/mocks/handlers/engine";
import { failuresEmpty } from "@/mocks/handlers/workers";
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
  it("maps known routings to their column keys (in_progress → rio; descarte → falha)", () => {
    expect(routingToColumn("mar")).toBe("mar");
    // Phase H: descarte-routed records surface in the Falha column, not a
    // (non-existent) standalone descarte column.
    expect(routingToColumn("descarte")).toBe("falha");
    expect(routingToColumn("dlq")).toBe("dlq");
    // 6-column model: the routing value `in_progress` is the "Rio · validação"
    // column (server twin: _ROUTING_TO_COLUMN in_progress → rio).
    expect(routingToColumn("in_progress")).toBe("rio");
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
    expect(atr.column).toBe("rio");
    expect(atr.municipality).toBeNull();
  });

  it("buckets an atrativo in sub_state aguardando_consulta_whatsapp into the whatsapp column", () => {
    const wa: AtrativoListItem = {
      id: "a-wa",
      entity_type: "attraction",
      uf: "BA",
      routing: "in_progress", // routing still in_progress, but the gate sub_state wins
      sub_state: "aguardando_consulta_whatsapp",
      score: 70,
      name: "Elevador Lacerda",
      validation_pending: true,
      mar_id: null,
      parent_mar_id: null,
      contacts_summary: { phone_masked: "**9999", website: null },
    };
    const cards = toPainelCards([], [wa]);
    expect(cards).toHaveLength(1);
    expect(cards[0].column).toBe("whatsapp");
  });

  it("maps a routing=descarte atrativo into the Falha column (phase H)", () => {
    const descartado: AtrativoListItem = {
      id: "a-descartado",
      entity_type: "attraction",
      uf: "MG",
      routing: "descarte",
      sub_state: null,
      score: 20,
      name: "Ponto Descartado",
      validation_pending: false,
      mar_id: null,
      parent_mar_id: null,
      contacts_summary: null,
    };
    const [card] = toPainelCards([], [descartado]);
    expect(card.routing).toBe("descarte");
    expect(card.column).toBe("falha");
  });

  it("projects whatsapp_eligible onto whatsappEligible (absent ⇒ eligible)", () => {
    const base: Omit<AtrativoListItem, "id" | "whatsapp_eligible"> = {
      entity_type: "attraction",
      uf: "BA",
      routing: "dlq",
      sub_state: null,
      score: 40,
      name: "Atrativo DLQ",
      validation_pending: false,
      mar_id: null,
      parent_mar_id: null,
      contacts_summary: null,
    };
    const cards = toPainelCards([], [
      { ...base, id: "a-elig", whatsapp_eligible: true },
      { ...base, id: "a-inelig", whatsapp_eligible: false },
      { ...base, id: "a-absent" }, // no flag → treated as eligible
    ]);
    expect(cards.find((c) => c.id === "a-elig")!.whatsappEligible).toBe(true);
    expect(cards.find((c) => c.id === "a-inelig")!.whatsappEligible).toBe(false);
    expect(cards.find((c) => c.id === "a-absent")!.whatsappEligible).toBe(true);
  });

  it("projects FailureItem[] into real, draggable falha cards (column=falha, error=reason)", () => {
    const failures: FailureItem[] = [
      {
        id: "f-1",
        task_name: "brave.process_nascente",
        error_message: "ValidationError: origem field required",
        quarantined_at: "2026-06-19T00:00:00Z",
      },
    ];
    const cards = toPainelCards(destinos, atrativos, failures);
    const falha = cards.find((c) => c.id === "f-1")!;
    expect(falha).toBeDefined();
    expect(falha.column).toBe("falha");
    expect(falha.error).toBe("ValidationError: origem field required");
    // Phase H: the Falha column holds BOTH the quarantine failure (f-1) AND the
    // descarte-routed atrativo (a-descarte) — two cards on top of the 3 rendered
    // list cards (d-mar, d-dlq, a-inprog).
    const falhaCards = cards.filter((c) => c.column === "falha");
    expect(falhaCards).toHaveLength(2);
    expect(falhaCards.map((c) => c.id).sort()).toEqual(["a-descarte", "f-1"]);
  });

  it("projects NascenteListItem[] into read-only nascente-column cards", () => {
    const nascente: NascenteListItem[] = [
      {
        id: "n-1",
        entity_type: "destination",
        uf: "BA",
        source: "places",
        name: "Praia do Forte",
        municipio: "Mata de São João",
        municipio_id: "2919926",
        ingested_at: "2026-06-28T00:00:00Z",
      },
      {
        id: "n-2",
        entity_type: "attraction",
        uf: "RJ",
        source: "tripadvisor",
        name: "Pão de Açúcar",
        municipio: null,
        municipio_id: null,
        ingested_at: "2026-06-28T00:01:00Z",
      },
    ];
    const cards = toPainelCards(destinos, atrativos, [], nascente);

    const n1 = cards.find((c) => c.id === "n-1")!;
    expect(n1.column).toBe("nascente");
    expect(n1.type).toBe("destino"); // "destination" → destino
    expect(n1.name).toBe("Praia do Forte");
    expect(n1.uf).toBe("BA");
    expect(n1.source).toBe("places");
    expect(n1.score).toBeNull();
    expect(n1.routing).toBe("nascente");
    // município carried from the item's municipio (surfaced on the card)
    expect(n1.municipality).toBe("Mata de São João");

    const n2 = cards.find((c) => c.id === "n-2")!;
    // entity_type "attraction" → atrativo
    expect(n2.type).toBe("atrativo");
    // no município → municipality stays null (UF-only fallback preserved)
    expect(n2.municipality).toBeNull();
    // Exactly the 2 nascente cards land in the nascente column.
    expect(cards.filter((c) => c.column === "nascente")).toHaveLength(2);
  });

  it("derives `duplicate` from the dedup-candidate id set (a REAL dedup signal), NOT validation_pending", () => {
    // d-dlq and a-descarte have validation_pending=true but are NOT dedup
    // candidates. The old code blanket-flagged them ("possível duplicado" on the
    // whole DLQ column); the fix flags a card ONLY when its rio id is a pending
    // candidate↔Mar dedup pair (the same source the Duplicados view reads).
    const dedupCandidateIds = new Set(["d-mar", "a-inprog"]);
    const cards = toPainelCards(destinos, atrativos, [], [], dedupCandidateIds);
    expect(cards.find((c) => c.id === "d-mar")!.duplicate).toBe(true);
    expect(cards.find((c) => c.id === "a-inprog")!.duplicate).toBe(true);
    // validation_pending=true but not a dedup candidate ⇒ NOT flagged.
    expect(cards.find((c) => c.id === "d-dlq")!.duplicate).toBe(false);
    expect(cards.find((c) => c.id === "a-descarte")!.duplicate).toBe(false);
    // No dedup set passed (default empty) ⇒ nothing is flagged.
    expect(
      toPainelCards(destinos, atrativos).every((c) => !c.duplicate),
    ).toBe(true);
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
  it("returns the 6 ordered stage columns with cards bucketed by column", () => {
    const cards = toPainelCards(destinos, atrativos);
    const cols = buildColumns(cards);
    expect(cols.map((c) => c.key)).toEqual([
      "nascente",
      "rio",
      "whatsapp",
      "mar",
      "dlq",
      "falha",
    ]);
    const byKey = Object.fromEntries(cols.map((c) => [c.key, c.cards.length]));
    expect(byKey.nascente).toBe(0);
    expect(byKey.rio).toBe(1); // a-inprog (in_progress → rio)
    expect(byKey.whatsapp).toBe(0);
    expect(byKey.mar).toBe(1);
    expect(byKey.dlq).toBe(1);
    // Phase H: a-descarte (routing=descarte) now lands in the Falha column.
    expect(byKey.falha).toBe(1);
  });

  it("uses COLUMN_DEFS labels in order (6 columns)", () => {
    expect(COLUMN_DEFS).toHaveLength(6);
    expect(COLUMN_DEFS.map((c) => c.label)).toEqual([
      "Nascente",
      "Rio · validação",
      "WhatsApp · contato",
      "Mar · publicado",
      "DLQ · revisão",
      "Falha",
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
    server.use(
      destinosListSuccess(),
      atrativosListSuccess(),
      failuresEmpty(),
      dedupPairsEmpty(),
    );
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
  it("derives truthful per-entity metrics from envelope totals + nascente from the nascente list total", async () => {
    server.use(
      destinosListSuccess(),
      atrativosListSuccess(),
      nascenteList([], 9),
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

    // Nascente COLUMN count comes from the /nascente envelope total
    expect(result.current.nascenteCount).toBe(9);
  });
});
