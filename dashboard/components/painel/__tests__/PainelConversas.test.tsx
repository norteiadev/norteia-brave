import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { PainelConversas } from "@/components/painel/PainelConversas";
import { server } from "@/mocks/server";
import {
  SAMPLE_MASKED_PHONE,
  SAMPLE_RIO_ID,
  conversationDetailSuccess,
  conversationsListEmpty,
  conversationsListSuccess,
} from "@/mocks/handlers/conversations";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

beforeEach(() => {
  server.resetHandlers();
});

/**
 * The un-minimized E.164 form that MUST NEVER reach the DOM (LGPD R3, T-04-28).
 * Masked sample is `+55 11 9••••-••42`; this is the raw number the panel must
 * never reconstruct.
 */
const RAW_E164 = "+5511987654342";

describe("PainelConversas", () => {
  it("renders one master row per conversation with the masked phone", async () => {
    server.use(conversationsListSuccess(), conversationDetailSuccess());
    renderWithClient(<PainelConversas />);

    const rows = await screen.findAllByTestId("convo-row");
    expect(rows).toHaveLength(2);
    expect(screen.getByText(SAMPLE_MASKED_PHONE)).toBeInTheDocument();
  });

  it("auto-selects the first conversation and loads its transcript bubbles", async () => {
    server.use(conversationsListSuccess(), conversationDetailSuccess());
    renderWithClient(<PainelConversas />);

    // Bubbles for the auto-selected first conversation arrive.
    const bubbles = await screen.findAllByTestId("convo-bubble");
    expect(bubbles.length).toBeGreaterThan(0);
    // Transcript content shows (also appears in the master preview, so >= 1).
    expect(
      screen.getAllByText("Funciona de terça a domingo, das 9h às 17h.")
        .length,
    ).toBeGreaterThan(0);
  });

  it("renders the extraction snapshot for a message that carries one", async () => {
    server.use(conversationsListSuccess(), conversationDetailSuccess());
    renderWithClient(<PainelConversas />);

    const pre = await screen.findByTestId("convo-extracted");
    expect(pre).toBeInTheDocument();
    expect(pre.textContent).toContain("opening_hours");
  });

  it("shows ONLY the masked phone — no raw E.164 leaks into the DOM (LGPD R3)", async () => {
    server.use(conversationsListSuccess(), conversationDetailSuccess());
    const { container } = renderWithClient(<PainelConversas />);

    await screen.findAllByTestId("convo-bubble");
    expect(container.textContent).toContain(SAMPLE_MASKED_PHONE);
    expect(container.textContent).not.toContain(RAW_E164);
    expect(container.innerHTML).not.toContain(RAW_E164);
  });

  it("selecting another row loads that conversation's detail query", async () => {
    server.use(conversationsListSuccess(), conversationDetailSuccess());
    renderWithClient(<PainelConversas />);

    const rows = await screen.findAllByTestId("convo-row");
    // First row is auto-selected (WhatsApp-green left border tint).
    expect(rows[0].getAttribute("data-id")).toBe(SAMPLE_RIO_ID);

    rows[1].click();
    await waitFor(() => {
      expect(
        screen.getAllByTestId("convo-bubble").length,
      ).toBeGreaterThan(0);
    });
  });

  it("shows the no-selection placeholder when the conversation log is empty", async () => {
    server.use(conversationsListEmpty());
    renderWithClient(<PainelConversas />);

    expect(await screen.findByTestId("convo-empty")).toBeInTheDocument();
    expect(
      screen.getByText("Selecione uma conversa para ver a transcrição."),
    ).toBeInTheDocument();
  });
});
