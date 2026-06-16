import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ThroughputChart } from "@/components/monitor/ThroughputChart";
import { server } from "@/mocks/server";
import {
  monitorEmpty,
  monitorError,
  monitorSuccess,
  monitorUnauthorized,
} from "@/mocks/handlers/monitor";

import { renderWithClient } from "../../dlq/__tests__/test-utils";

beforeEach(() => {
  server.resetHandlers();
});

describe("ThroughputChart", () => {
  it("renders the throughput readout and the per-layer series on success", async () => {
    server.use(monitorSuccess());
    renderWithClient(<ThroughputChart />);

    expect(await screen.findByTestId("throughput-chart")).toBeInTheDocument();
    // The windowed throughput readout (assertable independent of SVG sizing).
    expect(screen.getByText("318")).toBeInTheDocument();
    expect(
      screen.getByText(/Registros processados/),
    ).toBeInTheDocument();
  });

  it("renders the loading skeleton before data arrives", () => {
    server.use(monitorSuccess());
    renderWithClient(<ThroughputChart />);
    expect(screen.getByTestId("throughput-skeleton")).toBeInTheDocument();
  });

  it("shows the empty-period chart copy when the window has no data", async () => {
    server.use(monitorEmpty());
    renderWithClient(<ThroughputChart />);
    expect(await screen.findByText("Sem dados no período")).toBeInTheDocument();
    expect(
      screen.getByText(/Ajuste a janela de tempo/),
    ).toBeInTheDocument();
  });

  it("shows the fetch-error state with a retry button", async () => {
    server.use(monitorError(500));
    renderWithClient(<ThroughputChart />);
    expect(
      await screen.findByText("Não foi possível carregar"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Tentar novamente" }),
    ).toBeInTheDocument();
  });

  it("shows the 401 session-expired state", async () => {
    server.use(monitorUnauthorized());
    renderWithClient(<ThroughputChart />);
    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });
});
