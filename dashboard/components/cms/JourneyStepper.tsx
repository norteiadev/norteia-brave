"use client";

import { cn } from "@/lib/utils";

/**
 * JourneyStepper — visual pipeline journey for destinos and atrativos.
 *
 * Destino steps (4): Nascente → Rio/Score → DLQ → Mar
 * Atrativo steps (7): discovered → contacts_found → signals_gathered →
 *   score → gate → outreach → Mar/DLQ
 *
 * Step completion is derived from auditLog rows + routing/subState props:
 *   - Destino steps 1-2 are inferred from record existence (no AuditLog rows
 *     written for these pipeline steps).
 *   - compact prop renders a horizontal abbreviated step bar (circles only).
 */

export interface AuditLogRow {
  action: string;
  actor: string | null;
  after_state: Record<string, unknown> | null;
  created_at: string | null;
}

export interface JourneyStepperProps {
  entityType: "destination" | "attraction";
  routing: string;
  subState?: string | null;
  score?: number | null;
  auditLog: AuditLogRow[];
  compact?: boolean;
}

interface StepDef {
  key: string;
  label: string;
  description?: string;
}

/** Destino step definitions (pipeline order). */
function destinoSteps(score: number | null | undefined): StepDef[] {
  return [
    { key: "nascente", label: "Nascente", description: "Ingestão" },
    {
      key: "rio",
      label: "Rio / Score",
      description:
        score != null
          ? `Score ${score.toFixed(1)}`
          : "Aguardando pontuação",
    },
    {
      key: "dlq",
      label: "DLQ",
      description: "Aguardando validação humana",
    },
    {
      key: "mar",
      label: "Mar",
      description: "Registro canônico publicado",
    },
  ];
}

/** Atrativo step definitions (FSM order). */
const ATRATIVO_STEPS: StepDef[] = [
  { key: "discovered", label: "Descoberto" },
  { key: "contacts_found", label: "Contatos" },
  { key: "signals_gathered", label: "Sinais" },
  { key: "score", label: "Score" },
  { key: "gate", label: "Gate WhatsApp" },
  { key: "outreach", label: "Outreach" },
  { key: "terminal", label: "Mar / DLQ" },
];

/** Atrativo FSM sub_state → step key index (0-based). */
const ATRATIVO_SUB_STATE_INDEX: Record<string, number> = {
  discovered: 0,
  contacts_found: 1,
  signals_gathered: 2,
  aguardando_consulta_whatsapp: 3,
  whatsapp_in_progress: 5,
};

type StepStatus = "completed" | "current" | "pending";

interface StepState {
  status: StepStatus;
  /** AuditLog row that completed this step (for timestamp display). */
  auditRow?: AuditLogRow;
}

/** Compute completion states for destino journey. */
function destinoStepStates(
  routing: string,
  score: number | null | undefined,
  auditLog: AuditLogRow[],
): StepState[] {
  const DLQ_ACTIONS = new Set([
    "dlq_validated",
    "dlq_rejected",
    "dlq_reprocessed",
  ]);

  const dlqActionRows = auditLog.filter((r) => DLQ_ACTIONS.has(r.action));
  const dlqDone = dlqActionRows.length > 0;
  const marDone = routing === "mar";
  const dlqCurrent = routing === "dlq" && !dlqDone;

  // Step 0: Nascente — always completed (record exists = was ingested)
  // Step 1: Rio/Score — completed if score is populated
  // Step 2: DLQ gate — completed if dlq action exists; current if routing=dlq+no action
  // Step 3: Mar — completed/current if routing=mar
  return [
    { status: "completed" },
    { status: score != null ? "completed" : "pending" },
    {
      status: dlqDone
        ? "completed"
        : dlqCurrent
          ? "current"
          : "pending",
      auditRow: dlqActionRows[dlqActionRows.length - 1],
    },
    {
      status: marDone ? "completed" : "pending",
    },
  ];
}

/** Compute completion states for atrativo journey. */
function atrativoStepStates(
  routing: string,
  subState: string | null | undefined,
  auditLog: AuditLogRow[],
): StepState[] {
  // Determine current step index from sub_state or routing
  let currentIdx = -1;
  if (routing === "mar") {
    currentIdx = 6; // terminal (completed)
  } else if (routing === "descarte") {
    currentIdx = 6; // terminal (rejected path)
  } else if (subState && subState in ATRATIVO_SUB_STATE_INDEX) {
    currentIdx = ATRATIVO_SUB_STATE_INDEX[subState];
  }

  // Map AuditLog actions to step completions
  const completedSteps = new Set<number>();
  const stepAuditRows: (AuditLogRow | undefined)[] = new Array(7).fill(
    undefined,
  );

  for (const row of auditLog) {
    if (row.action === "atrativo_discovered") {
      completedSteps.add(0);
      stepAuditRows[0] = row;
    } else if (row.action === "sub_state_advanced") {
      const afterSubState = row.after_state?.sub_state as string | undefined;
      if (afterSubState && afterSubState in ATRATIVO_SUB_STATE_INDEX) {
        const idx = ATRATIVO_SUB_STATE_INDEX[afterSubState];
        completedSteps.add(idx);
        stepAuditRows[idx] = row;
      }
    } else if (row.action === "whatsapp_gate_approved") {
      completedSteps.add(4);
      stepAuditRows[4] = row;
    } else if (
      row.action === "whatsapp_gate_rejected" ||
      row.action === "hard_descarte"
    ) {
      completedSteps.add(6);
      stepAuditRows[6] = row;
    }
  }

  if (routing === "mar" || routing === "descarte") {
    completedSteps.add(6);
  }

  return ATRATIVO_STEPS.map((_, i) => {
    if (completedSteps.has(i)) {
      return { status: "completed", auditRow: stepAuditRows[i] };
    }
    if (i === currentIdx) {
      return { status: "current", auditRow: stepAuditRows[i] };
    }
    return { status: "pending" };
  });
}

/** Format ISO timestamp to a short human-readable form. */
function fmtTs(ts: string | null | undefined): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

export function JourneyStepper({
  entityType,
  routing,
  subState,
  score,
  auditLog,
  compact = false,
}: JourneyStepperProps) {
  const steps =
    entityType === "destination" ? destinoSteps(score) : ATRATIVO_STEPS;
  const states =
    entityType === "destination"
      ? destinoStepStates(routing, score, auditLog)
      : atrativoStepStates(routing, subState, auditLog);

  if (compact) {
    return (
      <ol className="flex items-center gap-1" aria-label="Pipeline journey">
        {steps.map((step, i) => {
          const { status } = states[i];
          return (
            <li key={step.key} className="flex items-center gap-1">
              <span
                title={step.label}
                aria-label={`${step.label}: ${status}`}
                className={cn(
                  "flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[9px] font-bold",
                  status === "completed" &&
                    "bg-[var(--status-mar)] text-white",
                  status === "current" &&
                    "ring-2 ring-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]",
                  status === "pending" && "bg-muted text-muted-foreground",
                )}
              >
                {status === "completed" ? "✓" : i + 1}
              </span>
              {i < steps.length - 1 && (
                <span className="h-px w-2 bg-border" />
              )}
            </li>
          );
        })}
      </ol>
    );
  }

  return (
    <ol className="flex flex-col gap-2" aria-label="Pipeline journey">
      {steps.map((step, i) => {
        const { status, auditRow } = states[i];
        return (
          <li key={step.key} className="flex items-start gap-3">
            {/* Step indicator circle */}
            <span
              aria-label={`${step.label}: ${status}`}
              className={cn(
                "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
                status === "completed" &&
                  "bg-[var(--status-mar)] text-white",
                status === "current" &&
                  "ring-2 ring-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]",
                status === "pending" && "bg-muted text-muted-foreground",
              )}
            >
              {status === "completed" ? "✓" : i + 1}
            </span>

            {/* Step content */}
            <div className="flex flex-col gap-0.5 pt-0.5">
              <span
                className={cn(
                  "text-[13px] font-semibold leading-tight",
                  status === "completed" && "text-[var(--status-mar)]",
                  status === "current" && "text-[var(--color-primary)]",
                  status === "pending" && "text-muted-foreground",
                )}
              >
                {step.label}
              </span>

              {step.description && (
                <span className="text-[12px] text-muted-foreground">
                  {step.description}
                </span>
              )}

              {/* Audit row: actor + timestamp */}
              {auditRow && (
                <span className="font-mono text-[11px] text-muted-foreground tabular-nums">
                  {fmtTs(auditRow.created_at)}
                  {auditRow.actor ? ` · ${auditRow.actor}` : ""}
                </span>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
