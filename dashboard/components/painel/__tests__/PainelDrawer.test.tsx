import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelDrawer } from "@/components/painel/PainelDrawer";
import type { PainelCard } from "@/lib/painel-data";
import { destinoTransitionSuccess } from "@/mocks/handlers/destinos";
import {
  conversationDetailSuccess,
  conversationDetailNotFound,
} from "@/mocks/handlers/conversations";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

// Spy on sonner so mutation toasts don't require a Toaster.
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));

const DESTINO_ID = "22222222-2222-2222-2222-222222222222";

const destinoCard: PainelCard = {
  id: DESTINO_ID,
  type: "destino",
  name: "Copacabana",
  uf: "RJ",
  municipality: "rio",
  routing: "mar",
  column: "mar",
  score: 91.2,
  source: null,
  duplicate: false,
  error: null,
};

// A rio-stage destino: (rio → descarte) IS an allowed server edge, so the
// drawer "Descartar" button resolves to a real generic transition call.
const destinoRioCard: PainelCard = {
  ...destinoCard,
  routing: "in_progress",
  column: "rio",
};

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

describe("PainelDrawer", () => {
  it("renders read-only fields for a destino card", () => {
    const { getByTestId } = renderWithClient(
      <PainelDrawer card={destinoCard} onClose={vi.fn()} />,
    );

    expect(getByTestId("drawer-field-name")).toHaveTextContent("Copacabana");
    expect(getByTestId("drawer-field-uf")).toHaveTextContent("RJ");
    expect(getByTestId("drawer-field-score")).toHaveTextContent("91.2");
    expect(getByTestId("drawer-field-stage")).toHaveTextContent(
      "Mar · publicado",
    );
    expect(getByTestId("drawer-field-type")).toHaveTextContent("Destino");

    // Values are static text, never <input>.
    expect(getByTestId("drawer-field-name").tagName).not.toBe("INPUT");
  });

  it("overlay click calls onClose", () => {
    const onClose = vi.fn();
    const { getByTestId } = renderWithClient(
      <PainelDrawer card={destinoCard} onClose={onClose} />,
    );

    fireEvent.click(getByTestId("drawer-overlay"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Descartar fires the generic transition PATCH and calls onClose", async () => {
    server.use(destinoTransitionSuccess());
    const onClose = vi.fn();
    const { getByTestId } = renderWithClient(
      <PainelDrawer card={destinoRioCard} onClose={onClose} />,
    );

    fireEvent.click(getByTestId("drawer-descartar"));

    await waitFor(() =>
      expect(patchesTo(`/destinos/${DESTINO_ID}/transition`)).toBe(true),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("card=null → overlay is not interactive (pointer-events none)", () => {
    const { getByTestId } = renderWithClient(
      <PainelDrawer card={null} onClose={vi.fn()} />,
    );

    expect(getByTestId("drawer-overlay")).toHaveStyle({
      pointerEvents: "none",
    });
  });

  it("Conversa tab renders bubbles + extracted after switching tabs", async () => {
    server.use(conversationDetailSuccess());
    const { getByTestId, findByText } = renderWithClient(
      <PainelDrawer card={destinoCard} onClose={vi.fn()} />,
    );

    fireEvent.click(getByTestId("drawer-tab-conversa"));

    expect(
      await findByText(/Funciona de terça a domingo/),
    ).toBeInTheDocument();
    // The extracted snapshot from msg-3 renders as a <pre> JSON block.
    expect(await findByText(/opening_hours/)).toBeInTheDocument();
  });

  it("Conversa tab shows the empty message when no conversation exists (404)", async () => {
    server.use(conversationDetailNotFound());
    const { getByTestId, findByTestId } = renderWithClient(
      <PainelDrawer card={destinoCard} onClose={vi.fn()} />,
    );

    fireEvent.click(getByTestId("drawer-tab-conversa"));

    expect(await findByTestId("drawer-convo-empty")).toHaveTextContent(
      "Nenhuma conversa de WhatsApp iniciada para este registro ainda.",
    );
  });
});
