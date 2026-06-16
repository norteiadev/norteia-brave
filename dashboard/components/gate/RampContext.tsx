"use client";

import { useQuery } from "@tanstack/react-query";

import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import {
  fetchRampContext,
  gateKeys,
  type RampQualityContext,
} from "@/lib/gate-api";

/**
 * RampContext — the WhatsApp send-path context panel (DASH-03).
 *
 * Shows, before the operator approves outreach:
 *  - the enforced VOLUME RAMP (remaining cap / used / cap) — enforced server-side
 *    in the Phase 3 send path (T-04-20); the UI only DISPLAYS it, no bypass.
 *  - the WhatsApp QUALITY-RATING state (GREEN / AMBER|YELLOW / RED). RED gets the
 *    destructive treatment (UI-SPEC §destructive: RED quality flag is a failure
 *    state) and announces that sends are auto-paused server-side.
 *
 * Fetched via TanStack Query under the shared ['gate'] key prefix so a gate
 * mutation's `invalidateQueries(['gate'])` also refreshes this context.
 */
export function RampContext() {
  const query = useQuery({
    queryKey: gateKeys.context(),
    queryFn: fetchRampContext,
  });

  if (query.isLoading) {
    return (
      <div className="flex flex-col gap-2 p-4" data-testid="ramp-skeleton">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  }

  if (query.isError || !query.data) {
    // WR-04: a 401 is NOT a benign advisory gap — it means the operator's
    // session expired. Surface the same session-expired UI the sibling views
    // (GateQueue / QueueList / ConversationList) show, so the operator is driven
    // to re-login instead of silently approving outreach against a blank panel
    // (the panel's whole purpose is showing the RED auto-pause state).
    const status =
      query.error instanceof ApiError ? query.error.status : undefined;
    if (status === 401) {
      return (
        <div className="flex flex-col items-center gap-1 rounded-md border p-4 text-center">
          <h3 className="text-[13px] font-semibold">
            Sessão expirada ou token inválido
          </h3>
          <p className="text-[12px] text-muted-foreground">
            Faça login novamente para continuar.
          </p>
        </div>
      );
    }
    // Non-auth advisory failure — keep the dashed fallback so it does not block
    // the queue.
    return (
      <div className="rounded-md border border-dashed p-4 text-[12px] text-muted-foreground">
        Contexto de ramp/qualidade indisponível.
      </div>
    );
  }

  const ctx = query.data;
  return <RampContextView ctx={ctx} />;
}

/** Pure presentational split so it can be unit-tested without the network. */
export function RampContextView({ ctx }: { ctx: RampQualityContext }) {
  const rating = (ctx.quality_rating ?? "GREEN").toUpperCase();
  const isRed = rating === "RED";
  const isAmber = rating === "AMBER" || rating === "YELLOW";

  return (
    <section
      aria-label="Contexto de ramp e qualidade WhatsApp"
      className={cn(
        "flex flex-col gap-3 rounded-md border p-4",
        isRed && "border-destructive bg-destructive/5",
      )}
    >
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
          Ramp & qualidade WhatsApp
        </h3>
        <span
          data-testid="quality-rating"
          className={cn(
            "rounded px-2 py-0.5 font-mono text-[11px] font-semibold tabular-nums",
            isRed && "bg-destructive text-white",
            isAmber && "bg-[oklch(0.75_0.16_80)] text-black",
            !isRed && !isAmber && "bg-[oklch(0.65_0.17_150)] text-white",
          )}
        >
          {rating}
        </span>
      </header>

      {isRed ? (
        <p className="text-[13px] font-medium text-destructive">
          Qualidade RED — envios pausados automaticamente. Aprovar contatos não
          dispara saída até a qualidade se recuperar.
        </p>
      ) : null}

      <dl className="grid grid-cols-3 gap-2 text-[12px]">
        <RampStat label="restante" value={ctx.ramp_remaining} />
        <RampStat label="usado" value={ctx.ramp_used} />
        <RampStat label="cap" value={ctx.ramp_cap} />
      </dl>

      {ctx.paused ? (
        <p className="text-[12px] text-muted-foreground">
          Send-path pausado pelo gate de compliance (Fase 3).
        </p>
      ) : null}
    </section>
  );
}

function RampStat({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex flex-col">
      <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="font-mono text-[16px] font-semibold tabular-nums">
        {value != null ? value : "—"}
      </dd>
    </div>
  );
}
