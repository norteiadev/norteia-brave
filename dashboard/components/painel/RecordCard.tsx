"use client";

import { StageBadge } from "@/components/cms/StageBadge";
import type { PainelCard } from "@/lib/painel-data";

/**
 * RecordCard — a single draggable Painel (Kanban) record card.
 *
 * NOTE (naming, M-1): the COMPONENT is `RecordCard`; its prop consumes the
 * `PainelCard` data TYPE from lib/painel-data.ts. Type and component names are
 * intentionally distinct — calling the component `PainelCard` would shadow the
 * type and fail to compile.
 *
 * Presentational only: it forwards native HTML5 drag events to handler props;
 * the drop targets + real mutations are wired by the board container (17-05).
 *
 * Tokens: painel CSS vars only (--painel-chip, --painel-navy, --painel-muted,
 * --painel-hint, --status-dlq, --status-descarte). No hardcoded hex.
 */

export interface RecordCardProps {
  card: PainelCard;
  onDragStart: (c: PainelCard) => void;
  onDragEnd?: () => void;
  onRetry: (c: PainelCard) => void;
  onClick?: (c: PainelCard) => void;
  /**
   * Edit-lock (phase H): cards are draggable/selectable ONLY when true (engine
   * mode PAUSADO/DESLIGADO). Defaults true so read-only callers/tests keep the
   * prior always-draggable behavior; the board threads the real lock state.
   */
  editingUnlocked?: boolean;
}

export function RecordCard({
  card,
  onDragStart,
  onDragEnd,
  onRetry,
  onClick,
  editingUnlocked = true,
}: RecordCardProps) {
  // The error/quarantine column is "falha" in the 6-column model (17.1-06).
  const isFalha = card.column === "falha";
  // Nascente cards are the raw immutable ingest layer — READ-ONLY: no drag
  // (nascente → rio is automatic), only a click-through to the drawer.
  const isNascente = card.column === "nascente";
  // Edit-lock: a card is only draggable when NOT nascente AND editing is unlocked.
  const draggable = !isNascente && editingUnlocked;

  return (
    <div
      draggable={draggable}
      data-id={card.id}
      data-testid="record-card"
      onDragStart={draggable ? () => onDragStart(card) : undefined}
      onDragEnd={draggable ? onDragEnd : undefined}
      onClick={() => onClick?.(card)}
      className={`flex flex-col gap-2 rounded-lg border border-[var(--painel-border-inner)] bg-[var(--card)] p-3 ${
        draggable ? "cursor-grab active:cursor-grabbing" : "cursor-pointer"
      }`}
    >
      {/* top row: type chip + score band */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="rounded-md bg-[var(--painel-chip)] px-2 py-0.5 text-[11px] font-semibold text-[var(--painel-navy)]">
            {card.type === "destino" ? "Destino" : "Atrativo"}
          </span>
        </div>
        {card.score != null ? <StageBadge score={card.score} /> : null}
      </div>

      {/* name */}
      <div className="text-[13.5px] font-semibold leading-tight tracking-[-0.2px]">
        {card.name}
      </div>

      {/* UF mono chip + município */}
      <div className="flex flex-wrap items-center gap-2">
        {card.uf != null ? (
          <span className="rounded-[5px] bg-[var(--painel-chip)] px-[7px] py-px font-mono text-[10.5px] font-semibold text-[var(--painel-muted)]">
            {card.uf}
          </span>
        ) : null}
        {card.municipality != null ? (
          <span className="text-[11.5px] text-[var(--painel-muted)]">
            {card.municipality}
          </span>
        ) : null}
      </div>

      {/* source label (hidden when null — L-2) + duplicate flag */}
      {card.source != null || card.duplicate ? (
        <div className="flex items-center justify-between gap-2">
          {card.source != null ? (
            <span className="text-[11px] text-[var(--painel-hint)]">
              {card.source}
            </span>
          ) : (
            <span />
          )}
          {card.duplicate ? (
            <span className="rounded-[5px] bg-[var(--status-dlq)]/15 px-[7px] py-px text-[10px] font-semibold text-[var(--status-dlq)]">
              Possível duplicado
            </span>
          ) : null}
        </div>
      ) : null}

      {/* falha line + reprocessar (descarte only) */}
      {isFalha ? (
        <div className="mt-0.5 flex items-center justify-between gap-2 border-t border-[var(--painel-border-inner)] pt-2">
          <span className="text-[11px] font-medium text-[var(--status-descarte)]">
            ⚠ {card.error ?? "Falha no processamento"}
          </span>
          <button
            type="button"
            data-testid="record-card-retry"
            onClick={(e) => {
              e.stopPropagation();
              onRetry(card);
            }}
            className="whitespace-nowrap rounded-md border border-[var(--painel-border-outer)] bg-[var(--card)] px-[9px] py-[3px] text-[11px] font-semibold text-[var(--painel-navy)]"
          >
            ↺ Reprocessar
          </button>
        </div>
      ) : null}
    </div>
  );
}
