"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * StageBadge — pipeline stage badge using Norteia CSS var tokens.
 *
 * Extends StatusBadge (routing only) to cover all CMS surface needs:
 *   - routing: mar/dlq/descarte/in_progress
 *   - subState: all 5 atrativo FSM states (navy gradient by depth)
 *   - score band: ≥85 green, 40–84.9 amber, <40 red
 *   - source: neutral chip
 *   - validationPending: amber flag chip
 *
 * Only CSS var references used — no hardcoded hex colors.
 */

export interface StageBadgeProps {
  /**
   * nascente: record exists in the Nascente layer with no Rio row yet
   * (the records the `Apenas nascente` engine depth parks at the free layer).
   * Prop-driven — stage is implicit by table membership; this is the visual.
   */
  nascente?: boolean;
  routing?: string | null;
  subState?: string | null;
  score?: number | null;
  source?: string | null;
  validationPending?: boolean;
  className?: string;
}

const ROUTING_CLASS: Record<string, string> = {
  mar: "border-transparent bg-[var(--status-mar)]/15 text-[var(--status-mar)]",
  dlq: "border-transparent bg-[var(--status-dlq)]/15 text-[var(--status-dlq)]",
  descarte:
    "border-transparent bg-[var(--status-descarte)]/15 text-[var(--status-descarte)]",
  in_progress:
    "border-transparent bg-[var(--color-primary)]/10 text-[var(--color-primary)]",
};

const ROUTING_LABEL: Record<string, string> = {
  mar: "MAR",
  dlq: "DLQ",
  descarte: "Descarte",
  in_progress: "Em andamento",
};

const SUB_STATE_CLASS: Record<string, string> = {
  discovered:
    "border-transparent bg-[var(--color-primary)]/10 text-[var(--color-primary)]",
  contacts_found:
    "border-transparent bg-[var(--color-primary)]/20 text-[var(--color-primary)]",
  signals_gathered:
    "border-transparent bg-[var(--color-primary)]/30 text-[var(--color-primary)]",
  aguardando_consulta_whatsapp:
    "border-transparent bg-[var(--status-dlq)]/15 text-[var(--status-dlq)]",
  whatsapp_in_progress:
    "border-transparent bg-[var(--status-dlq)]/25 text-[var(--status-dlq)]",
};

const SOURCE_LABEL: Record<string, string> = {
  mtur: "Mtur",
  notebooklm: "NotebookLM",
  desmembramento: "Desmemb.",
  places_discovery: "Places",
};

function scoreClass(score: number | null): string {
  if (score == null) return "";
  if (score >= 85)
    return "border-transparent bg-[var(--status-mar)]/15 text-[var(--status-mar)]";
  if (score >= 40)
    return "border-transparent bg-[var(--status-dlq)]/15 text-[var(--status-dlq)]";
  return "border-transparent bg-[var(--status-descarte)]/15 text-[var(--status-descarte)]";
}

function toTitleCase(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function StageBadge({
  nascente,
  routing,
  subState,
  score,
  source,
  validationPending,
  className,
}: StageBadgeProps) {
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-1", className)}>
      {nascente ? (
        <Badge
          variant="outline"
          className="border-transparent bg-[var(--color-primary)]/15 font-mono text-[12px] font-semibold text-[var(--color-primary)]"
        >
          Nascente
        </Badge>
      ) : null}

      {routing ? (
        <Badge
          variant="outline"
          className={cn(
            "font-mono text-[12px] font-semibold",
            ROUTING_CLASS[routing.toLowerCase()] ??
              "bg-muted/30 text-muted-foreground",
          )}
        >
          {ROUTING_LABEL[routing.toLowerCase()] ?? routing}
        </Badge>
      ) : null}

      {subState ? (
        <Badge
          variant="outline"
          className={cn(
            "font-mono text-[12px] font-semibold",
            SUB_STATE_CLASS[subState] ?? "bg-muted/30 text-muted-foreground",
          )}
        >
          {toTitleCase(subState)}
        </Badge>
      ) : null}

      {score != null ? (
        <Badge
          variant="outline"
          className={cn("font-mono text-[12px] font-semibold", scoreClass(score))}
        >
          {score.toFixed(1)}
        </Badge>
      ) : null}

      {source ? (
        <Badge
          variant="outline"
          className="border-transparent bg-muted/30 font-mono text-[12px] font-semibold text-muted-foreground"
        >
          {SOURCE_LABEL[source] ?? source}
        </Badge>
      ) : null}

      {validationPending ? (
        <Badge
          variant="outline"
          className="border-transparent bg-[var(--status-dlq)]/10 font-mono text-[12px] font-semibold text-[var(--status-dlq)]"
        >
          Aguardando
        </Badge>
      ) : null}
    </span>
  );
}
