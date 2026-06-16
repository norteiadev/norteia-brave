"use client";

import { useState } from "react";

import { ConversationList } from "@/components/conversations/ConversationList";
import { TranscriptPanel } from "@/components/conversations/TranscriptPanel";

/**
 * /conversations — the WhatsApp transcript view (DASH-05, master-detail).
 *
 * The left column lists conversations (one per rio_id) from the append-only
 * `conversation_message` log; selecting one renders its masked transcript in the
 * right-hand `TranscriptPanel`. Both surfaces read through the BFF.
 *
 * LGPD (R3, T-04-28): every phone shown is the masked value the backend returns
 * — the raw E.164 number is never fetched nor rendered.
 */
export default function ConversationsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <main className="flex min-h-dvh flex-col gap-4 p-6">
      <header>
        <h1 className="text-[20px] font-semibold">Conversas</h1>
        <p className="text-[12px] text-muted-foreground">
          Transcrições de WhatsApp por atrativo — telefone sempre minimizado.
        </p>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[320px_1fr]">
        <section className="overflow-auto rounded-md border">
          <ConversationList
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </section>
        <section className="min-h-[480px] overflow-hidden rounded-md border">
          <TranscriptPanel rioId={selectedId} />
        </section>
      </div>
    </main>
  );
}
