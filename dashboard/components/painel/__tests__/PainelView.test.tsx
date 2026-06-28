import { fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelView } from "@/components/painel/PainelView";
import {
  atrativosListSuccess,
  atrativoTransitionSuccess,
} from "@/mocks/handlers/atrativos";
import {
  destinoReprocessSuccess,
  destinoTransitionSuccess,
  destinosListSuccess,
} from "@/mocks/handlers/destinos";
import { engineStatus, nascenteEmpty, nascenteList } from "@/mocks/handlers/engine";
import { failuresEmpty, failuresSuccess } from "@/mocks/handlers/workers";
import type { FailuresData } from "@/lib/workers-api";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

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

  it("renders raw Nascente records as read-only (non-draggable) cards in the Nascente column", async () => {
    server.use(
      destinosListSuccess([]),
      atrativosListSuccess([]),
      failuresEmpty(),
      engineStatus(),
      nascenteList([
        {
          id: "n-1",
          entity_type: "destination",
          uf: "BA",
          source: "places",
          name: "Praia do Forte",
          ingested_at: "2026-06-28T00:00:00Z",
        },
      ]),
    );
    const { findByTestId, getByTestId } = renderWithClient(<PainelView />);

    // The card lands inside the Nascente column body…
    const col = getByTestId("painel-col-nascente");
    const card = await findByTestId("record-card");
    expect(col.contains(card)).toBe(true);
    // …and it is READ-ONLY: not draggable.
    expect(card.getAttribute("draggable")).toBe("false");
    // The column header count reflects the one ingested record.
    await waitFor(() =>
      expect(getByTestId("painel-col-count-nascente")).toHaveTextContent("1"),
    );
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

  // A real falha card sourced from GET /api/v1/failures (PoisonQuarantine). Its
  // task_name has no attraction marker → it projects as a destino falha card, so
  // ↺ Reprocessar maps to reprocessDestino(failureId).
  const FAILURE_ID = "33333333-3333-3333-3333-333333333333";
  const falhaSeed: FailuresData = {
    total: 1,
    by_task: { "brave.process_nascente": 1 },
    items: [
      {
        id: FAILURE_ID,
        task_name: "brave.process_nascente",
        error_message: "ValidationError: origem field required",
        quarantined_at: "2026-06-19T00:00:00Z",
      },
    ],
  };

  it("↺ Reprocessar does NOT open the drawer (stopPropagation)", async () => {
    server.use(
      destinosListSuccess([]),
      atrativosListSuccess([]),
      failuresSuccess(falhaSeed),
      engineStatus(),
      nascenteEmpty(),
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
      failuresSuccess(falhaSeed),
      engineStatus(),
      nascenteEmpty(),
      destinoReprocessSuccess(),
    );

    const { findByTestId } = renderWithClient(<PainelView />);

    const retry = await findByTestId("record-card-retry");
    fireEvent.click(retry);

    await waitFor(() =>
      expect(patchesTo(`/destinos/${FAILURE_ID}/reprocess`)).toBe(true),
    );
  });
});
