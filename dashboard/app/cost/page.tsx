"use client";

import { useState } from "react";

import { CostByLaneChart } from "@/components/cost/CostByLaneChart";
import { CostByModelChart } from "@/components/cost/CostByModelChart";
import { CostSummary } from "@/components/cost/CostSummary";
import { COST_WINDOWS, type CostWindowHours } from "@/lib/cost-api";
import { Button } from "@/components/ui/button";

/**
 * /cost — the Cost & LLM view (DASH-04).
 *
 * Spend per lane and per model from `llm_generations`, plus a totals summary. A
 * time-window selector (24h / 7d / 30d / Tudo) drives a refetch of all three
 * surfaces through the shared `useCost` hook (keyed by group-by + window). Each
 * surface reads the read-only `GET /api/v1/cost` aggregate through the BFF — no
 * pipeline logic, aggregate USD/token sums only (no PII). Empty windows render
 * "Sem dados no período" rather than crashing.
 */
export default function CostPage() {
  const [windowHours, setWindowHours] = useState<CostWindowHours>(24 * 7);

  return (
    <main className="flex min-h-dvh flex-col gap-6 p-6">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-[20px] font-semibold">Custo &amp; LLM</h1>
        <div className="flex items-center gap-1" role="group" aria-label="Janela de tempo">
          {COST_WINDOWS.map((w) => (
            <Button
              key={w.label}
              size="sm"
              variant={w.hours === windowHours ? "default" : "outline"}
              onClick={() => setWindowHours(w.hours)}
            >
              {w.label}
            </Button>
          ))}
        </div>
      </header>

      <section className="rounded-md border p-4">
        <h2 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
          Resumo
        </h2>
        <CostSummary windowHours={windowHours} />
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="rounded-md border p-4">
          <h2 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
            Gasto por lane
          </h2>
          <CostByLaneChart windowHours={windowHours} />
        </section>

        <section className="rounded-md border p-4">
          <h2 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
            Gasto por modelo
          </h2>
          <CostByModelChart windowHours={windowHours} />
        </section>
      </div>
    </main>
  );
}
