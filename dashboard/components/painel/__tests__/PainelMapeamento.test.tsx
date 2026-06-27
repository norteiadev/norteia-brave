import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PainelMapeamento } from "@/components/painel/PainelMapeamento";
import { renderWithClient } from "@/components/cms/__tests__/test-utils";

describe("PainelMapeamento", () => {
  it("renders the default mTur rows", () => {
    renderWithClient(<PainelMapeamento />);
    // NO_MUNICIPIO maps to both name + municipality → appears twice.
    expect(screen.getAllByText("NO_MUNICIPIO")).toHaveLength(2);
    expect(screen.getByText("DS_CATEGORIA")).toBeInTheDocument();
    // 8 mtur mapping entries → 8 rows.
    expect(screen.getAllByTestId("map-row")).toHaveLength(8);
  });

  it("updates the preview panel when a select changes", () => {
    renderWithClient(<PainelMapeamento />);
    // mtur has no 'rating' canonical by default → no preview row keyed 'rating'.
    const before = screen
      .getAllByTestId("map-preview-row")
      .map((el) => within(el).getAllByText(/.+/)[0].textContent);
    expect(before).not.toContain("rating");

    // Route the first row (NO_MUNICIPIO) to 'rating'.
    const firstSelect = screen.getAllByTestId("map-select")[0];
    fireEvent.change(firstSelect, { target: { value: "rating" } });

    const ratingRow = screen
      .getAllByTestId("map-preview-row")
      .find((el) => within(el).queryByText("rating"));
    expect(ratingRow).toBeTruthy();
  });

  it("swaps rows when a different source is selected", () => {
    renderWithClient(<PainelMapeamento />);
    expect(screen.queryByText("numReviews")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("map-source-tripadvisor"));

    // 'numReviews' is a tripadvisor-only source field.
    expect(screen.getByText("numReviews")).toBeInTheDocument();
    expect(screen.queryByText("NO_MUNICIPIO")).not.toBeInTheDocument();
  });
});
