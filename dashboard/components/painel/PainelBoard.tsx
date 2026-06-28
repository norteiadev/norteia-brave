"use client";

import { RecordCard } from "@/components/painel/RecordCard";
import {
  buildColumns,
  type PainelCard,
  type PainelColumnKey,
} from "@/lib/painel-data";

/**
 * PainelBoard — the 6-column horizontal-scroll Kanban board.
 *
 * Presentational: it buckets the supplied `cards` via buildColumns and renders
 * a RecordCard per card, forwarding native HTML5 drag events to handler props.
 * The Nascente column's count comes from the `nascenteCount` prop — the
 * rio-backed lists don't surface nascente-only records, so its real count is
 * the engine status count (17-02). Every other column counts its own cards.
 *
 * The real drop mutations are wired by the container (17-05); here a drop just
 * calls onDropToColumn(key). Tokens: painel CSS vars only.
 */

export interface PainelBoardProps {
  cards: PainelCard[];
  nascenteCount?: number;
  onDropToColumn: (key: PainelColumnKey) => void;
  onCardDragStart: (c: PainelCard) => void;
  onCardDragEnd?: () => void;
  onCardRetry: (c: PainelCard) => void;
  onCardClick?: (c: PainelCard) => void;
  isPending?: boolean;
}

const COLUMN_DOT: Record<PainelColumnKey, string> = {
  nascente: "bg-[var(--painel-muted-2)]",
  rio: "bg-[var(--painel-navy)]",
  whatsapp: "bg-[var(--status-whatsapp,var(--status-mar))]",
  mar: "bg-[var(--status-mar)]",
  dlq: "bg-[var(--status-dlq)]",
  // descarte is not a rendered column (no COLUMN_DEFS entry) but the key exists.
  descarte: "bg-[var(--status-descarte)]",
  falha: "bg-[var(--status-descarte)]",
};

export function PainelBoard({
  cards,
  nascenteCount,
  onDropToColumn,
  onCardDragStart,
  onCardDragEnd,
  onCardRetry,
  onCardClick,
  isPending,
}: PainelBoardProps) {
  const columns = buildColumns(cards);

  return (
    <div className="flex min-h-0 flex-1 items-stretch gap-[14px] overflow-x-auto overflow-y-hidden px-[22px] py-[14px]">
      {columns.map((column) => {
        const count =
          column.key === "nascente"
            ? (nascenteCount ?? column.cards.length)
            : column.cards.length;

        return (
          <div
            key={column.key}
            className="flex w-[300px] max-h-full flex-shrink-0 flex-col"
          >
            {/* header: dot + label + count pill */}
            <div className="flex items-center justify-between px-1 pb-[11px] pt-0.5">
              <div className="flex min-w-0 items-center gap-2">
                <span
                  className={`inline-block h-2 w-2 rounded-full ${COLUMN_DOT[column.key]}`}
                />
                <span className="whitespace-nowrap text-[12.5px] font-semibold">
                  {column.label}
                </span>
              </div>
              <span
                data-testid={`painel-col-count-${column.key}`}
                className="rounded-full bg-[var(--painel-chip)] px-2 py-px font-mono text-[11px] font-semibold text-[var(--painel-muted)]"
              >
                {count}
              </span>
            </div>

            {/* body: drop target */}
            <div
              data-col={column.key}
              data-testid={`painel-col-${column.key}`}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                onDropToColumn(column.key);
              }}
              className="flex min-h-0 flex-1 flex-col gap-[10px] overflow-y-auto rounded-lg"
            >
              {column.cards.map((card) => (
                <RecordCard
                  key={card.id}
                  card={card}
                  onDragStart={onCardDragStart}
                  onDragEnd={onCardDragEnd}
                  onRetry={onCardRetry}
                  onClick={onCardClick}
                />
              ))}
              {isPending && column.cards.length === 0 ? (
                <span className="px-1 py-2 text-[11px] text-[var(--painel-hint)]">
                  Carregando…
                </span>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
