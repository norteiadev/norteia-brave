import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PainelFilters } from "@/components/painel/PainelFilters";

function setup(overrides: {
  uf?: string | null;
  onUfChange?: (uf: string | null) => void;
} = {}) {
  const onUfChange = overrides.onUfChange ?? vi.fn();
  render(<PainelFilters uf={overrides.uf ?? null} onUfChange={onUfChange} />);
  return { onUfChange };
}

describe("PainelFilters — type control removed", () => {
  it("no longer renders the type segmented control", () => {
    setup();
    expect(screen.queryByTestId("filter-type-all")).not.toBeInTheDocument();
    expect(screen.queryByTestId("filter-type-destino")).not.toBeInTheDocument();
    expect(screen.queryByTestId("filter-type-atrativo")).not.toBeInTheDocument();
  });
});

describe("PainelFilters — UF-scope dropdown (single-select)", () => {
  it("shows 'Todas' in the trigger when no UF is selected", () => {
    setup({ uf: null });
    expect(screen.getByTestId("filter-uf-trigger")).toHaveTextContent("Todas");
  });

  it("shows the selected UF code in the trigger", () => {
    setup({ uf: "DF" });
    expect(screen.getByTestId("filter-uf-trigger")).toHaveTextContent("DF");
  });

  it("selects a UF on click from an empty scope", () => {
    const { onUfChange } = setup({ uf: null });

    fireEvent.click(screen.getByTestId("filter-uf-trigger"));
    fireEvent.click(screen.getByTestId("filter-uf-BA"));
    expect(onUfChange).toHaveBeenCalledWith("BA");
  });

  it("clicking the active UF again clears the scope to null", () => {
    const { onUfChange } = setup({ uf: "BA" });

    fireEvent.click(screen.getByTestId("filter-uf-trigger"));
    fireEvent.click(screen.getByTestId("filter-uf-BA"));
    expect(onUfChange).toHaveBeenCalledWith(null);
  });

  it("picking a different UF replaces the current one (single-select)", () => {
    const { onUfChange } = setup({ uf: "BA" });

    fireEvent.click(screen.getByTestId("filter-uf-trigger"));
    fireEvent.click(screen.getByTestId("filter-uf-SP"));
    expect(onUfChange).toHaveBeenCalledWith("SP");
  });

  it("clears the scope via 'Todas'", () => {
    const { onUfChange } = setup({ uf: "BA" });

    fireEvent.click(screen.getByTestId("filter-uf-trigger"));
    fireEvent.click(screen.getByTestId("filter-uf-clear"));
    expect(onUfChange).toHaveBeenCalledWith(null);
  });
});
