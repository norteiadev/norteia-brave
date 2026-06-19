"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { AtrativoList } from "@/components/cms/AtrativoList";
import { DetailPanel } from "@/components/cms/DetailPanel";
import { EditFieldsDialog } from "@/components/cms/EditFieldsDialog";
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
import { ApiError } from "@/lib/api-client";
import {
  atrativoKeys,
  fetchAtrativoDetail,
  advanceAtrativo,
  descartarAtrativo,
  editAtrativo,
  type AtrativoDetail,
} from "@/lib/atrativos-api";

/**
 * /atrativos — the Atrativos CMS master-detail surface (D-04).
 *
 * AtrativoList (left) ↔ DetailPanel with entityType="attraction" (right).
 * The action bar (Avançar/Descartar) is injected into the action-agnostic
 * DetailPanel via the `actions` render-prop. Destructive Descartar is behind
 * an AlertDialog confirm.
 *
 * Advance uses FSM-guided next_state: discovered→contacts_found→signals_gathered
 * →aguardando_consulta_whatsapp. If the backend returns 409 (expected_state
 * mismatch) the user is prompted to reload.
 *
 * PII contract: phone_e164 is never rendered here — only phone_masked from the
 * backend-masked contacts_summary is exposed, and only in the detail panel.
 */
export default function AtrativosPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <main className="flex h-dvh flex-col gap-4 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-[20px] font-semibold">Atrativos</h1>
        <span className="text-[12px] text-muted-foreground">
          Pipeline de atrativos · Descoberta → Contatos → Sinais → Mar
        </span>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-8 xl:grid-cols-[minmax(360px,1fr)_2fr]">
        <section className="min-h-0 overflow-hidden">
          <AtrativoList selectedId={selectedId} onSelect={setSelectedId} />
        </section>

        <section className="min-h-0 overflow-hidden rounded-md border">
          <DetailPanel
            rioId={selectedId}
            fetchDetail={fetchAtrativoDetail}
            queryKeys={atrativoKeys}
            entityType="attraction"
            actions={(detail: AtrativoDetail) => (
              <AtrativoActions
                detail={detail}
                onDone={() => setSelectedId(null)}
              />
            )}
          />
        </section>
      </div>
    </main>
  );
}

/** FSM progression for atrativos sub_state.
 *  Returns the next state or null if the state is terminal (no advance possible). */
function nextSubState(current: string | null): string | null {
  const progression: Record<string, string> = {
    discovered: "contacts_found",
    contacts_found: "signals_gathered",
    signals_gathered: "aguardando_consulta_whatsapp",
  };
  return current ? (progression[current] ?? null) : null;
}

function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    if (err.status === 409) return "Estado já avançado — recarregue a página";
    return err.message;
  }
  return "Falha ao consultar a API.";
}

function AtrativoActions({
  detail,
  onDone,
}: {
  detail: AtrativoDetail;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const ATRATIVO_KEY = atrativoKeys.all;
  const next = nextSubState(detail.sub_state);

  const advance = useMutation({
    mutationFn: () =>
      advanceAtrativo(detail.id, {
        expected_state: detail.sub_state ?? "",
        next_state: next ?? "",
      }),
    onError: (err) => {
      const msg = explainError(err);
      toast.error(msg);
    },
    onSuccess: () => {
      toast.success(`Atrativo avançado para ${next}`);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ATRATIVO_KEY });
    },
  });

  const descarte = useMutation({
    mutationFn: () => descartarAtrativo(detail.id),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => {
      toast.success("Atrativo descartado");
      onDone();
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ATRATIVO_KEY });
    },
  });

  const pending = advance.isPending || descarte.isPending;

  return (
    <>
      {/* Advance only shown when sub_state has a valid FSM progression */}
      {next != null && (
        <Button
          size="sm"
          disabled={pending}
          onClick={() => advance.mutate()}
        >
          Avançar → {next.replace(/_/g, " ")}
        </Button>
      )}

      <EditFieldsDialog
        normalized={detail.normalized}
        editFn={(fields) => editAtrativo(detail.id, fields)}
        invalidateKey={ATRATIVO_KEY}
        disabled={pending}
      />

      {/* Destructive Descartar behind AlertDialog confirm */}
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button size="sm" variant="destructive" disabled={pending}>
            Descartar
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Descartar atrativo?</AlertDialogTitle>
            <AlertDialogDescription>
              Este atrativo será descartado e não seguirá para o Mar. A ação é
              reversível somente via reprocessamento manual.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={() => descarte.mutate()}
            >
              Descartar
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
