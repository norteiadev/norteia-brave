import { describe, expect, it } from "vitest";

import { PainelLogsView } from "@/components/painel/PainelLogsView";
import { logsLines } from "@/mocks/handlers/logs";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

describe("PainelLogsView", () => {
  it("renders the inline log tail without the slide-over chrome", async () => {
    server.use(logsLines());

    const { findByTestId, findAllByTestId, queryByTestId } = renderWithClient(
      <PainelLogsView />,
    );

    await findByTestId("painel-logs-inline");
    const lines = await findAllByTestId("log-line");
    expect(lines.length).toBe(2);

    // The inline variant drops the fixed overlay + slide-over panel.
    expect(queryByTestId("painel-logs-overlay")).toBeNull();
    expect(queryByTestId("painel-logs-panel")).toBeNull();
  });

  it("surfaces TripAdvisor as the sole active log source", async () => {
    server.use(logsLines());

    const { getByTestId, queryByTestId, findByTestId } = renderWithClient(
      <PainelLogsView />,
    );
    await findByTestId("painel-logs-inline");

    // TripAdvisor is the only surfaced lane and is active by default; the retired
    // mtur/default lane is no longer offered as a selectable log source.
    const taBtn = getByTestId("logs-source-tripadvisor");
    expect(taBtn.getAttribute("data-active")).toBe("true");
    expect(queryByTestId("logs-source-default")).toBeNull();
    expect(queryByTestId("logs-source-mtur")).toBeNull();
  });
});
