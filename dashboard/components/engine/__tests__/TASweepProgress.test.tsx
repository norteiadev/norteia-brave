import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { TASweepProgress } from "@/components/engine/TASweepProgress";
import { server } from "@/mocks/server";
import {
  taSweepProgress,
  taSweepUnauthorized,
} from "@/mocks/handlers/ta-sweep";

import { renderWithClient } from "../../cms/__tests__/test-utils";

beforeEach(() => {
  server.resetHandlers();
});

describe("TASweepProgress", () => {
  it("renders the pages_done/pages_total (5/334) progress bar", async () => {
    server.use(taSweepProgress({ pages_done: 5, pages_total: 334 }));
    renderWithClient(<TASweepProgress />);

    const pages = await screen.findByTestId("ta-sweep-pages");
    expect(pages).toHaveTextContent("5/334");
    // 5/334 ≈ 1% — assert the computed percentage renders.
    expect(screen.getByTestId("ta-sweep-pct")).toHaveTextContent("1%");
  });

  it("renders attractions ingested, current offset and error count tiles", async () => {
    server.use(
      taSweepProgress({
        attractions_ingested: 150,
        current_offset: 120,
        error_count: 2,
      }),
    );
    renderWithClient(<TASweepProgress />);

    expect(await screen.findByTestId("ta-sweep-attractions")).toHaveTextContent(
      "150",
    );
    expect(screen.getByTestId("ta-sweep-offset")).toHaveTextContent("120");
    expect(screen.getByTestId("ta-sweep-errors")).toHaveTextContent("2");
  });

  it("shows the 'Varrendo' pill while running", async () => {
    server.use(taSweepProgress({ state: "running" }));
    renderWithClient(<TASweepProgress />);

    await waitFor(() =>
      expect(screen.getByTestId("ta-sweep-state")).toHaveTextContent("Varrendo"),
    );
  });

  it("shows the 'Concluído' pill when the sweep is done", async () => {
    server.use(
      taSweepProgress({ state: "done", pages_done: 334, pages_total: 334 }),
    );
    renderWithClient(<TASweepProgress />);

    await waitFor(() =>
      expect(screen.getByTestId("ta-sweep-state")).toHaveTextContent("Concluído"),
    );
    // Full sweep → 100%.
    expect(screen.getByTestId("ta-sweep-pct")).toHaveTextContent("100%");
  });

  it("shows the 'Precisa bootstrap' pill when stopped_needs_bootstrap", async () => {
    server.use(taSweepProgress({ state: "stopped_needs_bootstrap" }));
    renderWithClient(<TASweepProgress />);

    await waitFor(() =>
      expect(screen.getByTestId("ta-sweep-state")).toHaveTextContent(
        "Precisa bootstrap",
      ),
    );
  });

  it("surfaces 401 without throwing (session-expired) — idle fallback", async () => {
    server.use(taSweepUnauthorized());
    renderWithClient(<TASweepProgress />);

    // The panel shell renders; no progress data → idle pill, no crash.
    const panel = await screen.findByTestId("ta-sweep-progress");
    expect(panel).toBeInTheDocument();
    expect(screen.getByTestId("ta-sweep-state")).toHaveTextContent("Parado");
    // No counts/bar render without data.
    expect(screen.queryByTestId("ta-sweep-counts")).toBeNull();
    expect(screen.queryByTestId("ta-sweep-bar")).toBeNull();
  });
});
