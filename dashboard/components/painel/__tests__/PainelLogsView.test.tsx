import { fireEvent } from "@testing-library/react";
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

  it("switches the active log source", async () => {
    server.use(logsLines());

    const { getByTestId, findByTestId } = renderWithClient(<PainelLogsView />);
    await findByTestId("painel-logs-inline");

    const defaultBtn = getByTestId("logs-source-default");
    expect(defaultBtn.getAttribute("data-active")).toBe("false");

    fireEvent.click(defaultBtn);
    expect(defaultBtn.getAttribute("data-active")).toBe("true");
  });
});
