import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ReviewPanel } from "@/components/dlq/ReviewPanel";
import { server } from "@/mocks/server";
import {
  dlqDetailEmpty,
  dlqDetailError,
  dlqDetailSuccess,
  dlqUnauthorized,
} from "@/mocks/handlers/dlq";

import { renderWithClient } from "./test-utils";

const RIO_ID = "11111111-1111-1111-1111-111111111111";

beforeEach(() => {
  server.resetHandlers();
});

describe("ReviewPanel", () => {
  it("renders the §7.6 breakdown + Nascente/Rio/signals on success", async () => {
    server.use(dlqDetailSuccess());
    renderWithClient(<ReviewPanel rioId={RIO_ID} />);

    expect(await screen.findByText("DLQ Review")).toBeInTheDocument();
    expect(screen.getByText("§7.6 Breakdown")).toBeInTheDocument();
    expect(screen.getByText("origem")).toBeInTheDocument();
    expect(screen.getByText("Nascente (payload bruto)")).toBeInTheDocument();
    expect(screen.getByText("Rio normalizado")).toBeInTheDocument();
  });

  it("renders an empty-friendly detail without crashing (empty payloads)", async () => {
    server.use(dlqDetailEmpty());
    renderWithClient(<ReviewPanel rioId={RIO_ID} />);

    expect(await screen.findByText("DLQ Review")).toBeInTheDocument();
    // empty whatsapp_log surfaces the empty-event copy
    expect(
      screen.getByText("Nenhum evento registrado."),
    ).toBeInTheDocument();
  });

  it("shows the fetch-error state with a retry button", async () => {
    server.use(dlqDetailError(500));
    renderWithClient(<ReviewPanel rioId={RIO_ID} />);

    expect(
      await screen.findByText("Não foi possível carregar"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Tentar novamente" }),
    ).toBeInTheDocument();
  });

  it("shows the 401 session-expired state", async () => {
    server.use(...dlqUnauthorized());
    renderWithClient(<ReviewPanel rioId={RIO_ID} />);

    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });

  it("prompts to select a record when none is given", () => {
    renderWithClient(<ReviewPanel rioId={null} />);
    expect(
      screen.getByText("Selecione um registro na fila para revisar."),
    ).toBeInTheDocument();
  });

  it("renders the loading skeleton before data arrives", async () => {
    server.use(dlqDetailSuccess());
    renderWithClient(<ReviewPanel rioId={RIO_ID} />);
    expect(screen.getByTestId("review-skeleton")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText("DLQ Review")).toBeInTheDocument(),
    );
  });
});
