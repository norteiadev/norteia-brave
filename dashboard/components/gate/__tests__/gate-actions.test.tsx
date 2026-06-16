import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GatePage from "@/app/gate/page";
import { GateQueue } from "@/components/gate/GateQueue";
import { useApproveGate, useRejectGate } from "@/components/gate/gate-actions";
import { setOperatorToken } from "@/lib/api-client";
import { server } from "@/mocks/server";
import {
  gateApproveSuccess,
  gateListEmpty,
  gateListError,
  gateListSuccess,
  gateRejectSuccess,
  gateUnauthorized,
  rampContextSuccess,
  sampleGateItems,
} from "@/mocks/handlers/gate";

import { makeClient, renderWithClient } from "./test-utils";

beforeEach(() => {
  server.resetHandlers();
});

/** Buttons that exercise the gate mutation hooks against the live MSW handlers. */
function ApproveButton({ rioId }: { rioId: string }) {
  const approve = useApproveGate();
  return <button onClick={() => approve.mutate(rioId)}>aprovar</button>;
}

function RejectButton({ rioId }: { rioId: string }) {
  const reject = useRejectGate();
  return <button onClick={() => reject.mutate(rioId)}>rejeitar</button>;
}

describe("useApproveGate (approve → invalidate → refetch ['gate'])", () => {
  it("approves and refetches the queue on settle", async () => {
    const user = userEvent.setup();
    setOperatorToken("test-operator-token");

    let listCalls = 0;
    const countListCalls = ({ request }: { request: Request }) => {
      const u = new URL(request.url);
      if (u.pathname === "/api/api/v1/atrativos/gate") listCalls += 1;
    };
    server.events.on("request:start", countListCalls);

    server.use(gateListSuccess(), rampContextSuccess(), gateApproveSuccess());

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <GateQueue />
        <ApproveButton rioId={sampleGateItems[0].rio_id} />
      </QueryClientProvider>,
    );

    await screen.findByText("ba:salvador:farol-da-barra");
    const initialCalls = listCalls;

    // After approval the refetch returns the queue WITHOUT the approved row.
    server.use(gateListSuccess([]));

    await user.click(screen.getByText("aprovar"));

    // invalidateQueries(['gate']) on settle → the list refetches
    await waitFor(() => expect(listCalls).toBeGreaterThan(initialCalls));
    await waitFor(() =>
      expect(
        screen.queryByText("ba:salvador:farol-da-barra"),
      ).not.toBeInTheDocument(),
    );

    server.events.removeListener("request:start", countListCalls);
  });

  it("surfaces a 401 without throwing (session-expired path)", async () => {
    const user = userEvent.setup();
    server.use(...gateUnauthorized());
    const client = makeClient();
    render(
      <QueryClientProvider client={client}>
        <ApproveButton rioId={sampleGateItems[0].rio_id} />
      </QueryClientProvider>,
    );
    await user.click(screen.getByText("aprovar"));
    // Mutation settles (no unhandled rejection) — error surfaces via toast.
    await waitFor(() =>
      expect(screen.getByText("aprovar")).toBeInTheDocument(),
    );
  });
});

describe("useRejectGate", () => {
  it("rejects via the existing endpoint and refetches", async () => {
    const user = userEvent.setup();
    const onSettled = vi.fn();
    server.use(gateRejectSuccess());
    const client = makeClient();
    render(
      <QueryClientProvider client={client}>
        <RejectButton rioId={sampleGateItems[0].rio_id} />
      </QueryClientProvider>,
    );
    await user.click(screen.getByText("rejeitar"));
    await waitFor(() => {
      onSettled();
      expect(onSettled).toHaveBeenCalled();
    });
  });
});

describe("/gate page — master-detail + destructive reject AlertDialog", () => {
  it("renders the queue and approve/reject after selecting a row (success state)", async () => {
    const user = userEvent.setup();
    server.use(gateListSuccess(), rampContextSuccess());
    renderWithClient(<GatePage />);

    await user.click(await screen.findByText("ba:salvador:farol-da-barra"));

    expect(
      screen.getByRole("button", { name: "Aprovar contato" }),
    ).toBeInTheDocument();
    // Reject is gated by a shadcn AlertDialog — the confirm copy appears on open.
    await user.click(screen.getByRole("button", { name: "Rejeitar" }));
    expect(screen.getByText("Rejeitar atrativo?")).toBeInTheDocument();
  });

  it("shows the empty gate state", async () => {
    server.use(gateListEmpty(), rampContextSuccess());
    renderWithClient(<GatePage />);
    expect(await screen.findByText("Fila de gate vazia")).toBeInTheDocument();
  });

  it("shows the queue error state", async () => {
    server.use(gateListError(500), rampContextSuccess());
    renderWithClient(<GatePage />);
    expect(
      await screen.findByText("Não foi possível carregar"),
    ).toBeInTheDocument();
  });

  it("shows the 401 session-expired state", async () => {
    server.use(...gateUnauthorized());
    renderWithClient(<GatePage />);
    expect(
      await screen.findByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
  });
});
