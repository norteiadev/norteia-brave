"use client";

import { ApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { useMonitor } from "./useMonitor";

/**
 * MonitorTiles (DASH-02, UI-SPEC).
 *
 * Per-layer volume rendered as Display-size numerals (28px tabular-nums) with the
 * approval/rejection/DLQ rate captions underneath. Polls the monitor endpoint live
 * via `useMonitor` (TanStack `refetchInterval`). The status colors here are
 * data-encoding (green/amber/red) kept OFF the 10% accent budget per UI-SPEC — they
 * only tint small numerals/captions, never large fills.
 *
 * View states: Skeleton tiles while loading; "Sem dados no período" on an empty
 * window; "Não foi possível carregar" + retry on error; the session-expired copy on
 * a 401.
 */
export function MonitorTiles({ sinceHours = 24 }: { sinceHours?: number }) {
  const { data, isPending, isError, error, refetch } = useMonitor(sinceHours);

  if (isPending) {
    return (
      <div
        data-testid="monitor-tiles-skeleton"
        className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6"
      >
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-24 rounded-lg" />
        ))}
      </div>
    );
  }

  if (isError) {
    if (error instanceof ApiError && error.status === 401) {
      return (
        <p className="text-[14px] text-muted-foreground">
          Sessão expirada ou token inválido
        </p>
      );
    }
    return (
      <div className="flex flex-col items-start gap-2">
        <p className="text-[14px] text-muted-foreground">
          Não foi possível carregar
        </p>
        <Button size="sm" variant="outline" onClick={() => refetch()}>
          Tentar novamente
        </Button>
      </div>
    );
  }

  const { volume, rates, throughput } = data;
  const empty =
    volume.nascente_count === 0 &&
    volume.mar_count === 0 &&
    throughput === 0 &&
    Object.values(volume.rio_count).every((v) => v === 0);

  if (empty) {
    return (
      <p className="text-[14px] text-muted-foreground">Sem dados no período</p>
    );
  }

  const pct = (v: number) => `${Math.round(v * 100)}%`;

  return (
    <div
      data-testid="monitor-tiles"
      className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6"
    >
      <VolumeTile label="Nascente" value={volume.nascente_count} />
      <VolumeTile label="Rio · em curso" value={volume.rio_count.in_progress} />
      <VolumeTile
        label="Mar"
        value={volume.mar_count}
        className="text-emerald-500"
      />
      <VolumeTile
        label="DLQ"
        value={volume.rio_count.dlq}
        className="text-amber-500"
      />
      <VolumeTile
        label="Descarte"
        value={volume.rio_count.descarte}
        className="text-destructive"
      />
      <VolumeTile label="Throughput" value={throughput} caption="período" />

      {/* AuditLog-derived rate captions — the DASH-02 audit coverage. */}
      <div className="col-span-full flex flex-wrap gap-6 pt-1 text-[12px] text-muted-foreground">
        <RateCaption
          label="Aprovação"
          value={pct(rates.dlq_validated)}
          className="text-emerald-500"
        />
        <RateCaption
          label="Rejeição"
          value={pct(rates.dlq_rejected)}
          className="text-destructive"
        />
        <RateCaption
          label="Reprocesso"
          value={pct(rates.dlq_reprocessed)}
          className="text-amber-500"
        />
      </div>
    </div>
  );
}

function VolumeTile({
  label,
  value,
  caption,
  className,
}: {
  label: string;
  value: number;
  caption?: string;
  className?: string;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border bg-card p-4">
      <span className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span
        className={`text-[28px] font-semibold leading-none tabular-nums ${className ?? ""}`}
      >
        {value.toLocaleString("pt-BR")}
      </span>
      {caption ? (
        <span className="text-[12px] text-muted-foreground">{caption}</span>
      ) : null}
    </div>
  );
}

function RateCaption({
  label,
  value,
  className,
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="uppercase tracking-wide">{label}</span>
      <span className={`font-semibold tabular-nums ${className ?? ""}`}>
        {value}
      </span>
    </span>
  );
}
