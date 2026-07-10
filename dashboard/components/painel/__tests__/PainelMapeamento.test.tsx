import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PainelMapeamento } from "@/components/painel/PainelMapeamento";
import { renderWithClient } from "@/components/cms/__tests__/test-utils";

describe("PainelMapeamento", () => {
  it("renders the default TripAdvisor rows", () => {
    renderWithClient(<PainelMapeamento />);
    // TripAdvisor is the default (and sole surfaced collection) source.
    expect(screen.getByText("numReviews")).toBeInTheDocument();
    expect(screen.getByText("locationId")).toBeInTheDocument();
    // 9 tripadvisor mapping entries → 9 rows.
    expect(screen.getAllByTestId("map-row")).toHaveLength(9);
  });

  it("updates the preview panel when a select changes", () => {
    renderWithClient(<PainelMapeamento />);
    // TripAdvisor maps 'name' by default → the preview carries a 'name' row.
    const nameBefore = screen
      .getAllByTestId("map-preview-row")
      .find((el) => within(el).queryByText("name"));
    expect(nameBefore).toBeTruthy();

    // Route the first row (name) to '—' (ignore) → the 'name' canonical drops.
    const firstSelect = screen.getAllByTestId("map-select")[0];
    fireEvent.change(firstSelect, { target: { value: "—" } });

    const nameAfter = screen
      .getAllByTestId("map-preview-row")
      .find((el) => within(el).queryByText("name"));
    expect(nameAfter).toBeFalsy();
  });

  it("swaps rows when a different source is selected", () => {
    renderWithClient(<PainelMapeamento />);
    // Default TripAdvisor exposes numReviews; Google Places does not.
    expect(screen.getByText("numReviews")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("map-source-google_places"));

    // 'userRatingCount' is a google_places-only source field.
    expect(screen.getByText("userRatingCount")).toBeInTheDocument();
    expect(screen.queryByText("numReviews")).not.toBeInTheDocument();
  });
});
