import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { TranscriptPanel } from "@/components/conversations/TranscriptPanel";
import { ConversationList } from "@/components/conversations/ConversationList";
import { server } from "@/mocks/server";
import {
  SAMPLE_MASKED_PHONE,
  SAMPLE_RIO_ID,
  conversationDetailError,
  conversationDetailNotFound,
  conversationDetailSuccess,
  conversationDetailUnauthorized,
  conversationsListEmpty,
  conversationsListSuccess,
  conversationsListUnauthorized,
  sampleConversationDetail,
} from "@/mocks/handlers/conversations";

import { renderWithClient } from "../../dlq/__tests__/test-utils";

beforeEach(() => {
  server.resetHandlers();
});

/**
 * A raw E.164 number that MUST NEVER appear in the DOM (LGPD R3, T-04-28). The
 * masked sample is `+55 11 9••••-••42`; this is the un-minimized form. The
 * backend never emits it and the panel never reconstructs it.
 */
const RAW_E164 = "+5511987654342";

describe("TranscriptPanel", () => {
  it("renders inbound/outbound bubbles with the masked phone label", async () => {
    server.use(conversationDetailSuccess());
    renderWithClient(<TranscriptPanel rioId={SAMPLE_RIO_ID} />);

    expect(await screen.findByTestId("transcript-panel")).toBeInTheDocument();
    // Masked phone + the "minimizado" label (UI-SPEC).
    expect(screen.getByText(SAMPLE_MASKED_PHONE)).toBeInTheDocument();
    expect(screen.getByText("telefone (minimizado)")).toBeInTheDocument();
    // Both bubble directions render.
    expect(
      screen.getAllByTestId("transcript-bubble-outbound").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByTestId("transcript-bubble-inbound").length,
    ).toBeGreaterThan(0);
    // Message content is shown.
    expect(
      screen.getByText("Funciona de terça a domingo, das 9h às 17h."),
    ).toBeInTheDocument();
  });

  it("renders ONLY the masked phone — no raw E.164 reaches the DOM (LGPD R3)", async () => {
    server.use(conversationDetailSuccess());
    const { container } = renderWithClient(
      <TranscriptPanel rioId={SAMPLE_RIO_ID} />,
    );

    expect(await screen.findByTestId("transcript-panel")).toBeInTheDocument();
    // The full DOM text must contain the masked value and never the raw number.
    expect(container.textContent).toContain(SAMPLE_MASKED_PHONE);
    expect(container.textContent).not.toContain(RAW_E164);
    // Defensive: the un-masked digit run must not appear anywhere in the markup.
    expect(container.innerHTML).not.toContain(RAW_E164);
    // No element carries a raw-phone field either.
    expect(screen.queryByText(RAW_E164)).not.toBeInTheDocument();
  });

  it("shows the no-selection empty state when no conversation is selected", () => {
    renderWithClient(<TranscriptPanel rioId={null} />);
    expect(screen.getByTestId("transcript-empty")).toBeInTheDocument();
    expect(screen.getByText("Sem conversas ainda")).toBeInTheDocument();
  });

  it("renders the loading skeleton before the transcript arrives", () => {
    server.use(conversationDetailSuccess());
    renderWithClient(<TranscriptPanel rioId={SAMPLE_RIO_ID} />);
    expect(screen.getByTestId("transcript-skeleton")).toBeInTheDocument();
  });

  it("shows a 404 not-found state for an unknown rio_id", async () => {
    server.use(conversationDetailNotFound());
    renderWithClient(<TranscriptPanel rioId={SAMPLE_RIO_ID} />);
    expect(
      await screen.findByText("Conversa não encontrada"),
    ).toBeInTheDocument();
  });

  it("shows the fetch-error state with a retry button", async () => {
    server.use(conversationDetailError(500));
    renderWithClient(<TranscriptPanel rioId={SAMPLE_RIO_ID} />);
    expect(
      await screen.findByText("Não foi possível carregar"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Tentar novamente" }),
    ).toBeInTheDocument();
  });

  it("shows the 401 session-expired state", async () => {
    server.use(conversationDetailUnauthorized());
    renderWithClient(<TranscriptPanel rioId={SAMPLE_RIO_ID} />);
    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });

  it("renders the extraction snapshot under the turn it rode along", async () => {
    server.use(conversationDetailSuccess());
    renderWithClient(<TranscriptPanel rioId={SAMPLE_RIO_ID} />);
    expect(await screen.findByTestId("transcript-panel")).toBeInTheDocument();
    const extracted = sampleConversationDetail.messages.find(
      (m) => m.extracted,
    );
    expect(extracted).toBeDefined();
    expect(screen.getByText(/opening_hours/)).toBeInTheDocument();
  });
});

describe("ConversationList", () => {
  it("renders conversations with masked phones — no raw E.164 (LGPD R3)", async () => {
    server.use(conversationsListSuccess());
    const { container } = renderWithClient(<ConversationList />);

    expect(await screen.findByTestId("conversation-list")).toBeInTheDocument();
    expect(screen.getByText(SAMPLE_MASKED_PHONE)).toBeInTheDocument();
    expect(container.textContent).not.toContain(RAW_E164);
  });

  it("shows the empty-conversations copy when the log is empty", async () => {
    server.use(conversationsListEmpty());
    renderWithClient(<ConversationList />);
    expect(await screen.findByText("Sem conversas ainda")).toBeInTheDocument();
  });

  it("shows the 401 session-expired state", async () => {
    server.use(conversationsListUnauthorized());
    renderWithClient(<ConversationList />);
    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });
});
