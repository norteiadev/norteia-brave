import { waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PainelMonitor } from "@/components/painel/PainelMonitor";
import { funnelsEmpty, funnelsSuccess } from "@/mocks/handlers/funnels";
import { monitorEmpty, monitorSuccess } from "@/mocks/handlers/monitor";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

describe("PainelMonitor", () => {
  it("renders monitor volume tiles and the funnel bars", async () => {
    // Both endpoints must be mocked — onUnhandledRequest:"error".
    server.use(monitorSuccess(), funnelsSuccess());

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
    server.use(monitorEmpty(), funnelsEmpty());

    const { findByTestId, queryAllByTestId } = renderWithClient(
      <PainelMonitor />,
    );

    await findByTestId("funnel-empty");
    expect(queryAllByTestId("funnel-bar")).toHaveLength(0);
  });
});
