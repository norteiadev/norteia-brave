import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { setOperatorToken } from "@/lib/api-client";
import { BR_UFS, computeMetric, usePainelMetrics } from "@/lib/painel-data";
import { atrativosListSuccess, sampleAtrativos } from "@/mocks/handlers/atrativos";
import { destinosListSuccess } from "@/mocks/handlers/destinos";
import { nascenteList } from "@/mocks/handlers/engine";
import { server } from "@/mocks/server";

beforeEach(() => {
  server.resetHandlers();
});

describe("computeMetric", () => {
  it("computes total/mar/falha and rounded pct", () => {
    expect(computeMetric(2, 1, 0)).toEqual({
      total: 2,
      mar: 1,
      falha: 0,
      pct: 50,
    });
  });

  it("guards division by zero (pct = 0 when total = 0)", () => {
    expect(computeMetric(0, 0, 0).pct).toBe(0);
  });

  it("rounds the percentage", () => {
    expect(computeMetric(3, 1, 0).pct).toBe(33); // 33.33 → 33
    expect(computeMetric(3, 2, 0).pct).toBe(67); // 66.67 → 67
  });
});

describe("BR_UFS", () => {
  it("exports the 27 BR UF codes", () => {
    expect(BR_UFS).toHaveLength(27);
    expect(BR_UFS).toContain("SP");
    expect(BR_UFS).toContain("DF");
  });
});

// --- Hooks (GREEN, over MSW) ---

function hookWrapper() {
  setOperatorToken("test-operator-token");
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return function HookWrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

describe("usePainelMetrics", () => {
  it("derives truthful per-entity metrics from envelope totals + nascente from the nascente list total", async () => {
    server.use(
      destinosListSuccess(),
      atrativosListSuccess(),
      nascenteList([], 9),
    );
    const { result } = renderHook(() => usePainelMetrics(), {
      wrapper: hookWrapper(),
    });

    await waitFor(() => expect(result.current.isPending).toBe(false));

    // sampleDestinos: 1 mar + 1 dlq → total 2, mar 1, descarte 0
    expect(result.current.destino.total).toBe(2);
    expect(result.current.destino.mar).toBe(1);
    expect(result.current.destino.falha).toBe(0);
    expect(result.current.destino.pct).toBe(50);

    // sampleAtrativos: 2 items (the list envelope total is the server count)
    expect(result.current.atrativo.total).toBe(sampleAtrativos.length);

    // Nascente COLUMN count comes from the /nascente envelope total
    expect(result.current.nascenteCount).toBe(9);
  });

  it("scopes the count queries to the selected UF (?uf=DF)", async () => {
    const seen = new Set<string | null>();
    const capture = (path: string) =>
      http.get(`http://localhost:3000/api/api/v1/${path}`, ({ request }) => {
        seen.add(new URL(request.url).searchParams.get("uf"));
        return HttpResponse.json({ items: [], total: 0, offset: 0, limit: 1 });
      });
    server.use(capture("destinos"), capture("atrativos"), capture("nascente"));
    const { result } = renderHook(() => usePainelMetrics("DF"), {
      wrapper: hookWrapper(),
    });
    await waitFor(() => expect(result.current.isPending).toBe(false));
    // Every metric fetch carried ?uf=DF — no whole-base ("null") request slipped through.
    expect([...seen]).toEqual(["DF"]);
  });
});
