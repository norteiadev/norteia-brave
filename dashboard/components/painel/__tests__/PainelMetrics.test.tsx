import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PainelMetrics } from "@/components/painel/PainelMetrics";
import type { EntityMetric } from "@/lib/painel-data";

const destino: EntityMetric = { total: 12, mar: 5, falha: 2, pct: 42 };
const atrativo: EntityMetric = { total: 8, mar: 0, falha: 3, pct: 0 };

describe("PainelMetrics", () => {
  it("renders Destinos + Atrativos cards from EntityMetric props", () => {
    render(<PainelMetrics destino={destino} atrativo={atrativo} />);

    expect(screen.getByText("Destinos")).toBeInTheDocument();
    expect(screen.getByText("Atrativos")).toBeInTheDocument();
  });

  it("shows total, sincronizados, falhas and progress for Destinos", () => {
    render(<PainelMetrics destino={destino} atrativo={atrativo} />);

    expect(screen.getByTestId("metric-destino-total")).toHaveTextContent("12");
    expect(screen.getByTestId("metric-destino-mar")).toHaveTextContent("5");
    expect(screen.getByTestId("metric-destino-falha")).toHaveTextContent("2");
    expect(screen.getByTestId("metric-destino-pct")).toHaveTextContent("42");
  });

  it("shows the Atrativos metrics (including a zero progress)", () => {
    render(<PainelMetrics destino={destino} atrativo={atrativo} />);

    expect(screen.getByTestId("metric-atrativo-total")).toHaveTextContent("8");
    expect(screen.getByTestId("metric-atrativo-mar")).toHaveTextContent("0");
    expect(screen.getByTestId("metric-atrativo-falha")).toHaveTextContent("3");
    expect(screen.getByTestId("metric-atrativo-pct")).toHaveTextContent("0");
  });
});
