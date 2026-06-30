import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PainelLogs } from "@/components/painel/PainelLogs";
import { server } from "@/mocks/server";
import { logsEmpty, logsLines } from "@/mocks/handlers/logs";

import { renderWithClient } from "../../cms/__tests__/test-utils";

beforeEach(() => {
  server.resetHandlers();
});

describe("PainelLogs", () => {
  it("painel-logs-panel is off-screen when open=false", () => {
    renderWithClient(
      <PainelLogs open={false} onClose={vi.fn()} source="tripadvisor" />,
    );
    const panel = screen.getByTestId("painel-logs-panel");
    expect(panel.style.transform).toContain("translateX(100%)");
  });

  it("painel-logs-panel is on-screen when open=true", () => {
    server.use(logsLines());
    renderWithClient(
      <PainelLogs open={true} onClose={vi.fn()} source="tripadvisor" />,
    );
    const panel = screen.getByTestId("painel-logs-panel");
    expect(panel.style.transform).toContain("translateX(0)");
  });

  it("shows log event text from MSW handler", async () => {
    server.use(logsLines());
    renderWithClient(
      <PainelLogs open={true} onClose={vi.fn()} source="tripadvisor" />,
    );
    const lines = await screen.findAllByTestId("log-line");
    expect(lines.length).toBe(2);
    const allText = lines.map((l) => l.textContent ?? "").join(" ");
    expect(allText).toContain("page_ingested");
  });

  it("header shows source label", async () => {
    server.use(logsEmpty("tripadvisor"));
    renderWithClient(
      <PainelLogs open={true} onClose={vi.fn()} source="tripadvisor" />,
    );
    await waitFor(() =>
      expect(screen.getByText(/Logs · TripAdvisor/)).toBeTruthy(),
    );
  });

  it("close button fires onClose", async () => {
    server.use(logsEmpty("tripadvisor"));
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderWithClient(
      <PainelLogs open={true} onClose={onClose} source="tripadvisor" />,
    );
    await user.click(screen.getByTestId("painel-logs-close"));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
