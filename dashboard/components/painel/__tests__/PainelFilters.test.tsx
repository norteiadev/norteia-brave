import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PainelFilters } from "@/components/painel/PainelFilters";
import type { TypeFilter } from "@/lib/painel-data";

function setup(overrides: {
  type?: TypeFilter;
  ufs?: string[];
  onTypeChange?: (t: TypeFilter) => void;
  onUfsChange?: (ufs: string[]) => void;
} = {}) {
  const onTypeChange = overrides.onTypeChange ?? vi.fn();
  const onUfsChange = overrides.onUfsChange ?? vi.fn();
  render(
    <PainelFilters
      type={overrides.type ?? "all"}
      onTypeChange={onTypeChange}
      ufs={overrides.ufs ?? []}
      onUfsChange={onUfsChange}
    />,
  );
  return { onTypeChange, onUfsChange };
}

describe("PainelFilters — type segmented control", () => {
  it("reports the selected type and marks the active button", () => {
    const { onTypeChange } = setup({ type: "all" });

    expect(screen.getByTestId("filter-type-all")).toHaveAttribute(
      "data-active",
      "true",
    );

    fireEvent.click(screen.getByTestId("filter-type-destino"));
    expect(onTypeChange).toHaveBeenCalledWith("destino");
  });
});

describe("PainelFilters — UF-scope dropdown", () => {
  it("shows 'Todas' in the trigger when no UF is selected", () => {
    setup({ ufs: [] });
    expect(screen.getByTestId("filter-uf-trigger")).toHaveTextContent("Todas");
  });

  it("appends a UF on toggle from an empty scope", () => {
    const { onUfsChange } = setup({ ufs: [] });

    fireEvent.click(screen.getByTestId("filter-uf-trigger"));
    fireEvent.click(screen.getByTestId("filter-uf-BA"));
    expect(onUfsChange).toHaveBeenCalledWith(["BA"]);
  });

  it("removes a UF on toggle when already selected", () => {
    const { onUfsChange } = setup({ ufs: ["BA", "SP"] });

    fireEvent.click(screen.getByTestId("filter-uf-trigger"));
    fireEvent.click(screen.getByTestId("filter-uf-BA"));
    expect(onUfsChange).toHaveBeenCalledWith(["SP"]);
  });

  it("clears the scope via 'Todas'", () => {
    const { onUfsChange } = setup({ ufs: ["BA"] });

    fireEvent.click(screen.getByTestId("filter-uf-trigger"));
    fireEvent.click(screen.getByTestId("filter-uf-clear"));
    expect(onUfsChange).toHaveBeenCalledWith([]);
  });
});
