import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import ProcessoPage from "@/app/processo/page";
import { server } from "@/mocks/server";
import {
  dlqListSuccess,
  sampleListItems,
} from "@/mocks/handlers/dlq";
import {
  gateListSuccess,
  sampleGateItems,
} from "@/mocks/handlers/gate";
import {
  failuresSuccess,
  workersBrokerDown,
  workersSuccess,
} from "@/mocks/handlers/workers";

import { renderWithClient } from "../../../components/dlq/__tests__/test-utils";

/**
 * /processo page tests (D-05, D-06).
 *
 * Each test mounts all 4 required handlers (workers + failures + dlq + gate)
 * because `onUnhandledRequest: "error"` is set globally — every request from
 * the page must be mocked.
 */

// Re-use the global afterEach from vitest.setup.ts (cleanup + resetHandlers).

describe("/processo page", () => {
  beforeEach(() => {
    server.resetHandlers();
  });

  it("WorkerBoard renders worker hostname from sampleWorkers", async () => {
    server.use(
      workersSuccess(),
      failuresSuccess(),
      dlqListSuccess(),
      gateListSuccess(),
    );

    renderWithClient(<ProcessoPage />);

    // Worker hostname "celery@worker-1" should render as "worker-1" (prefix stripped).
    expect(await screen.findByText("worker-1")).toBeInTheDocument();
    // Status badge
    expect(screen.getByText("UP")).toBeInTheDocument();
  });

  it("WorkerBoard renders 'Broker indisponível' banner when broker is down", async () => {
    server.use(
      workersBrokerDown(),
      failuresSuccess(),
      dlqListSuccess(),
      gateListSuccess(),
    );

    renderWithClient(<ProcessoPage />);

    expect(
      await screen.findByText(
        "Broker indisponível — nenhum worker respondeu",
      ),
    ).toBeInTheDocument();
  });

  it("FailuresPanel renders task_name from sampleFailures", async () => {
    server.use(
      workersSuccess(),
      failuresSuccess(),
      dlqListSuccess(),
      gateListSuccess(),
    );

    renderWithClient(<ProcessoPage />);

    // Wait for failures panel to load — sampleFailures has "brave.process_nascente"
    // Use getAllByText because two failure items share the same task_name.
    await waitFor(() => {
      const items = screen.getAllByText("brave.process_nascente");
      expect(items.length).toBeGreaterThan(0);
    });
  });

  it("human-pending tiles render DLQ count and gate count", async () => {
    server.use(
      workersSuccess(),
      failuresSuccess(),
      dlqListSuccess(sampleListItems),
      gateListSuccess(sampleGateItems),
    );

    renderWithClient(<ProcessoPage />);

    // Labels appear immediately
    expect(await screen.findByText("DLQ pendente")).toBeInTheDocument();
    expect(screen.getByText("Gate WhatsApp")).toBeInTheDocument();

    // DLQ tile count: sampleListItems has 3 items → count "3"
    await waitFor(() => {
      const dlqTile = screen.getByTestId("tile-dlq-pending");
      expect(dlqTile.textContent).toContain("3");
    });

    // Gate tile count: sampleGateItems has 2 items → count "2"
    await waitFor(() => {
      const gateTile = screen.getByTestId("tile-gate-pending");
      expect(gateTile.textContent).toContain("2");
    });
  });

  it("WR-06: DLQ tile renders '500+' when the list is capped at the fetch limit", async () => {
    // 500 items === the page cap → true count is unknown and at-least 500.
    const cappedDlq = Array.from({ length: 500 }, (_, i) => ({
      ...sampleListItems[0],
      id: `cap-${i}`,
    }));

    server.use(
      workersSuccess(),
      failuresSuccess(),
      dlqListSuccess(cappedDlq),
      gateListSuccess(sampleGateItems),
    );

    renderWithClient(<ProcessoPage />);

    await waitFor(() => {
      const dlqTile = screen.getByTestId("tile-dlq-pending");
      expect(dlqTile.textContent).toContain("500+");
    });

    // Gate tile (2 items, well under cap) still shows an exact count.
    await waitFor(() => {
      const gateTile = screen.getByTestId("tile-gate-pending");
      expect(gateTile.textContent).toContain("2");
      expect(gateTile.textContent).not.toContain("+");
    });
  });

  it("renders the stage funnel section header", async () => {
    server.use(
      workersSuccess(),
      failuresSuccess(),
      dlqListSuccess(),
      gateListSuccess(),
    );

    renderWithClient(<ProcessoPage />);

    expect(
      await screen.findByText("Funil Atrativos por Sub-Estado"),
    ).toBeInTheDocument();
  });

  it("renders the /processo page header", async () => {
    server.use(
      workersSuccess(),
      failuresSuccess(),
      dlqListSuccess(),
      gateListSuccess(),
    );

    renderWithClient(<ProcessoPage />);

    // Wait for something to render, then check header
    await waitFor(() =>
      expect(screen.getByRole("main")).toBeInTheDocument(),
    );

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Processo Brave",
    );
  });
});
