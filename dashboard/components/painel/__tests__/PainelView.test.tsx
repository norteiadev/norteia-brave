import { fireEvent, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelView } from "@/components/painel/PainelView";
import type { AtrativoListItem } from "@/lib/atrativos-api";
import {
  atrativosListSuccess,
  atrativoTransitionSuccess,
  failureCardsSuccess,
} from "@/mocks/handlers/atrativos";
import type { FailureCard } from "@/lib/atrativos-api";
import {
  destinoReprocessSuccess,
  destinoTransitionSuccess,
  destinosListSuccess,
} from "@/mocks/handlers/destinos";
import {
  dlqWhatsappBatchIneligible,
  dlqWhatsappBatchSuccess,
} from "@/mocks/handlers/dlq";
import { dedupPairsEmpty } from "@/mocks/handlers/dedup";
import { engineStatus, nascenteEmpty, nascenteList } from "@/mocks/handlers/engine";
import { failuresEmpty } from "@/mocks/handlers/workers";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

/** A DLQ-column atrativo fixture for the WhatsApp multi-select flow. */
function dlqAtrativo(
  id: string,
  eligible: boolean,
  name = "Atrativo DLQ",
): AtrativoListItem {
  return {
    id,
    entity_type: "attraction",
    uf: "BA",
    routing: "dlq",
    sub_state: null,
    score: 42,
    name,
    validation_pending: false,
    mar_id: null,
    parent_mar_id: null,
    contacts_summary: null,
    whatsapp_eligible: eligible,
  };
}

// Spy on sonner so unmapped-drop toasts can be asserted without a Toaster.
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));
import { toast } from "sonner";

const DESTINO_DLQ_ID = "11111111-1111-1111-1111-111111111111"; // Pelourinho, routing=dlq → rio? no: dlq column
const DESTINO_MAR_ID = "22222222-2222-2222-2222-222222222222"; // Copacabana, routing=mar
const ATRATIVO_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"; // Mercado Modelo, in_progress → rio

/** Every request the suite observes (method + url), for PATCH assertions. */
const requests: { method: string; url: string }[] = [];

beforeEach(() => {
  requests.length = 0;
  vi.clearAllMocks();
  server.events.on("request:start", ({ request }) => {
    requests.push({ method: request.method, url: request.url });
  });
});

afterEach(() => {
  server.events.removeAllListeners();
});

function patchesTo(fragment: string): boolean {
  return requests.some(
    (r) => r.method === "PATCH" && r.url.includes(fragment),
  );
}

function useDefaultHandlers() {
  server.use(
    destinosListSuccess(),
    atrativosListSuccess(),
    failuresEmpty(),
    engineStatus(),
    dedupPairsEmpty(),
    // Nascente column count now comes from the /nascente envelope total, not
    // engine counts — seed total=12 (no cards needed for the count assertion).
    nascenteList([], 12),
    destinoReprocessSuccess(),
    destinoTransitionSuccess(),
    atrativoTransitionSuccess(),
  );
}

describe("PainelView", () => {
  it("renders the real board cards after load", async () => {
    useDefaultHandlers();
    const { findAllByTestId } = renderWithClient(<PainelView />);

    const cards = await findAllByTestId("record-card");
    expect(cards).toHaveLength(4); // 2 destinos + 2 atrativos
  });

  it("uses usePainelMetrics().nascenteCount for the Nascente column count", async () => {
    useDefaultHandlers();
    const { findByTestId } = renderWithClient(<PainelView />);

    const nascente = await findByTestId("painel-col-count-nascente");
    await waitFor(() => expect(nascente).toHaveTextContent("12"));
  });

  it("Nascente is a COUNT-ONLY column: shows the /nascente envelope total but renders no raw cards (F2 double-count fix)", async () => {
    server.use(
      destinosListSuccess([]),
      atrativosListSuccess([]),
      failuresEmpty(),
      engineStatus(),
      dedupPairsEmpty(),
      // Envelope total=7 drives the pill; the raw rows are intentionally NOT
      // surfaced as cards — they duplicate the routed (DLQ/Rio/Mar) layer.
      nascenteList([], 7),
    );
    const { getByTestId, queryByTestId } = renderWithClient(<PainelView />);

    // The Nascente header count reflects the envelope total (usePainelMetrics)…
    await waitFor(() =>
      expect(getByTestId("painel-col-count-nascente")).toHaveTextContent("7"),
    );
    // …and NO raw nascente card is rendered: the board no longer feeds the raw
    // ingest layer, so each record appears exactly once in its routed column.
    expect(queryByTestId("record-card")).toBeNull();
  });

  it("mapped drop (destino dlq → Mar) fires the generic transition PATCH and optimistically moves the card", async () => {
    useDefaultHandlers();
    const { container, findAllByTestId, getByTestId } = renderWithClient(
      <PainelView />,
    );

    await findAllByTestId("record-card"); // wait for load
    // Pelourinho is a dlq destino → (dlq, mar) is an allowed server edge.
    const card = container.querySelector(`[data-id="${DESTINO_DLQ_ID}"]`);
    expect(card).not.toBeNull();

    fireEvent.dragStart(card as Element);
    fireEvent.drop(getByTestId("painel-col-mar"));

    // The ONE generic, audited transition endpoint fired …
    await waitFor(() =>
      expect(patchesTo(`/destinos/${DESTINO_DLQ_ID}/transition`)).toBe(true),
    );
    // … and the card optimistically joins Mar (Copacabana + Pelourinho = 2).
    await waitFor(() =>
      expect(getByTestId("painel-col-count-mar")).toHaveTextContent("2"),
    );
  });

  it("unmapped drop (mar → Revisão) fires NO request and toasts the unavailable message", async () => {
    useDefaultHandlers();
    const { container, findAllByTestId, getByTestId } = renderWithClient(
      <PainelView />,
    );

    await findAllByTestId("record-card");
    // A live Mar destino can never move backward: (mar, dlq) is absent server-side.
    const card = container.querySelector(`[data-id="${DESTINO_MAR_ID}"]`);
    expect(card).not.toBeNull();

    fireEvent.dragStart(card as Element);
    fireEvent.drop(getByTestId("painel-col-dlq"));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Ação não disponível neste estágio",
      ),
    );
    // No mutation: no PATCH escaped to any endpoint.
    expect(requests.some((r) => r.method === "PATCH")).toBe(false);
  });

  it("clicking a record-card body opens the drawer with the card's id", async () => {
    useDefaultHandlers();
    const { container, findAllByTestId, getByTestId } = renderWithClient(
      <PainelView />,
    );

    await findAllByTestId("record-card"); // wait for load
    // Drawer starts closed: the id field shows the empty placeholder.
    expect(getByTestId("drawer-field-id")).toHaveTextContent("—");

    const card = container.querySelector(`[data-id="${DESTINO_MAR_ID}"]`);
    expect(card).not.toBeNull();
    fireEvent.click(card as Element);

    await waitFor(() =>
      expect(getByTestId("drawer-field-id")).toHaveTextContent(DESTINO_MAR_ID),
    );
  });

  // A real falha card sourced from GET /api/v1/failures/cards (RecordEvent
  // fail-timeline; legacy PoisonQuarantine rows are LEFT-merged server-side). A
  // non-"attraction" entity_type projects as a destino falha card, so its id is
  // the source_ref and ↺ Reprocessar maps to reprocessDestino(source_ref).
  const FAILURE_ID = "33333333-3333-3333-3333-333333333333";
  const falhaSeed: FailureCard[] = [
    {
      source_ref: FAILURE_ID,
      name: "Registro com falha",
      uf: null,
      entity_type: "destination",
      last_stage: "quarantined",
      error: "ValidationError: origem field required",
      quarantined_at: "2026-06-19T00:00:00Z",
    },
  ];

  it("↺ Reprocessar does NOT open the drawer (stopPropagation)", async () => {
    server.use(
      destinosListSuccess([]),
      atrativosListSuccess([]),
      failureCardsSuccess(falhaSeed),
      engineStatus(),
      nascenteEmpty(),
      dedupPairsEmpty(),
      destinoReprocessSuccess(),
    );

    const { findByTestId, getByTestId } = renderWithClient(<PainelView />);

    const retry = await findByTestId("record-card-retry");
    fireEvent.click(retry);

    await waitFor(() =>
      expect(patchesTo(`/destinos/${FAILURE_ID}/reprocess`)).toBe(true),
    );
    // The drawer must stay closed — the retry click stopped propagation.
    expect(getByTestId("drawer-field-id")).toHaveTextContent("—");
  });

  it("↺ Reprocessar on a falha card fires the reprocess PATCH", async () => {
    server.use(
      destinosListSuccess([]),
      atrativosListSuccess([]),
      failureCardsSuccess(falhaSeed),
      engineStatus(),
      nascenteEmpty(),
      dedupPairsEmpty(),
      destinoReprocessSuccess(),
    );

    const { findByTestId } = renderWithClient(<PainelView />);

    const retry = await findByTestId("record-card-retry");
    fireEvent.click(retry);

    await waitFor(() =>
      expect(patchesTo(`/destinos/${FAILURE_ID}/reprocess`)).toBe(true),
    );
  });

  // ---------------------------------------------------------------------------
  // Phase H — DLQ→WhatsApp multi-select + edit-lock
  // ---------------------------------------------------------------------------

  it("multi-selects eligible DLQ atrativos (ineligible disabled) and moves them to WhatsApp with branch feedback", async () => {
    let batchBody: { rio_ids: string[] } | null = null;
    server.use(
      destinosListSuccess([]),
      atrativosListSuccess([
        dlqAtrativo("atr-elig", true, "Elegível"),
        dlqAtrativo("atr-inelig", false, "Inelegível"),
      ]),
      failuresEmpty(),
      // PAUSADO ⇒ editing unlocked ⇒ checkboxes render.
      engineStatus({ mode: "PAUSADO", editing_unlocked: true }),
      nascenteEmpty(),
      dedupPairsEmpty(),
      http.post(
        "http://localhost:3000/api/api/v1/dlq/whatsapp-batch",
        async ({ request }) => {
          batchBody = (await request.json()) as { rio_ids: string[] };
          return HttpResponse.json(
            { status: "accepted", moved: 1, outreach: 1, discovery: 0 },
            { status: 202 },
          );
        },
      ),
    );
    const { container, findAllByTestId, getByTestId } = renderWithClient(
      <PainelView />,
    );
    await findAllByTestId("record-card");

    const eligBox = container.querySelector(
      '[data-id="atr-elig"] input[type="checkbox"]',
    ) as HTMLInputElement;
    const ineligBox = container.querySelector(
      '[data-id="atr-inelig"] input[type="checkbox"]',
    ) as HTMLInputElement;
    expect(eligBox).not.toBeNull();
    expect(ineligBox).not.toBeNull();
    // Ineligible (has horário/preço) → checkbox disabled; eligible → enabled.
    expect(eligBox.disabled).toBe(false);
    expect(ineligBox.disabled).toBe(true);

    // Selecting the eligible card reveals the batch bar.
    fireEvent.click(eligBox);
    getByTestId("whatsapp-batch-bar");
    fireEvent.click(getByTestId("whatsapp-batch-btn"));

    // The POST carries the selected rio_id …
    await waitFor(() => expect(batchBody).not.toBeNull());
    expect(batchBody!.rio_ids).toEqual(["atr-elig"]);
    // … and the outreach/discovery split is surfaced as branch feedback.
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        expect.stringContaining("conversa(s) iniciada(s)"),
      ),
    );
    // LGPD: no phone leaks onto the board.
    expect(container.innerHTML).not.toContain("phone");
  });

  it("toasts the per-item ineligibility on a 422 batch (atomic — nothing moved)", async () => {
    server.use(
      destinosListSuccess([]),
      atrativosListSuccess([dlqAtrativo("atr-1", true)]),
      failuresEmpty(),
      engineStatus({ mode: "PAUSADO", editing_unlocked: true }),
      nascenteEmpty(),
      dedupPairsEmpty(),
      dlqWhatsappBatchIneligible([
        { rio_id: "atr-1", reason: "has_horario_or_preco" },
      ]),
    );
    const { container, findAllByTestId, getByTestId } = renderWithClient(
      <PainelView />,
    );
    await findAllByTestId("record-card");

    const box = container.querySelector(
      '[data-id="atr-1"] input[type="checkbox"]',
    ) as HTMLInputElement;
    fireEvent.click(box);
    fireEvent.click(getByTestId("whatsapp-batch-btn"));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("inelegível"),
      ),
    );
    // Atomic: the reason is spelled out and nothing was moved.
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("já tem horário/preço"),
    );
  });

  it("edit-lock: a 423 from the transition reverts the optimistic move + toasts", async () => {
    server.use(
      destinosListSuccess(),
      atrativosListSuccess([]),
      failuresEmpty(),
      // Client believes editing is unlocked (draggable), but the server 423s —
      // the Motor Pausado backstop: revert + toast.
      engineStatus({ mode: "PAUSADO", editing_unlocked: true }),
      nascenteEmpty(),
      dedupPairsEmpty(),
      http.patch(
        "http://localhost:3000/api/api/v1/destinos/:id/transition",
        () => HttpResponse.json({ detail: "Edição bloqueada" }, { status: 423 }),
      ),
    );
    const { container, findAllByTestId, getByTestId } = renderWithClient(
      <PainelView />,
    );
    await findAllByTestId("record-card");

    const card = container.querySelector(`[data-id="${DESTINO_DLQ_ID}"]`);
    fireEvent.dragStart(card as Element);
    fireEvent.drop(getByTestId("painel-col-mar"));

    // 423 arm of explainError.
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("Motor ligado"),
      ),
    );
    // Optimistic move reverted: Pelourinho back in DLQ, Mar stays at 1 (Copacabana).
    await waitFor(() =>
      expect(getByTestId("painel-col-count-dlq")).toHaveTextContent("1"),
    );
    await waitFor(() =>
      expect(getByTestId("painel-col-count-mar")).toHaveTextContent("1"),
    );
  });

  it("edit-lock: when LIGADO the cards are not draggable and a drop fires no mutation", async () => {
    server.use(
      destinosListSuccess(),
      atrativosListSuccess([]),
      failuresEmpty(),
      engineStatus({ mode: "LIGADO", editing_unlocked: false }),
      nascenteEmpty(),
      dedupPairsEmpty(),
      destinoTransitionSuccess(),
    );
    const { container, findAllByTestId, getByTestId } = renderWithClient(
      <PainelView />,
    );
    await findAllByTestId("record-card");

    const card = container.querySelector(`[data-id="${DESTINO_DLQ_ID}"]`);
    // Once the LIGADO status resolves the card locks (draggable=false).
    await waitFor(() =>
      expect(card?.getAttribute("draggable")).toBe("false"),
    );

    fireEvent.dragStart(card as Element);
    fireEvent.drop(getByTestId("painel-col-mar"));
    await new Promise((r) => setTimeout(r, 50));
    // No transition escaped while locked.
    expect(requests.some((r) => r.method === "PATCH")).toBe(false);
  });
});
