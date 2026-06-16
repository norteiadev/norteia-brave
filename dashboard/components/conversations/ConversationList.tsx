"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError } from "@/lib/api-client";
import {
  type ConversationListData,
  type ConversationListItem,
  conversationKeys,
  fetchConversations,
} from "@/lib/conversations-api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * ConversationList — the master list of WhatsApp conversations (DASH-05).
 *
 * One row per rio_id from the append-only `conversation_message` log: the masked
 * phone, the message count, and a one-line preview of the last message. Selecting
 * a row drives the `TranscriptPanel` via `onSelect` (master-detail).
 *
 * LGPD (R3): the row shows only the masked phone (`phone_masked`) the backend
 * returns — never a raw E.164 number.
 *
 * Offline-tested view states (D-07): success / empty / error / 401.
 */
export function ConversationList({
  selectedId,
  onSelect,
}: {
  selectedId?: string | null;
  onSelect?: (rioId: string) => void;
}) {
  const { data, isPending, isError, error, refetch } =
    useQuery<ConversationListData>({
      queryKey: conversationKeys.list,
      queryFn: fetchConversations,
      refetchOnWindowFocus: false,
    });

  if (isPending) {
    return (
      <div
        className="flex flex-col gap-1 p-2"
        data-testid="conversation-list-skeleton"
      >
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full rounded" />
        ))}
      </div>
    );
  }

  if (isError) {
    if (error instanceof ApiError && error.status === 401) {
      return (
        <div className="flex flex-col items-center justify-center gap-1 p-12 text-center">
          <h3 className="text-[14px] font-semibold">
            Sessão expirada ou token inválido
          </h3>
          <p className="text-[12px] text-muted-foreground">
            Faça login novamente para continuar.
          </p>
        </div>
      );
    }
    return (
      <div className="flex flex-col items-center justify-center gap-2 p-12 text-center">
        <h3 className="text-[14px] font-semibold">Não foi possível carregar</h3>
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          Tentar novamente
        </Button>
      </div>
    );
  }

  const conversations: ConversationListItem[] = data.conversations;
  if (conversations.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-1 p-12 text-center">
        <h3 className="text-[14px] font-semibold">Sem conversas ainda</h3>
        <p className="text-[12px] text-muted-foreground">
          Nenhuma conversa de WhatsApp registrada. As transcrições aparecem após
          a primeira saída aprovada no gate.
        </p>
      </div>
    );
  }

  return (
    <ul className="flex flex-col" data-testid="conversation-list">
      {conversations.map((c) => (
        <li key={c.rio_id}>
          <button
            type="button"
            onClick={() => onSelect?.(c.rio_id)}
            data-state={selectedId === c.rio_id ? "selected" : undefined}
            className={cn(
              "flex w-full flex-col items-start gap-0.5 border-b px-3 py-2 text-left hover:bg-muted/60",
              selectedId === c.rio_id && "bg-muted",
            )}
          >
            <div className="flex w-full items-baseline justify-between gap-2">
              <span className="font-mono text-[12px] tabular-nums">
                {c.phone_masked}
              </span>
              <span className="text-[12px] text-muted-foreground tabular-nums">
                {c.message_count} msg
              </span>
            </div>
            {c.last_message ? (
              <span className="line-clamp-1 text-[12px] text-muted-foreground">
                {c.last_message.direction === "inbound" ? "← " : "→ "}
                {c.last_message.content}
              </span>
            ) : null}
          </button>
        </li>
      ))}
    </ul>
  );
}
