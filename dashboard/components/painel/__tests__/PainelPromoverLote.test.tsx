import { fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelView } from "@/components/painel/PainelView";
import {
  atrativosListSuccess,
  promoteBulkDryRunSuccess,
  promoteBulkRunSuccess,
  promoteBulkZeroCandidates,
} from "@/mocks/handlers/atrativos";
import { dedupPairsEmpty } from "@/mocks/handlers/dedup";
import { destinosListSuccess } from "@/mocks/handlers/destinos";
import { engineStatus, nascenteEmpty } from "@/mocks/handlers/engine";
import { failuresEmpty } from "@/mocks/handlers/workers";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));
import { toast } from "sonner";

const BULK_URL = "http://localhost:3000/api/api/v1/atrativos/promote-bulk";
const LOCKED_COPY = "Motor ligado — pause o motor para editar os cards.";

/** Bodies of every POST to promote-bulk, in order. */
const bulkBodies: Record<string, unknown>[] = [];
let atrativoListGets = 0;

beforeEach(() => {
  bulkBodies.length = 0;
  atrativoListGets = 0;
  vi.clearAllMocks();
  server.events.on("request:start", async ({ request }) => {
    if (request.method === "POST" && request.url === BULK_URL) {
      bulkBodies.push(await request.clone().json());
    }
    if (request.method === "GET" && request.url.includes("/api/v1/atrativos?")) {
      atrativoListGets += 1;
    }
  });
  server.use(
    destinosListSuccess([]),
    atrativosListSuccess([]),
    failuresEmpty(),
    engineStatus(),
    dedupPairsEmpty(),
    nascenteEmpty(),
  );
});

afterEach(() => {
  server.events.removeAllListeners();
  vi.restoreAllMocks();
});

describe("Painel — Promover em lote", () => {
  it("dry-run → confirm → real run → toast + board refetch, scoped to the UF filter", async () => {
    server.use(promoteBulkDryRunSuccess());

    const { getByTestId, findByTestId } = renderWithClient(<PainelView />);
    fireEvent.click(getByTestId("filter-uf-trigger"));
    fireEvent.click(getByTestId("filter-uf-BA"));
    await waitFor(() => expect(atrativoListGets).toBeGreaterThan(0));
    const getsBeforeRun = atrativoListGets;

    fireEvent.click(getByTestId("promover-lote-btn"));

    const dialog = await findByTestId("promover-lote-dialog");
    const message = dialog.textContent ?? "";
    expect(message).toContain("Promover 12 atrativos");
    expect(message).toContain("12 elegíveis em BA");
    // Swap in the real-run handler at the moment the steward confirms.
    server.use(promoteBulkRunSuccess({ promoted: 10, remaining: 2 }));
    fireEvent.click(getByTestId("promover-lote-confirm"));

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(bulkBodies).toEqual([
      { uf: "BA", dry_run: true },
      { uf: "BA", dry_run: false },
    ]);
    expect(message).toContain("3 por score");
    expect(message).toContain("2 sem descrição");
    expect(message).toContain("1 sem review recente");
    expect(toast.success).toHaveBeenCalledWith(
      "10 promovidos, 0 retidos, 0 falharam. Restam 2.",
    );
    // Invalidation → the board refetches the atrativos list.
    await waitFor(() =>
      expect(atrativoListGets).toBeGreaterThan(getsBeforeRun),
    );
    expect(getByTestId("promover-lote-btn")).not.toBeDisabled();
  });

  it("cancelling the confirm makes no second request", async () => {
    server.use(promoteBulkDryRunSuccess());

    const { getByTestId, findByTestId, queryByTestId } = renderWithClient(
      <PainelView />,
    );
    fireEvent.click(getByTestId("promover-lote-btn"));

    fireEvent.click(await findByTestId("promover-lote-cancel"));
    await waitFor(() =>
      expect(queryByTestId("promover-lote-dialog")).toBeNull(),
    );
    expect(getByTestId("promover-lote-btn")).not.toBeDisabled();
    expect(bulkBodies).toEqual([{ uf: null, dry_run: true }]);
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("zero candidates never opens the confirm", async () => {
    server.use(promoteBulkZeroCandidates());

    const { getByTestId, queryByTestId } = renderWithClient(<PainelView />);
    fireEvent.click(getByTestId("promover-lote-btn"));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(queryByTestId("promover-lote-dialog")).toBeNull();
    expect(bulkBodies).toHaveLength(1);
  });

  it("disables the button while a request is in flight", async () => {
    server.use(promoteBulkZeroCandidates());
    const { getByTestId } = renderWithClient(<PainelView />);
    const btn = getByTestId("promover-lote-btn");
    fireEvent.click(btn);
    expect(btn).toBeDisabled();
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  it("surfaces the edit-lock copy on a 423", async () => {
    server.use(
      http.post(BULK_URL, () =>
        HttpResponse.json({ detail: "locked" }, { status: 423 }),
      ),
    );
    const { getByTestId, queryByTestId } = renderWithClient(<PainelView />);
    fireEvent.click(getByTestId("promover-lote-btn"));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(LOCKED_COPY));
    expect(queryByTestId("promover-lote-dialog")).toBeNull();
  });
});
