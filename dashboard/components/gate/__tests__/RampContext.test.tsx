import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { RampContext } from "@/components/gate/RampContext";
import { server } from "@/mocks/server";
import {
  rampContextError,
  rampContextRed,
  rampContextSuccess,
} from "@/mocks/handlers/gate";

import { renderWithClient } from "./test-utils";

/**
 * DASH-03 gap closure: the RampContext panel fetches
 * GET /api/v1/atrativos/whatsapp/ramp-context (now a real backend endpoint).
 * These tests prove the panel renders REAL ramp/quality data on the happy path
 * (not the degraded "indisponível" fallback) and applies the RED destructive
 * treatment per UI-SPEC — offline via MSW.
 */

beforeEach(() => {
  server.resetHandlers();
});

describe("RampContext", () => {
  it("renders real ramp + quality data on the happy path (not the fallback)", async () => {
    server.use(rampContextSuccess());
    renderWithClient(<RampContext />);

    // Quality badge shows the real rating — proves data flowed, not the fallback.
    const badge = await screen.findByTestId("quality-rating");
    expect(badge).toHaveTextContent("GREEN");

    // Real cap numbers from the endpoint render (sample: remaining 120 / used 130 / cap 250).
    expect(screen.getByText("120")).toBeInTheDocument();
    expect(screen.getByText("130")).toBeInTheDocument();
    expect(screen.getByText("250")).toBeInTheDocument();

    // The degraded fallback must NOT be shown when data loads.
    expect(
      screen.queryByText(/Contexto de ramp\/qualidade indispon[ií]vel/i),
    ).not.toBeInTheDocument();
  });

  it("applies the RED destructive state + auto-pause copy", async () => {
    server.use(rampContextRed());
    renderWithClient(<RampContext />);

    const badge = await screen.findByTestId("quality-rating");
    expect(badge).toHaveTextContent("RED");
    // RED badge gets the destructive background per UI-SPEC.
    expect(badge.className).toContain("bg-destructive");

    // The auto-pause copy is announced on RED.
    expect(
      screen.getByText(/envios pausados automaticamente/i),
    ).toBeInTheDocument();

    // The destructive section border is applied.
    const section = screen.getByRole("region", {
      name: /Contexto de ramp e qualidade WhatsApp/i,
    });
    expect(section.className).toContain("border-destructive");
  });

  it("falls back to 'indisponível' only when the context fetch fails", async () => {
    server.use(rampContextError(500));
    renderWithClient(<RampContext />);

    expect(
      await screen.findByText(/Contexto de ramp\/qualidade indispon[ií]vel/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("quality-rating")).not.toBeInTheDocument();
  });
});
