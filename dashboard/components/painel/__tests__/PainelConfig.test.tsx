import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { PainelConfig } from "@/components/painel/PainelConfig";
import {
  configGetSuccess,
  configPatchError,
  configPatchSuccess,
} from "@/mocks/handlers/config";
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

describe("PainelConfig", () => {
  it("seeds the form from the snapshot: weights sum 100, sources + active mode", async () => {
    server.use(configGetSuccess(), configPatchSuccess());

    const { getByTestId, getByText, queryByTestId } = renderWithClient(
      <PainelConfig />,
    );

    await waitFor(() =>
      expect(getByTestId("config-weight-sum")).toHaveTextContent("100"),
    );
    expect(getByTestId("config-weight-sum").getAttribute("data-valid")).toBe(
      "true",
    );
    // engine.mode = LIGADO in the sample snapshot
    expect(getByTestId("config-mode-LIGADO").getAttribute("data-active")).toBe(
      "true",
    );
    // Only the surfaced source (TripAdvisor) renders, with its friendly label.
    // The dormant "default"/Places lane is in the snapshot but filtered out here.
    getByTestId("config-source-tripadvisor");
    getByText("TripAdvisor");
    expect(queryByTestId("config-source-default")).toBeNull();
    // save enabled while the weights are valid
    expect(getByTestId("config-save-pesos")).not.toBeDisabled();
  });

  it("blocks save when the five weights do not sum to 100 (client guard)", async () => {
    server.use(configGetSuccess(), configPatchSuccess());

    const { getByTestId } = renderWithClient(<PainelConfig />);

    await waitFor(() =>
      expect(getByTestId("config-weight-sum")).toHaveTextContent("100"),
    );

    // Bump origem 20 → 30 ⇒ sum 110 ⇒ invalid.
    fireEvent.change(getByTestId("config-weight-weight_origem"), {
      target: { value: "30" },
    });

    await waitFor(() =>
      expect(getByTestId("config-weight-sum")).toHaveTextContent("110"),
    );
    expect(getByTestId("config-weight-sum").getAttribute("data-valid")).toBe(
      "false",
    );
    getByTestId("config-weight-warning");
    expect(getByTestId("config-save-pesos")).toBeDisabled();
  });

  it("saving valid pesos PATCHes /config", async () => {
    server.use(configGetSuccess(), configPatchSuccess());

    const { getByTestId } = renderWithClient(<PainelConfig />);

    await waitFor(() =>
      expect(getByTestId("config-weight-sum")).toHaveTextContent("100"),
    );

    fireEvent.click(getByTestId("config-save-pesos"));

    await waitFor(() =>
      expect(
        requests.some(
          (r) =>
            r.method === "PATCH" && r.url.includes("/api/api/v1/config"),
        ),
      ).toBe(true),
    );
  });

  it("changing the engine mode PATCHes /config", async () => {
    server.use(configGetSuccess(), configPatchSuccess());

    const { getByTestId } = renderWithClient(<PainelConfig />);

    await waitFor(() =>
      expect(getByTestId("config-mode-LIGADO").getAttribute("data-active")).toBe(
        "true",
      ),
    );

    fireEvent.click(getByTestId("config-mode-PAUSADO"));

    await waitFor(() =>
      expect(
        requests.some(
          (r) =>
            r.method === "PATCH" && r.url.includes("/api/api/v1/config"),
        ),
      ).toBe(true),
    );
  });

  it("renders the description-enrichment toggle from the snapshot (on by default)", async () => {
    server.use(configGetSuccess(), configPatchSuccess());

    const { getByTestId } = renderWithClient(<PainelConfig />);

    await waitFor(() => getByTestId("config-enrichment-toggle"));
    expect(getByTestId("config-enrichment-toggle")).toBeChecked();
  });

  it("toggling description enrichment off PATCHes /config", async () => {
    server.use(configGetSuccess(), configPatchSuccess());

    const { getByTestId } = renderWithClient(<PainelConfig />);

    await waitFor(() => getByTestId("config-enrichment-toggle"));
    fireEvent.click(getByTestId("config-enrichment-toggle"));

    await waitFor(() =>
      expect(
        requests.some(
          (r) => r.method === "PATCH" && r.url.includes("/api/api/v1/config"),
        ),
      ).toBe(true),
    );
  });

  it("surfaces the server 422 (weight-sum backstop) without crashing", async () => {
    // GET valid, but PATCH is rejected 422 — the view must not crash on the
    // authoritative server guard (client already blocks the obvious case).
    server.use(configGetSuccess(), configPatchError());

    const { getByTestId } = renderWithClient(<PainelConfig />);

    await waitFor(() =>
      expect(getByTestId("config-weight-sum")).toHaveTextContent("100"),
    );

    fireEvent.click(getByTestId("config-save-pesos"));

    // The PATCH is attempted; the 422 is handled (toast) and the form survives.
    await waitFor(() =>
      expect(
        requests.some((r) => r.method === "PATCH"),
      ).toBe(true),
    );
    expect(getByTestId("config-weight-sum")).toHaveTextContent("100");
  });
});
