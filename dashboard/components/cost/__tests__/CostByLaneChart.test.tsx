import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { CostByLaneChart } from "@/components/cost/CostByLaneChart";
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

describe("CostByLaneChart", () => {
  it("renders the lane series and the total-spend readout on success", async () => {
    server.use(costSuccess());
    renderWithClient(<CostByLaneChart />);

    expect(await screen.findByTestId("cost-by-lane")).toBeInTheDocument();
    // Total USD headline: 4.215 + 2.8307 + 0.612 = 7.6577 → US$ 7,6577 (pt-BR).
    expect(screen.getByText("US$ 7,6577")).toBeInTheDocument();
    expect(screen.getByText(/Gasto total/)).toBeInTheDocument();
  });

  it("renders the loading skeleton before data arrives", () => {
    server.use(costSuccess());
    renderWithClient(<CostByLaneChart />);
    expect(screen.getByTestId("cost-by-lane-skeleton")).toBeInTheDocument();
  });

  it("shows the empty-period copy when the window has no data", async () => {
    server.use(costEmpty());
    renderWithClient(<CostByLaneChart />);
    expect(await screen.findByText("Sem dados no período")).toBeInTheDocument();
    expect(screen.getByText(/Ajuste a janela de tempo/)).toBeInTheDocument();
  });

  it("shows the fetch-error state with a retry button", async () => {
    server.use(costError(500));
    renderWithClient(<CostByLaneChart />);
    expect(
      await screen.findByText("Não foi possível carregar"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Tentar novamente" }),
    ).toBeInTheDocument();
  });

  it("shows the 401 session-expired state", async () => {
    server.use(costUnauthorized());
    renderWithClient(<CostByLaneChart />);
    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });
});
