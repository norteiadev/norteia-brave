import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { MarReadyList } from "@/components/mar-ready/MarReadyList";
import { setOperatorToken } from "@/lib/api-client";
import { marReadyKeys, type MarReadyItem } from "@/lib/mar-ready-api";
import { server } from "@/mocks/server";
import {
  marReadyList,
  marReadyListEmpty,
  marReadyListError,
  promoteBatchSuccess,
  promoteSuccess,
  sampleMarReadyItems,
} from "@/mocks/handlers/mar-ready";

beforeEach(() => {
  setOperatorToken("test-operator-token");
  server.resetHandlers();
});

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

describe("MarReadyList", () => {
  it("renders attraction rows from the mar-ready list", async () => {
    server.use(marReadyList(), promoteSuccess(), promoteBatchSuccess());
    renderWithClient(<MarReadyList />);

    expect(
      await screen.findByText("tripadvisor:attraction:12345"),
    ).toBeInTheDocument();
    expect(screen.getByText("tripadvisor:attraction:67890")).toBeInTheDocument();
  });

  it("renders a 'Promover' button per row", async () => {
    server.use(marReadyList(), promoteSuccess(), promoteBatchSuccess());
    renderWithClient(<MarReadyList />);

    await screen.findByText("tripadvisor:attraction:12345");
    const promoverButtons = screen.getAllByRole("button", { name: "Promover" });
    expect(promoverButtons.length).toBe(sampleMarReadyItems.length);
  });

  it("shows empty state when list is empty", async () => {
    server.use(marReadyListEmpty(), promoteSuccess(), promoteBatchSuccess());
    renderWithClient(<MarReadyList />);

    expect(
      await screen.findByText("Nenhum atrativo pronto para promoção"),
    ).toBeInTheDocument();
  });

  it("shows fetch-error state with retry", async () => {
    server.use(marReadyListError(500), promoteSuccess(), promoteBatchSuccess());
    renderWithClient(<MarReadyList />);

    expect(
      await screen.findByText("Não foi possível carregar"),
    ).toBeInTheDocument();
  });

  it("bulk 'Promover selecionados' is disabled until rows are selected", async () => {
    const user = userEvent.setup();
    server.use(marReadyList(), promoteSuccess(), promoteBatchSuccess());
    renderWithClient(<MarReadyList />);

    await screen.findByText("tripadvisor:attraction:12345");
    const batchBtn = screen.getByTestId("mar-ready-batch-btn");
    expect(batchBtn).toBeDisabled();

    await user.click(screen.getAllByRole("checkbox")[0]);
    expect(batchBtn).toBeEnabled();
  });

  it("clicking 'Promover selecionados' after selecting rows shows confirm dialog", async () => {
    const user = userEvent.setup();
    server.use(marReadyList(), promoteSuccess(), promoteBatchSuccess());
    renderWithClient(<MarReadyList />);

    await screen.findByText("tripadvisor:attraction:12345");
    await user.click(screen.getAllByRole("checkbox")[0]);

    const batchBtn = screen.getByTestId("mar-ready-batch-btn");
    await user.click(batchBtn);

    expect(
      await screen.findByText(/Promover .* atrativo/),
    ).toBeInTheDocument();
  });
});

describe("MarReadyList — optimistic promote with 409 rollback", () => {
  it("optimistically removes the row when 'Promover' is clicked", async () => {
    const user = userEvent.setup();
    server.use(marReadyList(), promoteSuccess(), promoteBatchSuccess());

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });

    // Pre-seed the cache so the optimistic update fires against something
    const listKey = marReadyKeys.list();
    client.setQueryData<MarReadyItem[]>(listKey, [...sampleMarReadyItems]);

    render(
      <QueryClientProvider client={client}>
        <MarReadyList />
      </QueryClientProvider>,
    );

    await screen.findByText("tripadvisor:attraction:12345");

    // After promote, re-query returns the list without this row
    server.use(marReadyList(sampleMarReadyItems.slice(1)));

    const promoverBtns = screen.getAllByRole("button", { name: "Promover" });
    await user.click(promoverBtns[0]);

    await waitFor(() =>
      expect(
        screen.queryByText("tripadvisor:attraction:12345"),
      ).not.toBeInTheDocument(),
    );
  });
});
