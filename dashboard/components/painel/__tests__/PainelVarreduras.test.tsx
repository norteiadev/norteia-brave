import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { PainelVarreduras } from "@/components/painel/PainelVarreduras";
import {
  runsListEmpty,
  runsListSuccess,
  runsReprocessSuccess,
  sampleRuns,
} from "@/mocks/handlers/runs";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

const requests: { method: string; url: string }[] = [];

beforeEach(() => {
  requests.length = 0;
  server.events.on("request:start", ({ request }) => {
    requests.push({ method: request.method, url: request.url });
  });
});

afterEach(() => {
  server.events.removeAllListeners();
});

describe("PainelVarreduras", () => {
  it("renders one row per run with all columns + a colored status pill", async () => {
    server.use(runsListSuccess(), runsReprocessSuccess());

    const { findAllByTestId, getAllByTestId } = renderWithClient(
      <PainelVarreduras />,
    );

    const rows = await findAllByTestId("runs-row");
    expect(rows).toHaveLength(sampleRuns.length);

    // Columns: source / depth label / synced / failed are present.
    expect(rows[0]).toHaveTextContent("mtur");
    expect(rows[0]).toHaveTextContent("Nascente → Rio → Mar");
    expect(rows[0]).toHaveTextContent("138"); // synced
    expect(rows[0]).toHaveTextContent("CE"); // uf

    // Status pills mirror each run's status.
    const pills = getAllByTestId("runs-status-pill");
    expect(pills).toHaveLength(sampleRuns.length);
    expect(pills[0]).toHaveAttribute("data-status", "concluido");
    expect(pills[0]).toHaveTextContent("Concluído");
    expect(pills[1]).toHaveAttribute("data-status", "parcial");
    expect(pills[2]).toHaveAttribute("data-status", "falha");
  });

  it("renders 7-day stat cards summarizing the recent runs", async () => {
    server.use(runsListSuccess(), runsReprocessSuccess());

    const { findAllByTestId, getByTestId } = renderWithClient(
      <PainelVarreduras />,
    );

    // Wait for the runs to load before asserting the derived stat cards.
    await findAllByTestId("runs-row");

    // All three sample runs started within the last 7 days.
    await waitFor(() =>
      expect(getByTestId("runs-stat-total")).toHaveTextContent("3"),
    );
    // synced = 138 + 71 + 0 = 209 ; failed = 4 + 17 + 20 = 41
    expect(getByTestId("runs-stat-synced")).toHaveTextContent("209");
    expect(getByTestId("runs-stat-failed")).toHaveTextContent("41");
  });

  it("clicking ↺ Falhas fires the real reprocess PATCH for that run", async () => {
    server.use(runsListSuccess(), runsReprocessSuccess());

    const { findAllByTestId } = renderWithClient(<PainelVarreduras />);

    const buttons = await findAllByTestId("runs-reprocess");
    fireEvent.click(buttons[0]);

    await waitFor(() =>
      expect(
        requests.some(
          (r) =>
            r.method === "PATCH" &&
            r.url.includes("/api/api/v1/runs/") &&
            r.url.includes("/reprocess"),
        ),
      ).toBe(true),
    );
  });

  it("renders the empty state when there are no runs", async () => {
    server.use(runsListEmpty());

    const { findByTestId, queryAllByTestId } = renderWithClient(
      <PainelVarreduras />,
    );

    await findByTestId("runs-empty");
    expect(queryAllByTestId("runs-row")).toHaveLength(0);
  });
});
