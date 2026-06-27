import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { PainelCusto } from "@/components/painel/PainelCusto";
import type { CostData } from "@/lib/cost-api";
import { costEmpty, costSuccess } from "@/mocks/handlers/cost";
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

describe("PainelCusto", () => {
  it("renders formatted summary totals and one bar per row, sorted desc by usd", async () => {
    // Deliberately unordered so we exercise the desc sort in the component.
    const unordered: CostData = {
      group_by: "lane",
      rows: [
        { key: "small", usd_cost: 0.5, tokens: 100, count: 5 },
        { key: "big", usd_cost: 9.0, tokens: 9000, count: 90 },
        { key: "mid", usd_cost: 3.0, tokens: 3000, count: 30 },
      ],
    };
    server.use(costSuccess(unordered));

    const { findAllByTestId, getByTestId } = renderWithClient(<PainelCusto />);

    const bars = await findAllByTestId("cost-bar");
    expect(bars).toHaveLength(3);
    // Sorted desc: big (9.0), mid (3.0), small (0.5).
    expect(bars[0]).toHaveTextContent("big");
    expect(bars[1]).toHaveTextContent("mid");
    expect(bars[2]).toHaveTextContent("small");

    // Totals: usd 12.5 → US$ 12,5000 ; tokens 12.100 ; calls 125.
    expect(getByTestId("cost-total-usd")).toHaveTextContent("US$ 12,5000");
    expect(getByTestId("cost-total-tokens")).toHaveTextContent("12.100");
    expect(getByTestId("cost-total-calls")).toHaveTextContent("125");
  });

  it("switching group to model refetches and shows the model rows", async () => {
    server.use(costSuccess());

    const { findAllByTestId, getByTestId } = renderWithClient(<PainelCusto />);

    // Default lane view first.
    const laneBars = await findAllByTestId("cost-bar");
    expect(laneBars[0]).toHaveTextContent("destinos");

    fireEvent.click(getByTestId("cost-group-model"));

    // A model-by request went out and the model keys now render.
    await waitFor(() =>
      expect(
        requests.some(
          (r) => r.method === "GET" && r.url.includes("group_by=model"),
        ),
      ).toBe(true),
    );
    await waitFor(() => {
      const bars = getByTestId("cost-bars");
      expect(bars).toHaveTextContent("deepseek/deepseek-chat:nitro");
      expect(bars).toHaveTextContent("anthropic/claude-sonnet-4.5");
    });
  });

  it("switching window changes the active segment", async () => {
    server.use(costSuccess());

    const { findByTestId, getByTestId } = renderWithClient(<PainelCusto />);

    // Default window is 7d (index 1).
    const sevenDay = await findByTestId("cost-window-7d");
    expect(sevenDay).toHaveAttribute("data-active", "true");
    expect(getByTestId("cost-window-30d")).toHaveAttribute(
      "data-active",
      "false",
    );

    fireEvent.click(getByTestId("cost-window-30d"));

    await waitFor(() =>
      expect(getByTestId("cost-window-30d")).toHaveAttribute(
        "data-active",
        "true",
      ),
    );
    expect(getByTestId("cost-window-7d")).toHaveAttribute("data-active", "false");
  });

  it("shows the empty state and zeroed totals when there are no rows", async () => {
    server.use(costEmpty());

    const { findByText, getByTestId } = renderWithClient(<PainelCusto />);

    await findByText("Sem dados na janela");
    expect(getByTestId("cost-total-usd")).toHaveTextContent("US$ 0,0000");
    expect(getByTestId("cost-total-tokens")).toHaveTextContent("0");
    expect(getByTestId("cost-total-calls")).toHaveTextContent("0");
  });
});
