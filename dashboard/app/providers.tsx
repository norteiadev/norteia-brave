"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { useState, type ReactNode } from "react";

/**
 * Client-side providers (D-04).
 *
 * TanStack Query `QueryClientProvider` is the server-state cache for every
 * dashboard view (DLQ queue, monitor polling, gate). Per RESEARCH §5 the client
 * gets a singleton QueryClient (held in `useState` so it is created once per
 * mount and survives re-renders) while the server creates one per request.
 *
 * `next-themes` provides the dark-default theme (UI-SPEC: dark for the 24/7 ops
 * console). `defaultTheme="dark"` + `enableSystem={false}` locks the console to
 * the dark ops palette unless the operator explicitly toggles.
 */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Ops dashboards re-fetch on focus; data is short-lived.
            staleTime: 30_000,
            retry: 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider
        attribute="class"
        defaultTheme="dark"
        enableSystem={false}
        disableTransitionOnChange
      >
        {children}
      </ThemeProvider>
    </QueryClientProvider>
  );
}
