"use client";

import Link from "next/link";
import { use } from "react";

import { DetailPanel } from "@/components/cms/DetailPanel";
import { destinoKeys, fetchDestinoDetail } from "@/lib/destinos-api";

/**
 * /destinos/[id] — full-detail view for a single destino (D-03).
 *
 * Renders DetailPanel in full-width mode (no master-list split).
 * Back link returns to /destinos.
 *
 * Read-only — actions are deliberately omitted here (the master-detail page
 * at /destinos is the primary action surface). Deep-links here are for
 * reference and audit inspection only.
 */
export default function DestinoDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  return (
    <main className="flex min-h-dvh flex-col gap-4 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-[20px] font-semibold">Destino — Detalhe</h1>
        <Link
          href="/destinos"
          className="font-mono text-[12px] text-muted-foreground underline-offset-2 hover:underline"
        >
          ← Voltar para Destinos
        </Link>
      </header>

      <div className="flex-1 rounded-md border">
        <DetailPanel
          rioId={id}
          fetchDetail={fetchDestinoDetail}
          queryKeys={destinoKeys}
          entityType="destination"
        />
      </div>
    </main>
  );
}
