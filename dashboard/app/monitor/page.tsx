"use client";

import { AlertsPanel } from "@/components/monitor/AlertsPanel";
import { MonitorTiles } from "@/components/monitor/MonitorTiles";
import { ThroughputChart } from "@/components/monitor/ThroughputChart";

/**
 * /monitor — the Brave monitor (DASH-02, §15.7).
 *
 * Volume tiles (per-layer counts + approval/rejection/DLQ rate captions) above a
 * Recharts throughput chart and the failure-alerts panel. All three read the single
 * `GET /api/v1/monitor` poll (TanStack `refetchInterval`, 10s) through the shared
 * `useMonitor` hook — one network poll feeds the whole page. No WebSocket this
 * milestone (CONTEXT deferred to a later slice).
 */
export default function MonitorPage() {
  return (
    <main className="flex min-h-dvh flex-col gap-6 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-[20px] font-semibold">Brave Monitor</h1>
        <span className="text-[12px] text-muted-foreground">
          Nascente → Rio → Mar · atualização ao vivo (10s)
        </span>
      </header>

      <MonitorTiles />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_1fr]">
        <section className="rounded-md border p-4">
          <h2 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
            Throughput por camada
          </h2>
          <ThroughputChart />
        </section>

        <section>
          <AlertsPanel />
        </section>
      </div>
    </main>
  );
}
