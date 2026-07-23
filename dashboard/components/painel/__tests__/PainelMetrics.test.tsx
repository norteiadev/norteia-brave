import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PainelMetrics } from "@/components/painel/PainelMetrics";
import type { EntityMetric } from "@/lib/painel-data";

const atrativo: EntityMetric = { total: 8, mar: 0, falha: 3, pct: 0 };

describe("PainelMetrics", () => {
  it("renders ONLY the Atrativos card (Destinos card removed)", () => {
    render(<PainelMetrics atrativo={atrativo} />);

    expect(screen.getByText("Atrativos")).toBeInTheDocument();
    expect(screen.queryByText("Destinos")).not.toBeInTheDocument();
    expect(screen.queryByTestId("metric-destino-total")).not.toBeInTheDocument();
  });

  it("shows the Atrativos total, sincronizados, falhas and progress", () => {
    render(<PainelMetrics atrativo={atrativo} />);

    expect(screen.getByTestId("metric-atrativo-total")).toHaveTextContent("8");
    expect(screen.getByTestId("metric-atrativo-mar")).toHaveTextContent("0");
    expect(screen.getByTestId("metric-atrativo-falha")).toHaveTextContent("3");
    expect(screen.getByTestId("metric-atrativo-pct")).toHaveTextContent("0");
  });
});
