import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

import { setOperatorToken } from "@/lib/api-client";

/**
 * Render a CMS component inside a fresh TanStack QueryClient with retries
 * disabled (so error states surface immediately) and an operator token set.
 * Each call gets its own QueryClient to keep suites isolated.
 */
export function renderWithClient(ui: ReactElement) {
  setOperatorToken("test-operator-token");
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

export function ClientWrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}
