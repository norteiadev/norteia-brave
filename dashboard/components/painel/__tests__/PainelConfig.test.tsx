import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { Toaster } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PainelConfig } from "@/components/painel/PainelConfig";
import {
  configGetSuccess,
  configPatchError,
  configPatchSuccess,
} from "@/mocks/handlers/config";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

/** Every PATCH body that reached the API. */
let patches: Record<string, unknown>[] = [];

function renderConfig() {
  return renderWithClient(
    <>
      <Toaster />
      <PainelConfig />
    </>,
  );
}

async function loaded() {
  await waitFor(() =>
    expect(screen.getByTestId("config-weight-sum")).toHaveAttribute(
      "data-valid",
      "true",
    ),
  );
}

// fireEvent (not userEvent): userEvent's internal delays fight fake timers.
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function fakeTimers() {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
}

beforeEach(() => {
  patches = [];
  server.use(
    configGetSuccess(),
    configPatchSuccess(undefined, (body) => patches.push(body)),
  );
});

afterEach(() => {
  vi.useRealTimers();
});

describe("PainelConfig", () => {
  it("seeds weights, threshold and lane switches from the snapshot, with no pending bar", async () => {
    renderConfig();
    await loaded();

    expect(screen.getByTestId("config-weight-sum")).toHaveTextContent("Soma 100");
    expect(screen.getByTestId("config-threshold-mar")).toHaveValue(85);
    expect(screen.getByRole("switch", { name: "Coleta TripAdvisor" })).toBeChecked();
    expect(
      screen.getByRole("switch", { name: "Enriquecimento Google Places" }),
    ).toBeChecked();
    expect(screen.getByRole("switch", { name: "Descrição editorial" })).toBeChecked();
    expect(
      screen.getByRole("switch", { name: "Descrição em lote (Batch API)" }),
    ).not.toBeChecked();
    // The dormant Places collection source is not a lane on this screen.
    expect(screen.queryByText(/source\.default/)).toBeNull();
    expect(screen.queryByTestId("config-save-bar")).toBeNull();
  });

  it("shows the env spend limits read-only, formatted as money", async () => {
    renderConfig();
    await loaded();

    const env = screen.getByTestId("config-env-limits");
    expect(env).toHaveTextContent("Orçamento LLM diário");
    expect(env).toHaveTextContent("US$ 50,00");
    expect(env).toHaveTextContent("Crédito do Google Places");
    expect(env).toHaveTextContent("US$ 300,00");
    expect(env).toHaveTextContent("APIFY_TOTAL_USD_CAP");
    expect(env.querySelector("input")).toBeNull();
  });

  it("blocks Salvar with a clear message while the weights do not sum to 100", async () => {
    renderConfig();
    await loaded();

    fireEvent.change(screen.getByTestId("config-weight-weight_origem"), {
      target: { value: "30" },
    });

    expect(screen.getByTestId("config-weight-sum")).toHaveAttribute("data-valid", "false");
    expect(screen.getByTestId("config-weight-sum")).toHaveTextContent("Soma 110");
    expect(screen.getByTestId("config-weight-warning")).toHaveTextContent(
      "Os pesos precisam somar 100",
    );
    expect(screen.getByRole("button", { name: "Salvar" })).toBeDisabled();
  });

  it("slider and number move together", async () => {
    renderConfig();
    await loaded();

    fireEvent.change(screen.getByRole("slider", { name: "Origem" }), {
      target: { value: "25" },
    });
    expect(screen.getByTestId("config-weight-weight_origem")).toHaveValue(25);
  });

  it("Descartar drops the draft and sends nothing", async () => {
    renderConfig();
    await loaded();

    fireEvent.click(screen.getByRole("switch", { name: "Coleta TripAdvisor" }));
    expect(screen.getByTestId("config-save-bar")).toHaveTextContent("1 alteração");

    fireEvent.click(screen.getByRole("button", { name: "Descartar" }));

    expect(screen.queryByTestId("config-save-bar")).toBeNull();
    expect(screen.getByRole("switch", { name: "Coleta TripAdvisor" })).toBeChecked();
    expect(patches).toHaveLength(0);
  });

  it("Salvar waits for the undo window, then sends exactly one PATCH with only the changed keys", async () => {
    renderConfig();
    await loaded();

    fireEvent.change(screen.getByTestId("config-threshold-mar"), {
      target: { value: "80" },
    });
    fireEvent.click(screen.getByRole("switch", { name: "Coleta TripAdvisor" }));
    expect(screen.getByTestId("config-save-bar")).toHaveTextContent("2 alterações");

    fakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Salvar" }));
    await advance(3_000);
    expect(patches).toHaveLength(0);

    await advance(3_500);
    await vi.waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toEqual({
      "score.threshold_mar": 80,
      "source.tripadvisor.enabled": false,
    });

    await advance(30_000);
    expect(patches).toHaveLength(1);
  });

  it("a weight edit sends the five weights and never an env limit", async () => {
    renderConfig();
    await loaded();

    fireEvent.change(screen.getByTestId("config-weight-weight_origem"), {
      target: { value: "25" },
    });
    fireEvent.change(screen.getByTestId("config-weight-weight_atualidade"), {
      target: { value: "15" },
    });

    fakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Salvar" }));
    await advance(6_500);
    await vi.waitFor(() => expect(patches).toHaveLength(1));

    expect(patches[0]).toEqual({
      "score.weight_origem": 25,
      "score.weight_completude": 20,
      "score.weight_corroboracao": 20,
      "score.weight_atualidade": 15,
      "score.weight_validacao_humana": 20,
    });
    const keys = Object.keys(patches[0]).join(" ");
    expect(keys).not.toMatch(/usd|cap|budget/);
  });

  it("Desfazer after Salvar sends nothing and restores the previous values", async () => {
    renderConfig();
    await loaded();

    fireEvent.click(screen.getByRole("switch", { name: "Coleta TripAdvisor" }));
    fakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Salvar" }));
    await advance(100);
    // Saved optimistically: the switch keeps the new value, the bar is gone.
    expect(screen.getByRole("switch", { name: "Coleta TripAdvisor" })).not.toBeChecked();
    expect(screen.queryByTestId("config-save-bar")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Desfazer" }));
    await advance(10_000);

    expect(patches).toHaveLength(0);
    expect(screen.getByRole("switch", { name: "Coleta TripAdvisor" })).toBeChecked();
    expect(screen.queryByTestId("config-save-bar")).toBeNull();
  });

  it("surfaces the server 422 and keeps the draft pending", async () => {
    server.use(configPatchError());
    renderConfig();
    await loaded();

    fireEvent.change(screen.getByTestId("config-threshold-mar"), {
      target: { value: "80" },
    });
    fakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Salvar" }));
    await advance(6_500);

    await vi.waitFor(() =>
      expect(screen.getByText(/must sum to 100/)).toBeInTheDocument(),
    );
    expect(screen.getByTestId("config-save-bar")).toHaveTextContent("1 alteração");
    expect(screen.getByTestId("config-threshold-mar")).toHaveValue(80);
  });
});
