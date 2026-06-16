import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { setOperatorToken } from "@/lib/api-client";

/**
 * Render a component inside a fresh TanStack QueryClient (retries off so error
 * states surface immediately) with an operator token set (so `apiFetch` attaches
 * the Bearer the BFF/MSW expects). Each call gets its own client for isolation.
 */
export function renderWithClient(ui: ReactElement) {
  setOperatorToken("test-operator-token");
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
    ),
  };
}

export function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}
