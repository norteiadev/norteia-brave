"use client";

import { useState } from "react";

import { GateQueue } from "@/components/gate/GateQueue";
import { GateReviewPanel } from "@/components/gate/GateReviewPanel";
import { useApproveGate, useRejectGate } from "@/components/gate/gate-actions";
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
import type { GateQueueItem } from "@/lib/gate-api";

/**
 * /gate — the WhatsApp gate master-detail surface (DASH-03, UI-SPEC D-06).
 *
 * GateQueue (state-filtered master, aguardando_consulta_whatsapp) ↔
 * GateReviewPanel (detail + RampContext), separated by the xl layout gap. The
 * gate action bar ("Aprovar contato" / "Rejeitar atrativo" behind the
 * destructive AlertDialog) is injected into the action-agnostic panel — the same
 * scaffold shape reused from the DLQ slice (D-06).
 *
 * Both actions go through the BFF to the EXISTING atrativos_gate.py endpoints; no
 * new mutations. The ramp is enforced server-side (T-04-20) — the UI shows
 * context only.
 */
export default function GatePage() {
  const [selected, setSelected] = useState<GateQueueItem | null>(null);

  const approve = useApproveGate();
  const reject = useRejectGate();

  return (
    <main className="flex h-dvh flex-col gap-4 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-[20px] font-semibold">Gate WhatsApp</h1>
        <span className="text-[12px] text-muted-foreground">
          Aprovação humana de contato · aguardando_consulta_whatsapp
        </span>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-8 xl:grid-cols-[minmax(360px,1fr)_2fr]">
        <section className="min-h-0 overflow-hidden">
          <GateQueue
            selectedId={selected?.rio_id ?? null}
            onSelect={setSelected}
          />
        </section>

        <section className="min-h-0 overflow-hidden rounded-md border">
          <GateReviewPanel
            item={selected}
            actions={(item: GateQueueItem) => (
              <GateActions
                item={item}
                onApprove={() => approve.mutate(item.rio_id)}
                onReject={() => reject.mutate(item.rio_id)}
                pending={approve.isPending || reject.isPending}
              />
            )}
          />
        </section>
      </div>
    </main>
  );
}

function GateActions({
  item,
  onApprove,
  onReject,
  pending,
}: {
  item: GateQueueItem;
  onApprove: () => void;
  onReject: () => void;
  pending: boolean;
}) {
  return (
    <>
      {/* Primary action: approve → enqueue WhatsApp outreach */}
      <Button size="sm" disabled={pending} onClick={onApprove}>
        Aprovar contato
      </Button>

      {/* Destructive reject → dlq/descarte, behind an AlertDialog confirm */}
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button size="sm" variant="destructive" disabled={pending}>
            Rejeitar
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Rejeitar atrativo?</AlertDialogTitle>
            <AlertDialogDescription>
              O atrativo não será contatado por WhatsApp e seguirá para
              DLQ/descarte conforme a pontuação.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={onReject}
            >
              Rejeitar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <span className="ml-auto font-mono text-[12px] text-muted-foreground tabular-nums">
        {item.rio_id.slice(0, 8)}
      </span>
    </>
  );
}
