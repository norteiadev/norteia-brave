import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

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

beforeEach(() => {
  requests.length = 0;
  server.events.on("request:start", ({ request }) => {
    requests.push({ method: request.method, url: request.url });
  });
});

afterEach(() => {
  server.events.removeAllListeners();
});

describe("PainelDuplicados", () => {
  it("renders one pair card per item with matched + diverged chips and a labeled similarity", async () => {
    server.use(dedupPairsSuccess(), dedupResolveSuccess());

    const { findAllByTestId, getAllByTestId } = renderWithClient(
      <PainelDuplicados />,
    );

    const cards = await findAllByTestId("dedup-pair");
    expect(cards).toHaveLength(sampleDedupPairs.length);

    // First pair: matched name/municipio/uf, diverged source/coordenadas.
    const matched = getAllByTestId("dedup-matched-chip");
    expect(matched.length).toBeGreaterThanOrEqual(3);
    expect(cards[0]).toHaveTextContent("name");
    expect(cards[0]).toHaveTextContent("source");

    // Similarity is rendered as a percentage (0.95 → 95%).
    const sims = getAllByTestId("dedup-similarity");
    expect(sims[0]).toHaveTextContent("95%");
  });

  it("clicking Descartar fires the real resolve PATCH with action=discard", async () => {
    server.use(dedupPairsSuccess(), dedupResolveSuccess());

    const { findAllByTestId } = renderWithClient(<PainelDuplicados />);

    const discardButtons = await findAllByTestId("dedup-discard");
    fireEvent.click(discardButtons[0]);

    await waitFor(() =>
      expect(
        requests.some(
          (r) =>
            r.method === "PATCH" &&
            r.url.includes("/api/api/v1/dedup/pairs/") &&
            r.url.includes("/resolve"),
        ),
      ).toBe(true),
    );
  });

  it("renders the empty state when there are no pending pairs", async () => {
    server.use(dedupPairsEmpty());

    const { findByTestId, queryAllByTestId } = renderWithClient(
      <PainelDuplicados />,
    );

    await findByTestId("dedup-empty");
    expect(queryAllByTestId("dedup-pair")).toHaveLength(0);
  });
});
