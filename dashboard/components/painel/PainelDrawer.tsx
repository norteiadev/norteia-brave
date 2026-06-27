"use client";

/**
 * PainelDrawer — the record-edit slide-over (17, UI-PAINEL-1).
 *
 * Opens when a Kanban card is clicked. THIS SLICE is read-only: there is no
 * record-update endpoint yet, so the "Dados" tab renders static fields (NOT
 * inputs) and the footer only fires the existing, allow-listed real actions
 * (Descartar / Promover / Reprocessar) via usePainelMutations. A free-text
 * "Salvar" is intentionally absent.
 *
 * Stays mounted at all times so the panel can slide in/out: when `card` is null
 * the overlay is non-interactive (pointer-events:none, opacity 0) and the panel
 * is translated off-screen (translateX(100%)). Design lines 381-437, 962-980,
 * 1020-1032.
 *
 * Tokens: painel CSS vars; design literals only where there is no token
 * (atrativo orange #b65a2e, WhatsApp green oklch literals).
 *
 * LGPD (R3): the Conversa tab shows ONLY the masked phone — never a raw E.164.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  conversationKeys,
  fetchConversationDetail,
} from "@/lib/conversations-api";
import { usePainelMutations } from "@/lib/painel-actions";
import { COLUMN_DEFS, type PainelCard } from "@/lib/painel-data";

/** Atrativo accent has no token (design literal); destino uses the navy var. */
const ATRATIVO_ACCENT = "#b65a2e";

type DrawerTab = "dados" | "conversa";

export interface PainelDrawerProps {
  card: PainelCard | null;
  onClose: () => void;
}

export function PainelDrawer({ card, onClose }: PainelDrawerProps) {
  const [tab, setTab] = useState<DrawerTab>("dados");
  const { drop, retry } = usePainelMutations();

  // Reset to the Dados tab whenever a different card opens.
  const cardId = card?.id ?? "";
  useEffect(() => {
    setTab("dados");
  }, [cardId]);

  const isOpen = card != null;
  const accent =
    card?.type === "atrativo" ? ATRATIVO_ACCENT : "var(--painel-navy)";
  const typeLabel = card?.type === "atrativo" ? "Atrativo" : "Destino";
  const stageLabel =
    COLUMN_DEFS.find((c) => c.key === card?.column)?.label ?? "—";

  const convo = useQuery({
    queryKey: conversationKeys.detail(cardId),
    queryFn: () => fetchConversationDetail(cardId),
    enabled: tab === "conversa" && isOpen,
    retry: false,
  });
  const messages = convo.data?.messages ?? [];
  const convoEmpty = convo.isError || (convo.isSuccess && messages.length === 0);

  function handleDescartar() {
    if (!card) return;
    drop(card, "descarte");
    onClose();
  }
  function handlePromover() {
    if (!card) return;
    drop(card, "mar");
    onClose();
  }
  function handleReprocessar() {
    if (!card) return;
    retry(card);
    onClose();
  }

  return (
    <>
      {/* overlay */}
      <div
        data-testid="drawer-overlay"
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(15,20,35,.32)",
          zIndex: 50,
          opacity: isOpen ? 1 : 0,
          pointerEvents: isOpen ? "auto" : "none",
          transition: "opacity .25s",
        }}
      />

      {/* slide-over panel */}
      <aside
        data-testid="drawer-panel"
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          height: "100%",
          width: 440,
          maxWidth: "92vw",
          zIndex: 60,
          display: "flex",
          flexDirection: "column",
          transform: isOpen ? "translateX(0)" : "translateX(100%)",
          transition: "transform .28s cubic-bezier(.4,0,.2,1)",
        }}
        className="border-l border-[var(--painel-border-outer)] bg-[var(--card)] shadow-[-12px_0_40px_rgba(15,23,42,.12)]"
      >
        {/* header */}
        <div className="flex flex-shrink-0 items-center justify-between gap-2.5 border-b border-[var(--painel-border-outer)] px-5 py-4">
          <div className="flex items-center gap-2.5">
            <span
              className="rounded-md px-2 py-[3px] text-[10px] font-semibold uppercase tracking-[0.4px]"
              style={{
                color: accent,
                background:
                  card?.type === "atrativo"
                    ? "color-mix(in oklch, #b65a2e 13%, white)"
                    : "color-mix(in oklch, var(--painel-navy) 13%, white)",
              }}
            >
              {typeLabel}
            </span>
            <span className="text-sm font-semibold text-[var(--painel-text)]">
              Registro
            </span>
          </div>
          <button
            type="button"
            data-testid="drawer-close"
            onClick={onClose}
            className="cursor-pointer px-1 py-0.5 text-xl leading-none text-[var(--painel-muted-2)]"
          >
            ×
          </button>
        </div>

        {/* tab bar */}
        <div className="flex flex-shrink-0 items-center border-b border-[var(--painel-border-outer)] px-5">
          {(
            [
              { key: "dados", label: "Dados" },
              { key: "conversa", label: "Conversa" },
            ] as const
          ).map((t) => (
            <button
              key={t.key}
              type="button"
              data-tab={t.key}
              data-testid={`drawer-tab-${t.key}`}
              onClick={() => setTab(t.key)}
              className="mr-[18px] h-[38px] cursor-pointer border-b-2 bg-transparent px-1 text-[13px] font-semibold"
              style={{
                borderBottomColor:
                  tab === t.key ? "var(--painel-navy)" : "transparent",
                color: tab === t.key ? "var(--painel-navy)" : "var(--painel-muted-2)",
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* body */}
        {tab === "dados" ? (
          <div className="flex flex-1 flex-col gap-3.5 overflow-y-auto px-5 py-[18px]">
            <Field label="Nome" testid="drawer-field-name">
              {card?.name ?? "—"}
            </Field>
            <div className="flex gap-3">
              <Field label="UF" testid="drawer-field-uf" mono>
                {card?.uf ?? "—"}
              </Field>
              <Field label="Tipo" testid="drawer-field-type">
                {typeLabel}
              </Field>
            </div>
            <Field label="Município" testid="drawer-field-municipality">
              {card?.municipality ?? "—"}
            </Field>
            <div className="flex gap-3">
              <Field label="Fonte" testid="drawer-field-source">
                {card?.source ?? "—"}
              </Field>
              <Field label="Score §7.6" testid="drawer-field-score" mono>
                {card?.score != null ? card.score.toFixed(1) : "—"}
              </Field>
            </div>
            <Field label="Etapa do pipeline" testid="drawer-field-stage">
              {stageLabel}
            </Field>

            {card?.duplicate ? (
              <div
                data-testid="drawer-duplicate-warning"
                className="flex gap-2.5 rounded-[9px] p-3"
                style={{
                  background: "color-mix(in oklch, var(--status-dlq) 12%, white)",
                  border:
                    "1px solid color-mix(in oklch, var(--status-dlq) 32%, white)",
                }}
              >
                <span className="font-bold text-[var(--status-dlq)]">⚠</span>
                <p className="m-0 text-[11.5px] leading-[1.45] text-[#7a5a17]">
                  A camada de validação detectou um registro semelhante já
                  publicado no Mar. Revise em <strong>Duplicados</strong> antes
                  de promover.
                </p>
              </div>
            ) : null}

            <div className="mt-0.5 border-t border-[var(--painel-border-inner)] pt-3">
              <div className="mb-[3px] text-[11px] text-[var(--painel-muted-2)]">
                ID do registro
              </div>
              <div
                data-testid="drawer-field-id"
                className="font-mono text-[11.5px] text-[#475569]"
              >
                {card?.id ?? "—"}
              </div>
            </div>
          </div>
        ) : (
          <div className="flex flex-1 flex-col overflow-hidden bg-[#fbfaf8]">
            {convoEmpty ? (
              <div
                data-testid="drawer-convo-empty"
                className="grid flex-1 place-items-center p-10 text-center text-[12.5px] text-[var(--painel-muted-2)]"
              >
                Nenhuma conversa de WhatsApp iniciada para este registro ainda.
              </div>
            ) : messages.length > 0 ? (
              <>
                {/* masked-phone header (LGPD: masked only) */}
                <div className="flex flex-shrink-0 items-center gap-2 border-b border-[var(--painel-border-inner)] bg-[#fbfaf8] px-5 py-[11px]">
                  <span
                    className="h-2 w-2 flex-shrink-0 rounded-full"
                    style={{ background: "oklch(0.6 0.15 156)" }}
                  />
                  <span className="font-mono text-[11.5px] text-[#475569]">
                    {convo.data?.phone_masked}
                  </span>
                  <span className="text-[10px] uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
                    telefone (minimizado)
                  </span>
                </div>
                {/* bubbles */}
                <div className="flex flex-1 flex-col gap-[11px] overflow-y-auto px-5 py-[18px]">
                  {messages.map((m) => {
                    const out = m.direction === "outbound";
                    return (
                      <div
                        key={m.id}
                        data-testid="drawer-bubble"
                        className="flex flex-col gap-[3px]"
                        style={{ alignItems: out ? "flex-end" : "flex-start" }}
                      >
                        <div
                          className="max-w-[82%] px-[13px] py-[9px] text-[13px] leading-[1.45]"
                          style={{
                            borderRadius: out
                              ? "13px 13px 3px 13px"
                              : "13px 13px 13px 3px",
                            background: out
                              ? "var(--painel-navy)"
                              : "var(--painel-chip)",
                            color: out ? "#fff" : "var(--painel-text)",
                          }}
                        >
                          {m.content}
                        </div>
                        {m.extracted ? (
                          <pre
                            data-testid="drawer-bubble-extracted"
                            className="m-0 max-w-[82%] overflow-x-auto rounded-[7px] px-2.5 py-[7px] font-mono text-[10.5px] leading-[1.5] text-[#3a5a3f]"
                            style={{
                              background:
                                "color-mix(in oklch, oklch(0.6 0.15 156) 10%, white)",
                              border:
                                "1px solid color-mix(in oklch, oklch(0.6 0.15 156) 26%, white)",
                            }}
                          >
                            {JSON.stringify(m.extracted, null, 2)}
                          </pre>
                        ) : null}
                        {m.created_at ? (
                          <span className="px-0.5 font-mono text-[10px] text-[var(--painel-muted-2)]">
                            {m.created_at}
                          </span>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </>
            ) : (
              <div className="grid flex-1 place-items-center p-10 text-center text-[12.5px] text-[var(--painel-muted-2)]">
                Carregando conversa…
              </div>
            )}
          </div>
        )}

        {/* footer actions (read-only slice: existing real actions only) */}
        <div className="flex flex-shrink-0 items-center justify-between gap-2.5 border-t border-[var(--painel-border-outer)] px-5 py-3.5">
          <button
            type="button"
            data-testid="drawer-descartar"
            onClick={handleDescartar}
            className="cursor-pointer bg-transparent px-1 py-2 text-[12.5px] font-semibold"
            style={{ color: "var(--status-descarte)" }}
          >
            Descartar
          </button>
          <div className="flex gap-2.5">
            {card?.type === "destino" ? (
              <button
                type="button"
                data-testid="drawer-reprocessar"
                onClick={handleReprocessar}
                className="h-9 cursor-pointer rounded-lg border border-[var(--painel-border-outer)] bg-[var(--card)] px-[15px] text-[12.5px] font-semibold text-[var(--painel-text)]"
              >
                Reprocessar
              </button>
            ) : null}
            <button
              type="button"
              data-testid="drawer-promover"
              onClick={handlePromover}
              className="h-9 cursor-pointer rounded-lg border-none px-[17px] text-[12.5px] font-semibold text-white"
              style={{ background: "var(--painel-navy)" }}
            >
              Promover
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}

/** A read-only labelled field row (static value text, never an input). */
function Field({
  label,
  testid,
  mono,
  children,
}: {
  label: string;
  testid: string;
  mono?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-1 flex-col gap-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-[0.4px] text-[var(--painel-muted)]">
        {label}
      </span>
      <span
        data-testid={testid}
        className={`flex h-[38px] items-center rounded-lg border border-[var(--painel-border-outer)] bg-[var(--card)] px-3 text-[13px] text-[var(--painel-text)] ${
          mono ? "font-mono" : ""
        }`}
      >
        {children}
      </span>
    </div>
  );
}
