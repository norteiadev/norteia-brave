"use client";

import { useState } from "react";
import {
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";

import { DestinoList } from "@/components/cms/DestinoList";
import { DetailPanel } from "@/components/cms/DetailPanel";
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
  destinoKeys,
  fetchDestinoDetail,
  promoteDestino,
  descarteDestino,
  reprocessDestino,
  type DestinoDetail,
} from "@/lib/destinos-api";

/**
 * /destinos — the Destinos CMS master-detail surface (D-03).
 *
 * DestinoList (left) ↔ DetailPanel (right). The action bar (Promover/Reprocessar/
 * Descartar) is injected into the action-agnostic DetailPanel via the `actions`
 * render-prop. Destructive Descartar is behind an AlertDialog confirm.
 */
export default function DestinosPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <main className="flex h-dvh flex-col gap-4 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-[20px] font-semibold">Destinos</h1>
        <span className="text-[12px] text-muted-foreground">
          Todas as etapas · Nascente → Rio → Mar
        </span>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-8 xl:grid-cols-[minmax(360px,1fr)_2fr]">
        <section className="min-h-0 overflow-hidden">
          <DestinoList selectedId={selectedId} onSelect={setSelectedId} />
        </section>

        <section className="min-h-0 overflow-hidden rounded-md border">
          <DetailPanel
            rioId={selectedId}
            fetchDetail={fetchDestinoDetail}
            queryKeys={destinoKeys}
            entityType="destination"
            actions={(detail: DestinoDetail) => (
              <DestinoActions
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

function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    return err.message;
  }
  return "Falha ao consultar a API.";
}

function DestinoActions({
  detail,
  onDone,
}: {
  detail: DestinoDetail;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const DESTINO_KEY = destinoKeys.all;

  const promote = useMutation({
    mutationFn: () => promoteDestino(detail.id),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => {
      toast.success("Destino promovido → Mar");
      onDone();
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: DESTINO_KEY });
    },
  });

  const reprocess = useMutation({
    mutationFn: () => reprocessDestino(detail.id),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => toast.success("Destino reenviado para reprocessamento"),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: DESTINO_KEY });
    },
  });

  const descarte = useMutation({
    mutationFn: () => descarteDestino(detail.id),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => {
      toast.success("Destino descartado");
      onDone();
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: DESTINO_KEY });
    },
  });

  const pending = promote.isPending || reprocess.isPending || descarte.isPending;

  return (
    <>
      <Button
        size="sm"
        disabled={pending}
        onClick={() => promote.mutate()}
      >
        Promover para Mar
      </Button>

      <Button
        size="sm"
        variant="outline"
        disabled={pending}
        onClick={() => reprocess.mutate()}
      >
        Reprocessar
      </Button>

      {/* Destructive Descartar behind AlertDialog confirm */}
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button size="sm" variant="destructive" disabled={pending}>
            Descartar
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Descartar destino?</AlertDialogTitle>
            <AlertDialogDescription>
              Este destino vai para descarte e não será publicado no Mar. Ação
              reversível só via reprocessamento.
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
