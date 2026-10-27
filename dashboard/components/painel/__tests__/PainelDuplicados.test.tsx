import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Toaster } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelDuplicados } from "@/components/painel/PainelDuplicados";
import {
  dedupPairsEmpty,
  dedupPairsSuccess,
  dedupResolveSuccess,
  sampleDedupPairs,
} from "@/mocks/handlers/dedup";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

const requests: { method: string; url: string }[] = [];
let resolves: { id: string; body: unknown }[] = [];

function renderDup() {
  return renderWithClient(
    <>
      <Toaster />
      <PainelDuplicados />
    </>,
  );
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  requests.length = 0;
  resolves = [];
  server.events.on("request:start", ({ request }) => {
    requests.push({ method: request.method, url: request.url });
  });
  server.use(
    dedupPairsSuccess(),
    dedupResolveSuccess((id, body) => resolves.push({ id, body })),
  );
});

afterEach(() => {
  server.events.removeAllListeners();
  vi.useRealTimers();
});

describe("PainelDuplicados", () => {
  it("lists the pairs and details the selected one with coincide/diverge tags and a labeled similarity", async () => {
    renderDup();

    const rows = await screen.findAllByTestId("dedup-pair");
    expect(rows).toHaveLength(sampleDedupPairs.length);
    expect(rows[0]).toHaveAttribute("aria-selected", "true");
    expect(rows[0]).toHaveTextContent("Fortaleza");
    expect(rows[0]).toHaveTextContent("0,95");

    const detail = screen.getByTestId("dedup-detail");
    expect(within(detail).getAllByTestId("dedup-matched-chip").map((c) => c.textContent)).toEqual([
      "name",
      "municipio",
      "uf",
    ]);
    expect(within(detail).getByTestId("dedup-similarity")).toHaveTextContent("0,95");
    expect(detail).toHaveTextContent("sobreposição de tokens (embedding pendente)");
    expect(detail).toHaveTextContent("mar-ce-0142");
    // Merge copy is honest about what the API does today.
    expect(detail).toHaveTextContent("Não preenche campos do Mar");

    fireEvent.click(rows[1]);
    expect(screen.getByTestId("dedup-detail")).toHaveTextContent("Salvador");
  });

  it("filters by UF on the server", async () => {
    renderDup();
    await screen.findAllByTestId("dedup-pair");

    fireEvent.change(screen.getByLabelText("UF"), { target: { value: "BA" } });

    await waitFor(() =>
      expect(requests.some((r) => r.url.includes("/dedup/pairs?uf=BA"))).toBe(true),
    );
  });

  it("never renders a phone, a contact or a raw object from the diff", async () => {
    server.use(
      dedupPairsSuccess([
        {
          ...sampleDedupPairs[0],
          diverged_fields: [
            { field: "phone", candidate: "+55 85 99999-1234", mar: "+55 85 98888-4321" },
            {
              field: "contacts",
              candidate: { phone_e164: "+5585999991234", email: "a@b.com" },
              mar: null,
            },
            { field: "name", candidate: "Praça do Ferreira", mar: "Praça do Ferreira (centro)" },
          ],
        },
      ]),
    );
    const { container } = renderDup();
    await screen.findAllByTestId("dedup-pair");

    const html = container.innerHTML;
    expect(html).not.toContain("99999");
    expect(html).not.toContain("98888");
    expect(html).not.toContain("a@b.com");
    expect(html).not.toContain("phone_e164");
    // A plain attraction field keeps its values.
    expect(html).toContain("Praça do Ferreira (centro)");
  });

  it("Descartar waits for the undo window, then sends one resolve with action=discard", async () => {
    renderDup();
    await screen.findAllByTestId("dedup-pair");

    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    fireEvent.click(screen.getByRole("button", { name: "Descartar candidato" }));
    await advance(100);
    // Optimistically out of the list; the next pair is selected.
    expect(screen.getAllByTestId("dedup-pair")).toHaveLength(1);
    expect(screen.getByTestId("dedup-detail")).toHaveTextContent("Salvador");
    expect(resolves).toHaveLength(0);

    await advance(6_500);
    await vi.waitFor(() => expect(resolves).toHaveLength(1));
    expect(resolves[0]).toEqual({
      id: "cand-ce-praca",
      body: { action: "discard", mar_id: "mar-ce-0142" },
    });
  });

  it("Desfazer sends nothing and brings the pair back", async () => {
    renderDup();
    await screen.findAllByTestId("dedup-pair");

    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    fireEvent.click(screen.getByRole("button", { name: "Mesclar no Mar" }));
    await advance(100);
    expect(screen.getAllByTestId("dedup-pair")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Desfazer" }));
    await advance(100);
    // Restored on the click, not at the end of the window.
    expect(screen.getAllByTestId("dedup-pair")).toHaveLength(2);
    await advance(10_000);

    expect(resolves).toHaveLength(0);
    expect(screen.getAllByTestId("dedup-pair")).toHaveLength(2);
  });

  it("renders the empty state when there are no pending pairs", async () => {
    server.use(dedupPairsEmpty());
    renderDup();

    await screen.findByTestId("dedup-empty");
    expect(screen.queryAllByTestId("dedup-pair")).toHaveLength(0);
  });
});
