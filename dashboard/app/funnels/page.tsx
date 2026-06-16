import { FunnelChart } from "@/components/funnels/FunnelChart";

/**
 * /funnels — the territorial funnel view (DASH-05).
 *
 * Stage bars (ingerido → em progresso → mar/dlq/descarte) for destinos &
 * atrativos, filterable by lane (entity_type) and UF, read from the read-only
 * `GET /api/v1/funnels` aggregate through the BFF. Aggregate counts only — no
 * PII. Empty windows render "Sem dados no período".
 */
export default function FunnelsPage() {
  return (
    <main className="flex min-h-dvh flex-col gap-6 p-6">
      <header>
        <h1 className="text-[20px] font-semibold">Funis</h1>
        <p className="text-[12px] text-muted-foreground">
          Contagem por estágio do pipeline (ingerido → mar/dlq/descarte) por UF
          e lane.
        </p>
      </header>

      <section className="rounded-md border p-4">
        <FunnelChart />
      </section>
    </main>
  );
}
