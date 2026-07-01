"use client";

import { useEffect, useRef, useState } from "react";

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
 * Render windowing (kiy): each column renders at most `visibleCount` cards
 * (100 initial, +50 per scroll-to-bottom via an IntersectionObserver sentinel).
 * ALL cards stay in memory — this is display windowing only, no fetch change.
 * The count pill always shows the TRUE total (column.cards.length), never the
 * rendered window size.
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

/** Initial per-column render window, and the increment revealed per scroll. */
const INITIAL_VISIBLE = 100;
const VISIBLE_STEP = 50;

interface PainelColumnProps {
  column: { key: PainelColumnKey; label: string; cards: PainelCard[] };
  nascenteCount?: number;
  onDropToColumn: (key: PainelColumnKey) => void;
  onCardDragStart: (c: PainelCard) => void;
  onCardDragEnd?: () => void;
  onCardRetry: (c: PainelCard) => void;
  onCardClick?: (c: PainelCard) => void;
  isPending?: boolean;
}

/**
 * A single Kanban column. Owns its own render-window state so heavy columns
 * (>100 cards) mount fast and reveal more only as the operator scrolls down.
 */
function PainelColumn({
  column,
  nascenteCount,
  onDropToColumn,
  onCardDragStart,
  onCardDragEnd,
  onCardRetry,
  onCardClick,
  isPending,
}: PainelColumnProps) {
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  // The count pill shows the TRUE total, never the rendered window.
  const total = column.cards.length;
  const count =
    column.key === "nascente" ? (nascenteCount ?? total) : total;

  // Reset the window whenever the column's data/filter identity changes. Card
  // count is the observable proxy for a data/filter change (simple, no deep
  // compare) — enough to re-cap a re-filtered/reloaded column at 100.
  useEffect(() => {
    setVisibleCount(INITIAL_VISIBLE);
  }, [column.key, column.cards.length]);

  const hasMore = total > visibleCount;

  // Attach an IntersectionObserver on the sentinel (root = the scroll body) that
  // reveals +VISIBLE_STEP cards each time the bottom sentinel scrolls into view.
  useEffect(() => {
    if (!hasMore) return;
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    if (typeof IntersectionObserver === "undefined") return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisibleCount((v) => Math.min(v + VISIBLE_STEP, total));
        }
      },
      { root: scrollRef.current },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, total]);

  const visibleCards = column.cards.slice(0, visibleCount);

  return (
    <div className="flex w-[300px] max-h-full flex-shrink-0 flex-col">
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

      {/* body: drop target + scroll container (IntersectionObserver root) */}
      <div
        ref={scrollRef}
        data-col={column.key}
        data-testid={`painel-col-${column.key}`}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          onDropToColumn(column.key);
        }}
        className="flex min-h-0 flex-1 flex-col gap-[10px] overflow-y-auto rounded-lg"
      >
        {visibleCards.map((card) => (
          <RecordCard
            key={card.id}
            card={card}
            onDragStart={onCardDragStart}
            onDragEnd={onCardDragEnd}
            onRetry={onCardRetry}
            onClick={onCardClick}
          />
        ))}
        {hasMore ? (
          <div
            ref={sentinelRef}
            data-testid={`painel-col-sentinel-${column.key}`}
            aria-hidden="true"
            className="h-px w-full shrink-0"
          />
        ) : null}
        {isPending && total === 0 ? (
          <span className="px-1 py-2 text-[11px] text-[var(--painel-hint)]">
            Carregando…
          </span>
        ) : null}
      </div>
    </div>
  );
}

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
      {columns.map((column) => (
        <PainelColumn
          key={column.key}
          column={column}
          nascenteCount={nascenteCount}
          onDropToColumn={onDropToColumn}
          onCardDragStart={onCardDragStart}
          onCardDragEnd={onCardDragEnd}
          onCardRetry={onCardRetry}
          onCardClick={onCardClick}
          isPending={isPending}
        />
      ))}
    </div>
  );
}
