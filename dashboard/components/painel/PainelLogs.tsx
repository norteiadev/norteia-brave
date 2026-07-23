"use client";

/**
 * PainelLogs — per-source log ring buffer viewer (slide-over).
 *
 * Opens when the operator clicks the terminal icon in PainelTopbar.
 * Polls GET /api/v1/logs every 2 s while open; stops polling when closed.
 * Lines are appended incrementally (cursor-based) and capped at 500 rendered.
 *
 * Structure mirrors PainelDrawer: position:fixed slide-over + overlay.
 * Width 480px (vs Drawer's 440px).
 *
 * Security: the log buffer never contains cookies/tokens/PII (enforced by
 * log_buffer._BLOCKED_FIELDS server-side). This component renders the safe
 * fields verbatim.
 */

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { SOURCE_LABELS, type EngineSource } from "@/lib/engine-api";
import { fetchLogs, logsKeys, type LogLine } from "@/lib/logs-api";
import { humanizeLogEvent, formatLogTime } from "@/lib/log-humanize";

interface PainelLogsProps {
  open: boolean;
  onClose: () => void;
  source: string; // string (not EngineSource) — endpoint accepts any source slug
  /**
   * Inline variant (phase H "Logs" painel view). When true the component drops
   * the fixed overlay + slide-over chrome and fills its parent container, and it
   * polls continuously regardless of `open`. Defaults to false so the original
   * slide-over usage (mounted from PainelTopbar's terminal icon) is unchanged.
   */
  inline?: boolean;
}

/** Level color mapping against painel-light CSS vars. */
function levelColor(level: string): string {
  if (level === "error" || level === "critical") return "var(--status-descarte)";
  if (level === "warning") return "var(--status-dlq)";
  if (level === "debug") return "var(--painel-muted-2)";
  return "var(--painel-text)";
}

export function PainelLogs({
  open,
  onClose,
  source,
  inline = false,
}: PainelLogsProps) {
  const [allLines, setAllLines] = useState<LogLine[]>([]);
  const cursorRef = useRef<number>(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Inline view polls continuously; the slide-over only while open.
  const active = open || inline;

  // Reset accumulated lines and cursor when the source changes
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset the tail buffer when the log source prop changes
    setAllLines([]);
    cursorRef.current = 0;
  }, [source]);

  const { data: logsData } = useQuery({
    queryKey: logsKeys.tail(source),
    queryFn: () => fetchLogs(source, cursorRef.current, 100),
    enabled: active,
    refetchInterval: active ? 2_000 : false,
    refetchOnWindowFocus: false,
  });

  // Append only NEW lines (deduplicated by id), cap at 500 rendered
  useEffect(() => {
    if (!logsData?.lines?.length) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- accumulate the polled log tail (external-system sync)
    setAllLines((prev) => {
      const existingIds = new Set(prev.map((l) => l.id));
      const fresh = logsData.lines.filter((l) => !existingIds.has(l.id));
      return [...prev, ...fresh].slice(-500);
    });
    cursorRef.current = logsData.cursor;
  }, [logsData]);

  // Auto-scroll to bottom when new lines arrive
  // Guard: scrollTo is not implemented in jsdom (test env) — use optional call
  useEffect(() => {
    const el = scrollRef.current;
    if (el && typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [allLines.length]);

  const sourceLabel =
    SOURCE_LABELS[source as EngineSource] ?? source;

  // Shared scroll body (empty state + rendered ring-buffer lines).
  const logScroll = (
    <div
      ref={scrollRef}
      className="flex-1 overflow-y-auto p-3"
      style={{ background: "var(--card)" }}
    >
      {allLines.length === 0 && active ? (
        <div
          className="grid h-full place-items-center text-[12px]"
          style={{ color: "var(--painel-muted)" }}
        >
          Aguardando logs…
        </div>
      ) : (
        allLines.map((l) => (
          <div
            key={l.id}
            data-testid="log-line"
            className="font-mono text-[11px] leading-[1.6]"
          >
            <span
              style={{ color: "var(--painel-muted-2)" }}
              className="mr-[8px] select-none"
            >
              {formatLogTime(l.ts)}
            </span>
            <span
              style={{ color: levelColor(l.level) }}
              className="mr-[8px] uppercase"
            >
              {l.level?.slice(0, 4)}
            </span>
            <span style={{ color: "var(--painel-text)" }}>
              {humanizeLogEvent(l)}
            </span>
          </div>
        ))
      )}
    </div>
  );

  // Inline variant (phase H "Logs" view): fill the parent, no overlay/close chrome.
  if (inline) {
    return (
      <div
        data-testid="painel-logs-inline"
        className="flex h-full min-h-0 flex-col bg-[var(--card)]"
      >
        <div className="flex flex-shrink-0 items-center gap-2.5 border-b border-[var(--painel-border-outer)] px-5 py-4">
          <span className="text-[13px] font-semibold text-[var(--painel-text)]">
            Logs · {sourceLabel}
          </span>
        </div>
        {logScroll}
      </div>
    );
  }

  return (
    <>
      {/* Overlay */}
      <div
        data-testid="painel-logs-overlay"
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(15,20,35,.32)",
          zIndex: 50,
          opacity: open ? 1 : 0,
          pointerEvents: open ? "auto" : "none",
          transition: "opacity .25s",
        }}
      />

      {/* Slide-over panel */}
      <aside
        data-testid="painel-logs-panel"
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          height: "100%",
          width: 480,
          maxWidth: "92vw",
          zIndex: 60,
          display: "flex",
          flexDirection: "column",
          transform: open ? "translateX(0)" : "translateX(100%)",
          transition: "transform .28s cubic-bezier(.4,0,.2,1)",
        }}
        className="border-l border-[var(--painel-border-outer)] bg-[var(--card)] shadow-[-12px_0_40px_rgba(15,23,42,.12)]"
      >
        {/* Header */}
        <div className="flex flex-shrink-0 items-center justify-between gap-2.5 border-b border-[var(--painel-border-outer)] px-5 py-4">
          <span className="text-[13px] font-semibold text-[var(--painel-text)]">
            Logs · {sourceLabel}
          </span>
          <button
            type="button"
            data-testid="painel-logs-close"
            onClick={onClose}
            className="cursor-pointer px-1 py-0.5 text-xl leading-none text-[var(--painel-muted-2)]"
          >
            ×
          </button>
        </div>

        {logScroll}
      </aside>
    </>
  );
}
