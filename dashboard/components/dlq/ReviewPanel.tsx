"use client";

import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ScoreBreakdownPanel } from "@/components/dlq/ScoreBreakdownPanel";
import { StatusBadge } from "@/components/dlq/StatusBadge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { dlqKeys, fetchDlqDetail, type DlqDetail } from "@/lib/dlq-api";

/**
 * ReviewPanel — the master-detail RIGHT pane (UI-SPEC D-06).
 *
 * Fetches `GET /api/v1/dlq/{rio_id}` through the BFF via TanStack Query and
 * renders: Nascente raw payload (Geist Mono JSON), Rio normalized, the signature
 * `ScoreBreakdownPanel` (§7.6), signals, and the WhatsApp/steward log.
 *
 * Deliberately ACTION-AGNOSTIC: the action bar is injected via `actions` so the
 * plan-05 WhatsApp gate can reuse this exact panel with its own action set. The
 * panel owns rendering + the fetch lifecycle (loading skeleton / error / 401 /
 * empty) only.
 *
 * View states (all MSW-covered, D-07):
 *  - loading  → shadcn Skeleton (UI-SPEC: never a blank flash)
 *  - 401      → "Sessão expirada ou token inválido"
 *  - error    → "Não foi possível carregar" + "Tentar novamente" (refetch)
 *  - empty    → no record selected hint
 */
export function ReviewPanel({
  rioId,
  actions,
}: {
  rioId: string | null;
  /** Injected action bar (DLQ actions, or the gate's actions when reused). */
  actions?: (detail: DlqDetail) => ReactNode;
}) {
  const query = useQuery({
    queryKey: rioId ? dlqKeys.detail(rioId) : ["dlq", "detail", "none"],
    queryFn: () => fetchDlqDetail(rioId as string),
    enabled: rioId != null,
  });

  if (rioId == null) {
    return (
      <div className="flex h-full items-center justify-center p-12 text-center text-[14px] text-muted-foreground">
        Selecione um registro na fila para revisar.
      </div>
    );
  }

  if (query.isLoading) {
    return (
      <div className="flex flex-col gap-4 p-6" data-testid="review-skeleton">
        <Skeleton className="h-7 w-48" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (query.isError) {
    const status =
      query.error instanceof ApiError ? query.error.status : undefined;

    if (status === 401) {
      return (
        <div className="flex flex-col items-center justify-center gap-2 p-12 text-center">
          <h3 className="text-[20px] font-semibold">
            Sessão expirada ou token inválido
          </h3>
          <p className="text-[14px] text-muted-foreground">
            Faça login novamente para continuar.
          </p>
        </div>
      );
    }

    return (
      <div className="flex flex-col items-center justify-center gap-3 p-12 text-center">
        <h3 className="text-[20px] font-semibold">Não foi possível carregar</h3>
        <p className="text-[14px] text-muted-foreground">
          Falha ao consultar a API ({status ?? "rede"}). Verifique se o serviço
          Brave está no ar e tente novamente.
        </p>
        <Button variant="outline" size="sm" onClick={() => query.refetch()}>
          Tentar novamente
        </Button>
      </div>
    );
  }

  const detail = query.data as DlqDetail;

  return (
    <div className="flex h-full flex-col gap-6 overflow-auto p-6">
      <header className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h2 className="text-[20px] font-semibold leading-tight">
            DLQ Review
          </h2>
          <span className="font-mono text-[12px] text-muted-foreground tabular-nums">
            {detail.id || "—"}
          </span>
        </div>
        <StatusBadge routing={detail.routing} subState={detail.sub_state} />
      </header>

      <ScoreBreakdownPanel breakdown={detail.score_breakdown} score={detail.score} />

      <Separator />

      <Section title="Rio normalizado">
        <JsonBlock value={detail.normalized} />
      </Section>

      <Section title="Nascente (payload bruto)">
        <JsonBlock value={detail.nascente_payload} />
      </Section>

      <Section title="Sinais">
        <JsonBlock value={detail.signals} />
      </Section>

      <Section title="Log WhatsApp / steward">
        {detail.whatsapp_log.length === 0 ? (
          <p className="text-[14px] text-muted-foreground">
            Nenhum evento registrado.
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {detail.whatsapp_log.map((entry) => (
              <li
                key={entry.id}
                className="flex items-baseline gap-2 font-mono text-[12px] tabular-nums"
              >
                <span className="text-muted-foreground">
                  {entry.created_at ?? "—"}
                </span>
                <span className="font-semibold">{entry.action}</span>
                {entry.actor ? (
                  <span className="text-muted-foreground">· {entry.actor}</span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {actions ? (
        <>
          <Separator />
          <div className="flex flex-wrap items-center gap-2">
            {actions(detail)}
          </div>
        </>
      ) : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 font-mono text-[12px] leading-relaxed">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
