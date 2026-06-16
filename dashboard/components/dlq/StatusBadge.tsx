"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * StatusBadge — routing / sub_state badge using the UI-SPEC semantic status
 * colors (data-encoding, NOT the 10% accent budget):
 *   Mar / high-confidence  → green  (--status-mar)
 *   DLQ / borderline       → amber  (--status-dlq)
 *   descarte / rejected    → red    (--status-descarte / --destructive)
 *
 * Small badge only (never a large fill). PT-BR domain terms keep their canonical
 * pipeline spelling (Mar / DLQ / descarte). Unknown routings fall back to a
 * neutral outline badge showing the raw value.
 */
export type Routing = "mar" | "dlq" | "descarte" | (string & {});

const ROUTING_LABEL: Record<string, string> = {
  mar: "Mar",
  dlq: "DLQ",
  descarte: "descarte",
};

const ROUTING_CLASS: Record<string, string> = {
  mar: "border-transparent bg-[var(--status-mar)]/15 text-[var(--status-mar)]",
  dlq: "border-transparent bg-[var(--status-dlq)]/15 text-[var(--status-dlq)]",
  descarte:
    "border-transparent bg-[var(--status-descarte)]/15 text-[var(--status-descarte)]",
};

export function StatusBadge({
  routing,
  subState,
  className,
}: {
  routing: Routing;
  subState?: string | null;
  className?: string;
}) {
  const key = routing.toLowerCase();
  const label = ROUTING_LABEL[key] ?? routing;
  const tone = ROUTING_CLASS[key];

  return (
    <span className="inline-flex items-center gap-1">
      <Badge
        variant={tone ? "outline" : "secondary"}
        className={cn("font-mono text-[12px] font-semibold", tone, className)}
      >
        {label}
      </Badge>
      {subState ? (
        <span className="font-mono text-[12px] text-muted-foreground">
          {subState}
        </span>
      ) : null}
    </span>
  );
}
