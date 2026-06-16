import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { FunnelChart } from "@/components/funnels/FunnelChart";
import { server } from "@/mocks/server";
import {
  funnelsEmpty,
  funnelsError,
  funnelsSuccess,
  funnelsUnauthorized,
} from "@/mocks/handlers/funnels";

import { renderWithClient } from "../../dlq/__tests__/test-utils";

beforeEach(() => {
  server.resetHandlers();
});

describe("FunnelChart", () => {
  it("renders the stage series on success", async () => {
    server.use(funnelsSuccess());
    renderWithClient(<FunnelChart />);

    expect(await screen.findByTestId("funnel-chart")).toBeInTheDocument();
    // Stage labels from FUNNEL_STAGES (the X-axis ticks / chart legend body).
    expect(await screen.findByText("ingerido")).toBeInTheDocument();
    expect(screen.getByText("em progresso")).toBeInTheDocument();
    expect(screen.getByText("mar")).toBeInTheDocument();
    expect(screen.getByText("dlq")).toBeInTheDocument();
    expect(screen.getByText("descarte")).toBeInTheDocument();
    // The lane + UF filter controls are present.
    expect(
      screen.getByRole("button", { name: "Destinos" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "BA" })).toBeInTheDocument();
  });

  it("renders the loading skeleton before data arrives", () => {
    server.use(funnelsSuccess());
    renderWithClient(<FunnelChart />);
    expect(screen.getByTestId("funnel-chart-skeleton")).toBeInTheDocument();
  });

  it("shows the empty-period copy when there is no funnel data", async () => {
    server.use(funnelsEmpty());
    renderWithClient(<FunnelChart />);
    expect(await screen.findByText("Sem dados no período")).toBeInTheDocument();
    expect(screen.getByText(/Ajuste a janela de tempo/)).toBeInTheDocument();
  });

  it("shows the fetch-error state with a retry button", async () => {
    server.use(funnelsError(500));
    renderWithClient(<FunnelChart />);
    expect(
      await screen.findByText("Não foi possível carregar"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Tentar novamente" }),
    ).toBeInTheDocument();
  });

  it("shows the 401 session-expired state", async () => {
    server.use(funnelsUnauthorized());
    renderWithClient(<FunnelChart />);
    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });
});
