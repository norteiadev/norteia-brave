import { fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelView } from "@/components/painel/PainelView";
import { atrativosListSuccess } from "@/mocks/handlers/atrativos";
import { dedupPairsEmpty } from "@/mocks/handlers/dedup";
import { destinosListSuccess } from "@/mocks/handlers/destinos";
import {
  engineStatus,
  marRepushSuccess,
  nascenteEmpty,
} from "@/mocks/handlers/engine";
import { failuresEmpty } from "@/mocks/handlers/workers";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));
import { toast } from "sonner";

const REPUSH_URL = "http://localhost:3000/api/api/v1/mar/repush";
let repushPosts = 0;

function mount(norteia_api?: { up: boolean | null; pending: number }) {
  server.use(
    destinosListSuccess([]),
    atrativosListSuccess([]),
    failuresEmpty(),
    engineStatus(norteia_api ? { norteia_api } : {}),
    dedupPairsEmpty(),
    nascenteEmpty(),
  );
  return renderWithClient(<PainelView />);
}

beforeEach(() => {
  repushPosts = 0;
  vi.clearAllMocks();
  server.events.on("request:start", ({ request }) => {
    if (request.method === "POST" && request.url === REPUSH_URL) repushPosts += 1;
  });
});

afterEach(() => {
  server.events.removeAllListeners();
});

describe("Painel — sync com a norteia-api", () => {
  it("online + pendentes → Reenviar dispatches and toasts the count", async () => {
    server.use(marRepushSuccess(4));
    const { findByTestId, getByTestId } = mount({ up: true, pending: 4 });

    expect((await findByTestId("api-sync-label")).textContent).toBe(
      "norteia-api online · 4 pendentes de envio",
    );
    fireEvent.click(getByTestId("reenviar-btn"));

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "4 reenviados para a norteia-api.",
      ),
    );
    expect(repushPosts).toBe(1);
  });

  it("fora do ar → label says so and Reenviar is disabled", async () => {
    const { findByTestId, getByTestId } = mount({ up: false, pending: 1 });

    expect((await findByTestId("api-sync-label")).textContent).toBe(
      "norteia-api fora do ar · 1 pendente de envio",
    );
    expect(getByTestId("reenviar-btn")).toBeDisabled();
  });

  it("online with nothing pending shows no Reenviar button", async () => {
    const { findByTestId, queryByTestId } = mount({ up: true, pending: 0 });

    expect((await findByTestId("api-sync-label")).textContent).toBe(
      "norteia-api online",
    );
    expect(queryByTestId("reenviar-btn")).toBeNull();
  });

  it("hidden while externals are off (up=null) or the block is absent", async () => {
    const { findByTestId, queryByTestId } = mount({ up: null, pending: 9 });
    await findByTestId("promover-lote-btn");
    await waitFor(() => expect(queryByTestId("api-sync")).toBeNull());
  });

  it("surfaces the server's 503 detail when the API went down meanwhile", async () => {
    server.use(
      http.post(REPUSH_URL, () =>
        HttpResponse.json({ detail: "norteia-api fora do ar" }, { status: 503 }),
      ),
    );
    const { findByTestId } = mount({ up: true, pending: 2 });
    fireEvent.click(await findByTestId("reenviar-btn"));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
  });
});
