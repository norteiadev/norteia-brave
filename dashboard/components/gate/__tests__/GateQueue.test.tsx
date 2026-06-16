import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GateQueue } from "@/components/gate/GateQueue";
import { GateReviewPanel } from "@/components/gate/GateReviewPanel";
import { server } from "@/mocks/server";
import {
  gateListEmpty,
  gateListError,
  gateListSuccess,
  gateUnauthorized,
  rampContextSuccess,
  sampleGateItems,
} from "@/mocks/handlers/gate";

import { makeClient, renderWithClient } from "./test-utils";

beforeEach(() => {
  server.resetHandlers();
});

describe("GateQueue", () => {
  it("defaults the UF filter to the BA/RJ/SP/SC/CE/PE priority order", async () => {
    server.use(gateListSuccess());
    renderWithClient(<GateQueue />);

    const tablist = screen.getByRole("tablist");
    const buttons = within(tablist).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual([
      "BA",
      "RJ",
      "SP",
      "SC",
      "CE",
      "PE",
    ]);
    expect(buttons[0]).toHaveAttribute("aria-pressed", "true");
    // BA-only rows render (the success handler filters by uf)
    expect(
      await screen.findByText("ba:salvador:farol-da-barra"),
    ).toBeInTheDocument();
  });

  it("shows the gate empty state 'Fila de gate vazia'", async () => {
    server.use(gateListEmpty());
    renderWithClient(<GateQueue />);
    expect(
      await screen.findByText("Fila de gate vazia"),
    ).toBeInTheDocument();
  });

  it("shows the fetch-error state with retry", async () => {
    server.use(gateListError(500));
    renderWithClient(<GateQueue />);
    expect(
      await screen.findByText("Não foi possível carregar"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Tentar novamente" }),
    ).toBeInTheDocument();
  });

  it("shows the 401 session-expired state", async () => {
    server.use(...gateUnauthorized());
    renderWithClient(<GateQueue />);
    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });

  it("calls onSelect with the full row when a row is clicked", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    server.use(gateListSuccess());
    renderWithClient(<GateQueue onSelect={onSelect} />);

    await user.click(await screen.findByText("ba:salvador:farol-da-barra"));
    expect(onSelect).toHaveBeenCalledWith(sampleGateItems[0]);
  });
});

describe("GateReviewPanel — LGPD masked-phone (T-04-18)", () => {
  it("renders the masked phone labeled 'telefone (minimizado)'", async () => {
    server.use(rampContextSuccess());
    const client = makeClient();
    render(
      <QueryClientProvider client={client}>
        <GateReviewPanel item={sampleGateItems[0]} />
      </QueryClientProvider>,
    );

    expect(screen.getByText("telefone (minimizado)")).toBeInTheDocument();
    expect(screen.getByText("+55 71 9••••-••42")).toBeInTheDocument();
  });

  it("never renders a raw E.164 phone number in the DOM", async () => {
    server.use(rampContextSuccess());
    const client = makeClient();
    // Adversarial: a raw phone_e164 slips into normalized — the panel must NOT
    // render it (defensive redaction belt-and-suspenders, never reconstruct).
    const poisoned = {
      ...sampleGateItems[0],
      normalized: {
        ...sampleGateItems[0].normalized,
        phone_e164: "+5571998765442",
      },
    };
    const { container } = render(
      <QueryClientProvider client={client}>
        <GateReviewPanel item={poisoned} />
      </QueryClientProvider>,
    );

    const dom = container.textContent ?? "";
    // No raw E.164 digits run (a full unmasked number) anywhere in the DOM.
    expect(dom).not.toContain("+5571998765442");
    expect(dom).not.toMatch(/\+55\s?71\s?9\d{4}-?\d{4}/);
    // The masked value IS present (proves we render the minimized field).
    expect(dom).toContain("+55 71 9••••-••42");
  });
});
