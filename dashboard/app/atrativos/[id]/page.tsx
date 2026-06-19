"use client";

import Link from "next/link";
import { use } from "react";

import { DetailPanel } from "@/components/cms/DetailPanel";
import { atrativoKeys, fetchAtrativoDetail } from "@/lib/atrativos-api";

/**
 * /atrativos/[id] — full-detail view for a single atrativo (D-04).
 *
 * Renders DetailPanel in full-width mode (no master-list split) with
 * entityType="attraction" so JourneyStepper renders the 7-step atrativo
 * journey (discovered → contacts_found → signals_gathered →
 * aguardando_consulta_whatsapp → whatsapp_in_progress → scored → mar).
 *
 * Parent destino link is shown if the detail includes a parent_destino field.
 * Back link returns to /atrativos.
 *
 * Read-only — actions are deliberately omitted here (the master-detail page
 * at /atrativos is the primary action surface). Deep-links here are for
 * reference and audit inspection only.
 *
 * PII contract: phone_e164 is never rendered — only phone_masked via
 * contacts_summary in the detail panel.
 */
export default function AtrativoDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  return (
    <main className="flex min-h-dvh flex-col gap-4 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-[20px] font-semibold">Atrativo — Detalhe</h1>
        <Link
          href="/atrativos"
          className="font-mono text-[12px] text-muted-foreground underline-offset-2 hover:underline"
        >
          ← Voltar para Atrativos
        </Link>
      </header>

      <div className="flex-1 rounded-md border">
        <DetailPanel
          rioId={id}
          fetchDetail={fetchAtrativoDetail}
          queryKeys={atrativoKeys}
          entityType="attraction"
          actions={(detail) => {
            // Show parent_destino link if available
            const parentDestino = (detail as { parent_destino?: { mar_id: string; name: string } | null })
              .parent_destino;
            if (!parentDestino) return null;
            return (
              <Link
                href={`/destinos/${parentDestino.mar_id}`}
                className="font-mono text-[12px] text-muted-foreground underline-offset-2 hover:underline"
              >
                Destino pai: {parentDestino.name}
              </Link>
            );
          }}
        />
      </div>
    </main>
  );
}
