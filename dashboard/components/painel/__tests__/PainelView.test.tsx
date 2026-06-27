import { fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelView } from "@/components/painel/PainelView";
import type { DestinoListItem } from "@/lib/destinos-api";
import {
  atrativoDescarteSuccess,
  atrativosListSuccess,
} from "@/mocks/handlers/atrativos";
import {
  destinoDescarteSuccess,
  destinoPromoteSuccess,
  destinoReprocessSuccess,
  destinosListSuccess,
} from "@/mocks/handlers/destinos";
import { engineStatus } from "@/mocks/handlers/engine";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

// Spy on sonner so unmapped-drop toasts can be asserted without a Toaster.
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));
import { toast } from "sonner";

const DESTINO_MAR_ID = "22222222-2222-2222-2222-222222222222"; // Copacabana, routing=mar
const ATRATIVO_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"; // Mercado Modelo, in_progress

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
    engineStatus({
      counts: {
        nascente: 12,
        rio: { in_progress: 0, mar: 0, dlq: 0, descarte: 0 },
        mar: 0,
        atrativos_by_sub_state: {},
      },
    }),
    destinoDescarteSuccess(),
    destinoReprocessSuccess(),
    destinoPromoteSuccess(),
    atrativoDescarteSuccess(),
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

  it("mapped drop (destino → Descarte) fires the real descarte PATCH and optimistically moves the card", async () => {
    useDefaultHandlers();
    const { container, findByTestId, getByTestId } = renderWithClient(
      <PainelView />,
    );

    await findByTestId("record-card"); // wait for load
    const card = container.querySelector(`[data-id="${DESTINO_MAR_ID}"]`);
    expect(card).not.toBeNull();

    fireEvent.dragStart(card as Element);
    fireEvent.drop(getByTestId("painel-col-descarte"));

    // Real descarte PATCH fired …
    await waitFor(() =>
      expect(patchesTo(`/destinos/${DESTINO_MAR_ID}/descarte`)).toBe(true),
    );
    // … and the card optimistically appears under Descarte (count 0 → 1).
    await waitFor(() =>
      expect(getByTestId("painel-col-count-descarte")).toHaveTextContent("1"),
    );
  });

  it("unmapped drop (atrativo → Revisão) fires NO request and toasts the unavailable message", async () => {
    useDefaultHandlers();
    const { container, findByTestId, getByTestId } = renderWithClient(
      <PainelView />,
    );

    await findByTestId("record-card");
    const card = container.querySelector(`[data-id="${ATRATIVO_ID}"]`);
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

  it("↺ Reprocessar on a seeded descarte destino fires the reprocess PATCH", async () => {
    const seed: DestinoListItem = {
      id: "33333333-3333-3333-3333-333333333333",
      entity_type: "destination",
      uf: "MG",
      routing: "descarte",
      score: 21.0,
      name: "Falha X",
      canonical_key: "mg:x:falha",
      validation_pending: false,
      mar_id: null,
      published_at: null,
    };
    server.use(
      destinosListSuccess([seed]),
      atrativosListSuccess([]),
      engineStatus(),
      destinoReprocessSuccess(),
    );

    const { findByTestId } = renderWithClient(<PainelView />);

    const retry = await findByTestId("record-card-retry");
    fireEvent.click(retry);

    await waitFor(() =>
      expect(patchesTo(`/destinos/${seed.id}/reprocess`)).toBe(true),
    );
  });
});
