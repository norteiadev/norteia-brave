"use client";

import { useState } from "react";

import { QueueList } from "@/components/dlq/QueueList";
import { ReviewPanel } from "@/components/dlq/ReviewPanel";
import {
  useDescarteDlqRecord,
  useReprocessDlqRecord,
  useValidateDlqRecord,
} from "@/components/dlq/dlq-actions";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { UF_PRIORITY, type DlqDetail } from "@/lib/dlq-api";

/**
 * /dlq — the DLQ master-detail review surface (DASH-01, UI-SPEC D-06).
 *
 * QueueList (state-filtered master) ↔ ReviewPanel (detail), separated by the
 * xl layout gap (UI-SPEC). The DLQ action bar (approve / reject / reprocess) is
 * injected into the otherwise action-agnostic ReviewPanel so plan-05's gate can
 * reuse the same panel with a different action set.
 */
export default function DlqPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // UF context for optimistic validate (must match the QueueList list key).
  const [uf] = useState<string>(UF_PRIORITY[0]);
  const entityType = "destination";

  const validate = useValidateDlqRecord(uf, entityType);
  const descarte = useDescarteDlqRecord();
  const reprocess = useReprocessDlqRecord();

  return (
    <main className="flex h-dvh flex-col gap-4 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-[20px] font-semibold">DLQ Review</h1>
        <span className="text-[12px] text-muted-foreground">
          Revisão batch-by-state · Nascente → Rio → Mar
        </span>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-8 xl:grid-cols-[minmax(360px,1fr)_2fr]">
        <section className="min-h-0 overflow-hidden">
          <QueueList
            selectedId={selectedId}
            onSelect={setSelectedId}
            entityType={entityType}
          />
        </section>

        <section className="min-h-0 overflow-hidden rounded-md border">
          <ReviewPanel
            rioId={selectedId}
            actions={(detail: DlqDetail) => (
              <DlqActions
                detail={detail}
                onValidate={() => validate.mutate(detail.id)}
                onReprocess={() => reprocess.mutate(detail.id)}
                onDescarte={() => descarte.mutate(detail.id)}
                pending={
                  validate.isPending ||
                  descarte.isPending ||
                  reprocess.isPending
                }
              />
            )}
          />
        </section>
      </div>
    </main>
  );
}

function DlqActions({
  detail,
  onValidate,
  onReprocess,
  onDescarte,
  pending,
}: {
  detail: DlqDetail;
  onValidate: () => void;
  onReprocess: () => void;
  onDescarte: () => void;
  pending: boolean;
}) {
  return (
    <>
      {/* Primary action (blue --primary): validate & publish → Mar */}
      <Button size="sm" disabled={pending} onClick={onValidate}>
        Validar e publicar
      </Button>

      {/* Edit→re-score / reprocess */}
      <Button
        size="sm"
        variant="outline"
        disabled={pending}
        onClick={onReprocess}
      >
        Salvar e reprocessar
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={pending}
        onClick={onReprocess}
      >
        Reprocessar
      </Button>

      {/* Destructive reject → descarte, behind an AlertDialog confirm */}
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button size="sm" variant="destructive" disabled={pending}>
            Rejeitar
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Rejeitar registro?</AlertDialogTitle>
            <AlertDialogDescription>
              Este registro vai para descarte e não será publicado no Mar. Ação
              reversível só via reprocessamento.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={onDescarte}
            >
              Rejeitar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <span className="ml-auto font-mono text-[12px] text-muted-foreground tabular-nums">
        {detail.id.slice(0, 8)}
      </span>
    </>
  );
}
