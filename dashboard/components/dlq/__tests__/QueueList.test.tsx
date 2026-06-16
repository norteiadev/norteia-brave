import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { QueueList } from "@/components/dlq/QueueList";
import { useValidateDlqRecord } from "@/components/dlq/dlq-actions";
import { setOperatorToken } from "@/lib/api-client";
import { server } from "@/mocks/server";
import {
  dlqBatchSuccess,
  dlqListEmpty,
  dlqListError,
  dlqListSuccess,
  dlqUnauthorized,
  dlqValidateSuccess,
  sampleListItems,
} from "@/mocks/handlers/dlq";

import { renderWithClient } from "./test-utils";

beforeEach(() => {
  server.resetHandlers();
});

describe("QueueList", () => {
  it("defaults the UF filter to the BA/RJ/SP/SC/CE/PE priority order", async () => {
    server.use(dlqListSuccess());
    renderWithClient(<QueueList />);

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
    // BA is the active (default) filter
    expect(buttons[0]).toHaveAttribute("aria-pressed", "true");
    // BA-only rows render (the success handler filters by uf)
    expect(
      await screen.findByText("ba:salvador:pelourinho"),
    ).toBeInTheDocument();
  });

  it("enables 'Validar lote' only once rows are selected", async () => {
    const user = userEvent.setup();
    server.use(dlqListSuccess(sampleListItems), dlqBatchSuccess(1));
    renderWithClient(<QueueList />);

    await screen.findByText("ba:salvador:pelourinho");
    const batchBtn = screen.getByRole("button", { name: /Validar lote/ });
    expect(batchBtn).toBeDisabled();

    await user.click(screen.getAllByRole("checkbox")[0]);
    expect(batchBtn).toBeEnabled();
    expect(batchBtn).toHaveTextContent("(1)");
  });

  it("calls onSelect with the row id when a row is clicked", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    server.use(dlqListSuccess());
    renderWithClient(<QueueList onSelect={onSelect} />);

    await user.click(await screen.findByText("ba:salvador:pelourinho"));
    expect(onSelect).toHaveBeenCalledWith(sampleListItems[0].id);
  });

  it("shows the empty state for a state with no DLQ records", async () => {
    server.use(dlqListEmpty());
    renderWithClient(<QueueList />);
    expect(
      await screen.findByText("DLQ vazia para este estado"),
    ).toBeInTheDocument();
  });

  it("shows the fetch-error state with retry", async () => {
    server.use(dlqListError(500));
    renderWithClient(<QueueList />);
    expect(
      await screen.findByText("Não foi possível carregar"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Tentar novamente" }),
    ).toBeInTheDocument();
  });

  it("shows the 401 session-expired state", async () => {
    server.use(...dlqUnauthorized());
    renderWithClient(<QueueList />);
    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });
});

/** Exercises the validate mutation's optimistic removal + invalidate→refetch,
 *  sharing ONE QueryClient with a live QueueList so the queue actually refetches. */
function ValidateButton() {
  const validate = useValidateDlqRecord("BA", "destination");
  return (
    <button onClick={() => validate.mutate(sampleListItems[0].id)}>
      validar
    </button>
  );
}

describe("useValidateDlqRecord (approve → invalidate → refetch)", () => {
  it("optimistically drops the row and refetches the queue on settle", async () => {
    const user = userEvent.setup();
    setOperatorToken("test-operator-token");

    let listCalls = 0;
    const countListCalls = ({ request }: { request: Request }) => {
      const u = new URL(request.url);
      if (u.pathname === "/api/api/v1/dlq") listCalls += 1;
    };
    server.events.on("request:start", countListCalls);

    // Initial list has BA row; after validate the refetch returns it gone
    // (it left the DLQ for Mar) — the realistic end state.
    server.use(dlqListSuccess(), dlqValidateSuccess());

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <QueueList />
        <ValidateButton />
      </QueryClientProvider>,
    );

    await screen.findByText("ba:salvador:pelourinho");
    const initialCalls = listCalls;

    // After approval the refetch returns the queue WITHOUT the validated row.
    server.use(dlqListSuccess([]));

    await user.click(screen.getByText("validar"));

    // invalidateQueries(['dlq']) on settle → the list refetches
    await waitFor(() => expect(listCalls).toBeGreaterThan(initialCalls));
    // optimistic removal + refetch end state: the validated row is gone
    await waitFor(() =>
      expect(
        screen.queryByText("ba:salvador:pelourinho"),
      ).not.toBeInTheDocument(),
    );

    server.events.removeListener("request:start", countListCalls);
  });
});
