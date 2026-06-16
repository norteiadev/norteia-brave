import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { AlertsPanel } from "@/components/monitor/AlertsPanel";
import { MonitorTiles } from "@/components/monitor/MonitorTiles";
import { server } from "@/mocks/server";
import {
  monitorEmpty,
  monitorError,
  monitorSuccess,
  monitorUnauthorized,
  sampleMonitorAlerting,
} from "@/mocks/handlers/monitor";

import { renderWithClient } from "../../dlq/__tests__/test-utils";

beforeEach(() => {
  server.resetHandlers();
});

describe("MonitorTiles", () => {
  it("renders per-layer volume numerals and the audit-derived rate captions", async () => {
    server.use(monitorSuccess());
    renderWithClient(<MonitorTiles />);

    // Display-size volume numerals (pt-BR thousands separator).
    expect(await screen.findByText("1.280")).toBeInTheDocument(); // nascente
    expect(screen.getByText("910")).toBeInTheDocument(); // mar
    expect(screen.getByText("73")).toBeInTheDocument(); // dlq
    expect(screen.getByText("318")).toBeInTheDocument(); // throughput

    // AuditLog-derived rate captions (DASH-02 audit coverage).
    expect(screen.getByText("Aprovação")).toBeInTheDocument();
    expect(screen.getByText("60%")).toBeInTheDocument(); // dlq_validated 0.6
    expect(screen.getByText("Rejeição")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument(); // dlq_rejected 0.25
  });

  it("renders the loading skeleton before data arrives", async () => {
    server.use(monitorSuccess());
    renderWithClient(<MonitorTiles />);
    expect(screen.getByTestId("monitor-tiles-skeleton")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("monitor-tiles")).toBeInTheDocument(),
    );
  });

  it("shows the empty-period copy when the window has no data", async () => {
    server.use(monitorEmpty());
    renderWithClient(<MonitorTiles />);
    expect(await screen.findByText("Sem dados no período")).toBeInTheDocument();
  });

  it("shows the fetch-error state with a retry button", async () => {
    server.use(monitorError(500));
    renderWithClient(<MonitorTiles />);
    expect(
      await screen.findByText("Não foi possível carregar"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Tentar novamente" }),
    ).toBeInTheDocument();
  });

  it("shows the 401 session-expired state", async () => {
    server.use(monitorUnauthorized());
    renderWithClient(<MonitorTiles />);
    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });
});

describe("AlertsPanel", () => {
  it("renders a calm no-failures state when alerts are clear", async () => {
    server.use(monitorSuccess());
    renderWithClient(<AlertsPanel />);
    expect(await screen.findByTestId("alerts-ok")).toBeInTheDocument();
    expect(screen.getByText("Sem falhas no período")).toBeInTheDocument();
  });

  it("turns destructive when PoisonQuarantine failures > 0 or quality is RED", async () => {
    server.use(monitorSuccess(sampleMonitorAlerting));
    renderWithClient(<AlertsPanel />);

    const panel = await screen.findByTestId("alerts-failure");
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveAttribute("role", "alert");
    expect(panel.className).toContain("border-destructive");
    expect(screen.getByText("4")).toBeInTheDocument(); // failure count
    expect(
      screen.getByText("Qualidade WhatsApp RED — envios pausados"),
    ).toBeInTheDocument();
  });

  it("shows the 401 state", async () => {
    server.use(monitorUnauthorized());
    renderWithClient(<AlertsPanel />);
    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });
});
