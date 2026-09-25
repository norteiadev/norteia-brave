import { waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PainelMonitor } from "@/components/painel/PainelMonitor";
import { engineStatus } from "@/mocks/handlers/engine";
import { funnelsEmpty, funnelsSuccess } from "@/mocks/handlers/funnels";
import { monitorEmpty, monitorSuccess } from "@/mocks/handlers/monitor";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

describe("PainelMonitor", () => {
  it("renders monitor volume tiles and the funnel bars", async () => {
    // Both endpoints must be mocked — onUnhandledRequest:"error".
    server.use(monitorSuccess(), funnelsSuccess(), engineStatus());

    const { getByTestId, findAllByTestId } = renderWithClient(
      <PainelMonitor />,
    );

    // Tiles always render (placeholder "—" until the query settles) — wait for content.
    await waitFor(() =>
      expect(getByTestId("monitor-mar")).toHaveTextContent("910"),
    );
    expect(getByTestId("monitor-throughput")).toHaveTextContent("318");

    // ingested → in_progress → mar → dlq → descarte
    const bars = await findAllByTestId("funnel-bar");
    expect(bars).toHaveLength(5);
    const descarte = bars.find((b) => b.getAttribute("data-stage") === "descarte");
    expect(descarte).toHaveTextContent("130");
  });

  it("shows the empty funnel state when there are no records", async () => {
    server.use(monitorEmpty(), funnelsEmpty(), engineStatus());

    const { findByTestId, queryAllByTestId } = renderWithClient(
      <PainelMonitor />,
    );

    await findByTestId("funnel-empty");
    expect(queryAllByTestId("funnel-bar")).toHaveLength(0);
    expect(queryAllByTestId("monitor-beat-error")).toHaveLength(0);
  });

  it("shows one alert per failing beat task", async () => {
    server.use(
      monitorSuccess(),
      funnelsSuccess(),
      engineStatus({
        beat_errors: [
          {
            task: "brave.prune_record_events",
            at: "2026-09-25T04:00:00+00:00",
            error_type: "OperationalError",
          },
          {
            task: "brave.ta_keepalive",
            at: "2026-09-25T04:10:00+00:00",
            error_type: "ConnectError",
          },
        ],
      }),
    );

    const { findAllByTestId } = renderWithClient(<PainelMonitor />);

    const alerts = await findAllByTestId("monitor-beat-error");
    expect(alerts).toHaveLength(2);
    expect(alerts[0]).toHaveTextContent("brave.prune_record_events");
    expect(alerts[0]).toHaveTextContent("OperationalError");
    expect(alerts[1]).toHaveTextContent("brave.ta_keepalive");
  });
});
