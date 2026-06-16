import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { CostSummary } from "@/components/cost/CostSummary";
import { server } from "@/mocks/server";
import {
  costEmpty,
  costError,
  costSuccess,
  costUnauthorized,
} from "@/mocks/handlers/cost";

import { renderWithClient } from "../../dlq/__tests__/test-utils";

beforeEach(() => {
  server.resetHandlers();
});

describe("CostSummary", () => {
  it("renders the USD / tokens / calls totals in mono on success", async () => {
    server.use(costSuccess());
    renderWithClient(<CostSummary />);

    expect(await screen.findByTestId("cost-summary")).toBeInTheDocument();
    // USD total (lane rows): 4.215 + 2.8307 + 0.612 = 7.6577 → US$ 7,6577.
    const usd = screen.getByText("US$ 7,6577");
    expect(usd).toBeInTheDocument();
    expect(usd).toHaveClass("font-mono");
    // tokens: 1,280,400 + 940,120 + 210,300 = 2,430,820 (pt-BR grouping).
    expect(screen.getByText("2.430.820")).toBeInTheDocument();
    // calls: 1820 + 1310 + 290 = 3420.
    expect(screen.getByText("3.420")).toBeInTheDocument();
    expect(screen.getByText("Gasto total (USD)")).toBeInTheDocument();
  });

  it("renders the loading skeleton before data arrives", () => {
    server.use(costSuccess());
    renderWithClient(<CostSummary />);
    expect(screen.getByTestId("cost-summary-skeleton")).toBeInTheDocument();
  });

  it("shows the empty-period copy when there are no rows", async () => {
    server.use(costEmpty());
    renderWithClient(<CostSummary />);
    expect(await screen.findByText("Sem dados no período")).toBeInTheDocument();
  });

  it("shows the fetch-error state with a retry button", async () => {
    server.use(costError(500));
    renderWithClient(<CostSummary />);
    expect(
      await screen.findByText("Não foi possível carregar"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Tentar novamente" }),
    ).toBeInTheDocument();
  });

  it("shows the 401 session-expired state", async () => {
    server.use(costUnauthorized());
    renderWithClient(<CostSummary />);
    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });
});
