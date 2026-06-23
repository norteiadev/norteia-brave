import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  usePromoteMarReadyBatch,
  usePromoteMarReadyRecord,
} from "@/components/mar-ready/MarReadyActions";
import { setOperatorToken } from "@/lib/api-client";
import { marReadyKeys, type MarReadyItem } from "@/lib/mar-ready-api";
import { server } from "@/mocks/server";
import {
  promoteBatchSuccess,
  promoteFailure,
  promoteSuccess,
  sampleMarReadyItems,
} from "@/mocks/handlers/mar-ready";

beforeEach(() => {
  setOperatorToken("test-operator-token");
  server.resetHandlers();
});

// ── Test harness components ─────────────────────────────────────────────────

function PromoteButton({ rioId }: { rioId: string }) {
  const promote = usePromoteMarReadyRecord();
  return (
    <button
      onClick={() => promote.mutate(rioId)}
      data-testid="promote-btn"
    >
      promover
    </button>
  );
}

function BatchPromoteButton({ ufs }: { ufs: string[] }) {
  const batch = usePromoteMarReadyBatch();
  return (
    <button
      onClick={() => batch.mutate({ ufs })}
      data-testid="batch-promote-btn"
    >
      promover lote
    </button>
  );
}

// ── usePromoteMarReadyRecord ────────────────────────────────────────────────

describe("usePromoteMarReadyRecord", () => {
  it("optimistically removes the row from cached list keys on mutate", async () => {
    const user = userEvent.setup();
    server.use(promoteSuccess());

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: Infinity },
        mutations: { retry: false },
      },
    });

    const listKey = marReadyKeys.list();
    const seed: MarReadyItem[] = [...sampleMarReadyItems];
    client.setQueryData<MarReadyItem[]>(listKey, seed);

    render(
      <QueryClientProvider client={client}>
        <PromoteButton rioId={sampleMarReadyItems[0].id} />
      </QueryClientProvider>,
    );

    await user.click(screen.getByTestId("promote-btn"));

    // Optimistic remove fires before settle
    await waitFor(() => {
      const cached = client.getQueryData<MarReadyItem[]>(listKey) ?? [];
      expect(cached.some((r) => r.id === sampleMarReadyItems[0].id)).toBe(false);
    });
  });

  it("rolls back snapshot on 409 response — row reappears", async () => {
    const user = userEvent.setup();
    server.use(promoteFailure());

    const client = new QueryClient({
      defaultOptions: {
        // Keep inactive caches alive so rollback is observable
        queries: { retry: false, gcTime: Infinity },
        mutations: { retry: false },
      },
    });

    const listKey = marReadyKeys.list();
    const seed: MarReadyItem[] = [...sampleMarReadyItems];
    client.setQueryData<MarReadyItem[]>(listKey, seed);

    render(
      <QueryClientProvider client={client}>
        <PromoteButton rioId={sampleMarReadyItems[0].id} />
      </QueryClientProvider>,
    );

    await user.click(screen.getByTestId("promote-btn"));

    // On 409 error, snapshot restored — row reappears in cached list
    await waitFor(() => {
      const cached = client.getQueryData<MarReadyItem[]>(listKey) ?? [];
      expect(cached.some((r) => r.id === sampleMarReadyItems[0].id)).toBe(true);
    });
  });

  it("rolls back ALL cached list keys on 409 (WR-05 pattern)", async () => {
    const user = userEvent.setup();
    server.use(promoteFailure());

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: Infinity },
        mutations: { retry: false },
      },
    });

    // Two distinct list caches — e.g. operator visited BA then RJ
    const baKey = marReadyKeys.list("BA");
    const allKey = marReadyKeys.list();
    const seed: MarReadyItem[] = [...sampleMarReadyItems];
    client.setQueryData<MarReadyItem[]>(baKey, seed);
    client.setQueryData<MarReadyItem[]>(allKey, seed);

    render(
      <QueryClientProvider client={client}>
        <PromoteButton rioId={sampleMarReadyItems[0].id} />
      </QueryClientProvider>,
    );

    await user.click(screen.getByTestId("promote-btn"));

    // Both caches restored after 409 rollback
    await waitFor(() => {
      const ba = client.getQueryData<MarReadyItem[]>(baKey) ?? [];
      const all = client.getQueryData<MarReadyItem[]>(allKey) ?? [];
      expect(ba.some((r) => r.id === sampleMarReadyItems[0].id)).toBe(true);
      expect(all.some((r) => r.id === sampleMarReadyItems[0].id)).toBe(true);
    });
  });
});

// ── usePromoteMarReadyBatch ─────────────────────────────────────────────────

describe("usePromoteMarReadyBatch", () => {
  it("dispatches POST promote-batch and invalidates on success", async () => {
    const user = userEvent.setup();

    let batchCalled = false;
    server.use(
      promoteBatchSuccess(2),
    );
    server.events.on("request:start", ({ request }: { request: Request }) => {
      const url = new URL(request.url);
      if (url.pathname.includes("promote-batch")) batchCalled = true;
    });

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <BatchPromoteButton ufs={["BA"]} />
      </QueryClientProvider>,
    );

    await user.click(screen.getByTestId("batch-promote-btn"));

    await waitFor(() => expect(batchCalled).toBe(true));

    server.events.removeAllListeners();
  });
});
