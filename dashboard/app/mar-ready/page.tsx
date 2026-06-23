"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { MarReadyList } from "@/components/mar-ready/MarReadyList";

/**
 * /mar-ready — TripAdvisor attractions ready for manual promotion to Mar (TA-07).
 *
 * Renders the MarReadyList with an optional UF filter from URL search params
 * (e.g. /mar-ready?uf=BA). No UF param = all UFs.
 */
function MarReadyContent() {
  const searchParams = useSearchParams();
  const uf = searchParams.get("uf") ?? undefined;

  return (
    <main className="flex min-h-screen flex-col gap-6 p-8">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-[20px] font-semibold">Mar Ready</h1>
          <p className="text-[12px] text-muted-foreground">
            Atrativos TripAdvisor prontos para promoção manual → Mar
          </p>
        </div>
        {uf && (
          <span className="rounded-md border px-2.5 py-1 font-mono text-[12px] text-muted-foreground">
            Filtrando por UF: {uf}
          </span>
        )}
      </header>

      <MarReadyList uf={uf} />
    </main>
  );
}

export default function MarReadyPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-muted-foreground">Carregando…</div>}>
      <MarReadyContent />
    </Suspense>
  );
}
