"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  type ConversationDetail,
  type ConversationLastMessage,
  type ConversationListData,
  type ConversationListItem,
  conversationKeys,
  fetchConversationDetail,
  fetchConversations,
} from "@/lib/conversations-api";

/**
 * PainelConversas — WhatsApp transcripts, re-skinned to the light Painel theme
 * (Phase 17). Master/detail over the real conversations-api: the left list is
 * one row per rio_id; the right pane renders the selected transcript as chat
 * bubbles (navy outbound, cream inbound) with inline extraction snapshots.
 *
 * LGPD (R3, T-04-28): only the masked phone the backend emits is ever shown —
 * this view never holds nor reconstructs a raw E.164 number.
 *
 * Tokens: scoped `.painel-light` CSS vars; design literals (WhatsApp green
 * oklch(0.55 0.13 156), navy #15315e, body #fbfaf8, extraction green) kept inline
 * and localized where the design uses an untokenized accent.
 */

const WA_GREEN = "oklch(0.55 0.13 156)";

interface ConvoStatus {
  label: string;
  color: string;
  bg: string;
}

/** Derive the row status pill from the last message's direction (task spec). */
function deriveStatus(last: ConversationLastMessage | null): ConvoStatus {
  if (!last) {
    return {
      label: "Sem resposta",
      color: "var(--painel-muted)",
      bg: "var(--painel-chip)",
    };
  }
  if (last.direction === "inbound") {
    return {
      label: "Respondido",
      color: "var(--status-mar)",
      bg: "color-mix(in oklch, var(--status-mar) 14%, white)",
    };
  }
  return {
    label: "Aguardando",
    color: "var(--status-dlq)",
    bg: "color-mix(in oklch, var(--status-dlq) 16%, white)",
  };
}

/** Short HH:MM from an ISO timestamp, timezone-stable (no Date parsing). */
function formatTime(iso: string | null): string {
  if (!iso) return "";
  const m = iso.match(/T(\d{2}:\d{2})/);
  return m ? m[1] : "";
}

function MasterRow({
  c,
  selected,
  onSelect,
}: {
  c: ConversationListItem;
  selected: boolean;
  onSelect: (rioId: string) => void;
}) {
  const status = deriveStatus(c.last_message);
  return (
    <div
      data-testid="convo-row"
      data-id={c.rio_id}
      onClick={() => onSelect(c.rio_id)}
      className="flex cursor-pointer flex-col gap-[5px] border-b border-[var(--painel-border-inner)] px-[14px] py-3 transition-colors"
      style={{
        borderLeft: `3px solid ${selected ? WA_GREEN : "transparent"}`,
        background: selected
          ? "color-mix(in oklch, " + WA_GREEN + " 7%, white)"
          : "var(--card)",
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="overflow-hidden text-ellipsis whitespace-nowrap text-[13px] font-semibold">
          {c.rio_id}
        </span>
        <span
          className="whitespace-nowrap rounded-full px-2 py-px text-[10px] font-semibold"
          style={{ color: status.color, background: status.bg }}
        >
          {status.label}
        </span>
      </div>
      <div className="flex items-center gap-[7px]">
        <span className="overflow-hidden text-ellipsis whitespace-nowrap rounded-[5px] bg-[var(--painel-chip)] px-[6px] py-px font-mono text-[10.5px] font-semibold text-[var(--painel-muted)]">
          {c.rio_id}
        </span>
        <span className="whitespace-nowrap font-mono text-[11px] text-[var(--painel-muted-2)]">
          {c.phone_masked}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-[11.5px] text-[var(--painel-muted)]">
          {c.last_message
            ? (c.last_message.direction === "outbound" ? "Você: " : "") +
              c.last_message.content
            : ""}
        </span>
        {c.last_message ? (
          <span className="whitespace-nowrap font-mono text-[10px] text-[var(--painel-hint)]">
            {formatTime(c.last_message.created_at)}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function DetailPane({ rioId }: { rioId: string }) {
  const { data } = useQuery<ConversationDetail>({
    queryKey: conversationKeys.detail(rioId),
    queryFn: () => fetchConversationDetail(rioId),
    enabled: !!rioId,
    refetchOnWindowFocus: false,
  });

  if (!data) {
    return <div className="flex-1" style={{ background: "#fbfaf8" }} />;
  }

  return (
    <>
      <div className="flex items-center gap-[10px] border-b border-[var(--painel-border-outer)] bg-[var(--card)] px-5 py-[13px]">
        <span
          className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-full text-white"
          style={{ background: WA_GREEN }}
        >
          <svg width="17" height="17" viewBox="0 0 16 16" fill="none">
            <path
              d="M2 4.5C2 3.4 2.9 2.5 4 2.5H12C13.1 2.5 14 3.4 14 4.5V9.5C14 10.6 13.1 11.5 12 11.5H6L3 14V11.5C2.4 11.5 2 11 2 10.5V4.5Z"
              stroke="currentColor"
              strokeWidth="1.4"
              fill="none"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <div className="flex min-w-0 flex-col leading-[1.3]">
          <span className="overflow-hidden text-ellipsis whitespace-nowrap text-[14px] font-semibold">
            {data.rio_id}
          </span>
          <span className="flex items-center gap-[7px]">
            <span className="font-mono text-[11.5px] text-[var(--painel-muted)]">
              {data.phone_masked}
            </span>
            <span className="text-[10px] uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
              telefone (minimizado)
            </span>
          </span>
        </div>
      </div>

      <div
        className="flex flex-1 flex-col gap-3 overflow-y-auto px-6 py-5"
        style={{ background: "#fbfaf8" }}
      >
        {data.messages.map((m) => {
          const out = m.direction === "outbound";
          return (
            <div
              key={m.id}
              className="flex flex-col gap-[3px]"
              style={{ alignItems: out ? "flex-end" : "flex-start" }}
            >
              <div
                data-testid="convo-bubble"
                className="max-w-[78%] px-[13px] py-[9px] text-[13.5px] leading-[1.45]"
                style={{
                  borderRadius: out
                    ? "13px 13px 3px 13px"
                    : "13px 13px 13px 3px",
                  background: out ? "var(--painel-navy)" : "var(--painel-chip)",
                  color: out ? "#fff" : "var(--painel-text)",
                }}
              >
                {m.content}
              </div>
              {m.extracted ? (
                <pre
                  data-testid="convo-extracted"
                  className="m-0 max-w-[78%] overflow-x-auto rounded-[7px] px-[10px] py-[7px] font-mono text-[10.5px] leading-[1.5]"
                  style={{
                    background:
                      "color-mix(in oklch, oklch(0.6 0.15 156) 10%, white)",
                    border:
                      "1px solid color-mix(in oklch, oklch(0.6 0.15 156) 26%, white)",
                    color: "#3a5a3f",
                  }}
                >
                  {JSON.stringify(m.extracted, null, 2)}
                </pre>
              ) : null}
              <span className="px-0.5 font-mono text-[10.5px] text-[var(--painel-muted-2)]">
                {formatTime(m.created_at)}
              </span>
            </div>
          );
        })}
      </div>
    </>
  );
}

export function PainelConversas() {
  const { data } = useQuery<ConversationListData>({
    queryKey: conversationKeys.list,
    queryFn: fetchConversations,
    refetchOnWindowFocus: false,
  });

  const conversations: ConversationListItem[] = data?.conversations ?? [];
  const [selectedRioId, setSelectedRioId] = useState<string | null>(null);

  // Auto-select the first conversation once the list arrives.
  useEffect(() => {
    if (selectedRioId == null && conversations.length > 0) {
      setSelectedRioId(conversations[0].rio_id);
    }
  }, [selectedRioId, conversations]);

  return (
    <div className="flex h-full">
      <div className="flex w-[330px] flex-shrink-0 flex-col border-r border-[var(--painel-border-outer)] bg-[var(--card)]">
        <div className="flex items-center justify-between border-b border-[var(--painel-border-inner)] px-4 py-[13px]">
          <span className="text-[12px] font-semibold">Conversas ativas</span>
          <span className="rounded-full bg-[var(--painel-chip)] px-2 py-px font-mono text-[11px] font-semibold text-[var(--painel-muted)]">
            {conversations.length}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto">
          {conversations.map((c) => (
            <MasterRow
              key={c.rio_id}
              c={c}
              selected={c.rio_id === selectedRioId}
              onSelect={setSelectedRioId}
            />
          ))}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        {selectedRioId ? (
          <DetailPane rioId={selectedRioId} />
        ) : (
          <div
            data-testid="convo-empty"
            className="grid flex-1 place-items-center text-[13px] text-[var(--painel-muted-2)]"
          >
            Selecione uma conversa para ver a transcrição.
          </div>
        )}
      </div>
    </div>
  );
}
