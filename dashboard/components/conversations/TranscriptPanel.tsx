"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError } from "@/lib/api-client";
import {
  type ConversationDetail,
  type ConversationMessage,
  conversationKeys,
  fetchConversationDetail,
} from "@/lib/conversations-api";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * TranscriptPanel — the WhatsApp transcript for one conversation (DASH-05).
 *
 * Renders the append-only `conversation_message` log (R2 Option B) as chat
 * bubbles: outbound (our messages) align right, inbound (the attraction's
 * replies) align left, oldest-first. Geist Sans body (UI-SPEC: 14px transcript
 * text). The structured extraction snapshot rides under the outbound turn it was
 * attached to.
 *
 * LGPD (R3, T-04-28): the header shows ONLY the masked phone the backend returns,
 * labeled "telefone (minimizado)". No raw E.164 number is ever fetched, held, or
 * rendered — there is no field for it in the response. A test asserts no raw
 * E.164 string reaches the DOM.
 *
 * Offline-tested view states (D-07): success / empty / error / 401 / 404.
 */
export function TranscriptPanel({ rioId }: { rioId: string | null }) {
  const { data, isPending, isError, error, refetch } =
    useQuery<ConversationDetail>({
      queryKey: conversationKeys.detail(rioId ?? ""),
      queryFn: () => fetchConversationDetail(rioId as string),
      enabled: rioId != null,
      refetchOnWindowFocus: false,
    });

  if (rioId == null) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center gap-1 p-12 text-center"
        data-testid="transcript-empty"
      >
        <h3 className="text-[14px] font-semibold">Sem conversas ainda</h3>
        <p className="text-[12px] text-muted-foreground">
          Selecione uma conversa para ver a transcrição.
        </p>
      </div>
    );
  }

  if (isPending) {
    return (
      <div
        className="flex flex-col gap-2 p-3"
        data-testid="transcript-skeleton"
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-3/4 rounded-lg" />
        ))}
      </div>
    );
  }

  if (isError) {
    const status = error instanceof ApiError ? error.status : undefined;
    if (status === 401) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-1 p-12 text-center">
          <h3 className="text-[14px] font-semibold">
            Sessão expirada ou token inválido
          </h3>
          <p className="text-[12px] text-muted-foreground">
            Faça login novamente para continuar.
          </p>
        </div>
      );
    }
    if (status === 404) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-1 p-12 text-center">
          <h3 className="text-[14px] font-semibold">Conversa não encontrada</h3>
          <p className="text-[12px] text-muted-foreground">
            Nenhuma transcrição para este registro.
          </p>
        </div>
      );
    }
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-12 text-center">
        <h3 className="text-[14px] font-semibold">Não foi possível carregar</h3>
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          Tentar novamente
        </Button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col" data-testid="transcript-panel">
      <header className="flex items-baseline gap-2 border-b px-4 py-3">
        <span className="font-mono text-[14px] tabular-nums">
          {data.phone_masked}
        </span>
        <span className="text-[12px] uppercase tracking-wide text-muted-foreground">
          telefone (minimizado)
        </span>
      </header>

      <ScrollArea className="flex-1">
        <ul className="flex flex-col gap-2 p-4">
          {data.messages.map((m) => (
            <TranscriptBubble key={m.id} message={m} />
          ))}
        </ul>
      </ScrollArea>
    </div>
  );
}

function TranscriptBubble({ message }: { message: ConversationMessage }) {
  const outbound = message.direction === "outbound";
  return (
    <li
      data-testid={`transcript-bubble-${message.direction}`}
      className={cn(
        "flex flex-col",
        outbound ? "items-end" : "items-start",
      )}
    >
      <div
        className={cn(
          "max-w-[80%] rounded-lg px-3 py-2 text-[14px] leading-normal",
          outbound
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-foreground",
        )}
      >
        {message.content}
      </div>
      {message.extracted ? (
        <pre className="mt-1 max-w-[80%] overflow-x-auto rounded bg-muted/50 px-2 py-1 font-mono text-[11px] text-muted-foreground">
          {JSON.stringify(message.extracted, null, 2)}
        </pre>
      ) : null}
      {message.created_at ? (
        <span className="mt-0.5 font-mono text-[11px] text-muted-foreground tabular-nums">
          {message.created_at}
        </span>
      ) : null}
    </li>
  );
}
