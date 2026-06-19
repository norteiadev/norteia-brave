"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { FailuresPanel } from "@/components/processo/FailuresPanel";
import { WorkerBoard } from "@/components/processo/WorkerBoard";
import { Skeleton } from "@/components/ui/skeleton";
import { dlqKeys, fetchDlqList } from "@/lib/dlq-api";
import { fetchGateQueue, gateKeys } from "@/lib/gate-api";
import { WORKERS_REFETCH_INTERVAL_MS } from "@/lib/workers-api";

/**
 * /processo — process-observability page (D-05, D-06).
 *
 * Composites:
 *   1. WorkerBoard — live-polled (10s) Celery worker tiles + queue depths
 *   2. Human-pending tiles — DLQ count + WhatsApp gate count
 *   3. Stage funnel — Recharts BarChart of atrativos by sub_state (from gate queue)
 *   4. FailuresPanel — PoisonQuarantine recent failures
 *
 * Human-pending counts use the existing DLQ and gate endpoints respectively —
 * these are the correct backing data sources (plan D-05, interfaces section).
 *
 * The stage funnel derives from the atrativos gate queue: all atrativos in
 * `aguardando_consulta_whatsapp` are counted (this is the most actionable
 * sub_state operators care about at the gate). Full FSM-stage distribution
 * is available via the /atrativos CMS (plan 08-05).
 *
 * Note: this page intentionally does NOT import from destinos-api.ts or
 * atrativos-api.ts — those are built by plans 08-04/08-05 in the same wave.
 * Existing endpoints (dlq.py + atrativos_gate.py) provide the required counts.
 */
export default function ProcessoPage() {
  // DLQ pending count — uses existing DLQ list endpoint
  const {
    data: dlqItems,
    isPending: dlqPending,
  } = useQuery({
    queryKey: dlqKeys.list(),
    queryFn: () => fetchDlqList(undefined, undefined, 500),
    refetchInterval: WORKERS_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });

  // Gate pending count — atrativos in aguardando_consulta_whatsapp
  const {
    data: gateItems,
    isPending: gatePending,
  } = useQuery({
    queryKey: gateKeys.list(),
    queryFn: () => fetchGateQueue(undefined, 500),
    refetchInterval: WORKERS_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });

  const dlqTotal = dlqItems?.length ?? null;
  const gateTotal = gateItems?.length ?? null;

  // Stage funnel — group gate items by sub_state to show pipeline distribution
  const funnelData = buildFunnelData(gateItems ?? []);

  return (
    <main className="flex min-h-dvh flex-col gap-6 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-[20px] font-semibold">Processo Brave</h1>
        <span className="text-[12px] text-muted-foreground">
          Workers · falhas · fila humana · funil · 10s
        </span>
      </header>

      {/* WorkerBoard */}
      <section>
        <WorkerBoard />
      </section>

      {/* Human-pending tiles */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <HumanPendingTile
          label="DLQ pendente"
          count={dlqTotal}
          isPending={dlqPending}
          href="/dlq"
          testId="tile-dlq-pending"
        />
        <HumanPendingTile
          label="Gate WhatsApp"
          count={gateTotal}
          isPending={gatePending}
          href="/gate"
          testId="tile-gate-pending"
        />
      </div>

      {/* Funnel + Failures layout */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_1fr]">
        {/* Stage funnel chart */}
        <section className="rounded-md border p-4">
          <h2 className="mb-4 text-[14px] font-semibold">
            Funil Atrativos por Sub-Estado
          </h2>
          {funnelData.length === 0 ? (
            <p className="text-[14px] text-muted-foreground">
              Sem dados de funil
            </p>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={funnelData}>
                <XAxis
                  dataKey="stage"
                  tick={{ fontSize: 11 }}
                  interval={0}
                />
                <YAxis />
                <Tooltip />
                <Bar dataKey="count" fill="var(--color-primary)" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </section>

        {/* FailuresPanel */}
        <section>
          <FailuresPanel />
        </section>
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface HumanPendingTileProps {
  label: string;
  count: number | null;
  isPending: boolean;
  href: string;
  testId: string;
}

function HumanPendingTile({
  label,
  count,
  isPending,
  href,
  testId,
}: HumanPendingTileProps) {
  return (
    <a
      href={href}
      data-testid={testId}
      aria-label={`${label}: ${count ?? "carregando"}`}
      className="flex flex-col gap-1 rounded-lg border bg-card p-4 transition-colors hover:bg-accent/50"
    >
      <span className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {isPending ? (
        <Skeleton className="h-8 w-12 rounded" />
      ) : (
        <span className="text-[28px] font-semibold leading-none tabular-nums">
          {count ?? "—"}
        </span>
      )}
    </a>
  );
}

// ---------------------------------------------------------------------------
// Funnel helpers
// ---------------------------------------------------------------------------

/** FSM sub_state → display label (ordered by pipeline progression). */
const FUNNEL_STAGES: Array<{ key: string; label: string }> = [
  { key: "discovered", label: "Descoberto" },
  { key: "contacts_found", label: "Contatos" },
  { key: "signals_gathered", label: "Sinais" },
  { key: "aguardando_consulta_whatsapp", label: "Gate WA" },
  { key: "whatsapp_in_progress", label: "Outreach" },
];

interface FunnelRow {
  stage: string;
  count: number;
}

function buildFunnelData(
  items: Array<{ sub_state?: string | null }>,
): FunnelRow[] {
  if (items.length === 0) return [];

  const counts: Record<string, number> = {};
  for (const item of items) {
    if (item.sub_state) {
      counts[item.sub_state] = (counts[item.sub_state] ?? 0) + 1;
    }
  }

  return FUNNEL_STAGES.filter(
    ({ key }) => (counts[key] ?? 0) > 0,
  ).map(({ key, label }) => ({
    stage: label,
    count: counts[key] ?? 0,
  }));
}
