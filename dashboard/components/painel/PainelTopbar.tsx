"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import {
  ENGINE_REFETCH_INTERVAL_MS,
  SOURCE_LABELS,
  engineKeys,
  fetchEngineStatus,
  fetchTASessionStatus,
  startEngine,
  stopEngine,
  taSessionKeys,
  type EngineSource,
  type EngineState,
  type TASessionStatus,
} from "@/lib/engine-api";

/**
 * PainelTopbar — 58px chrome row of the Painel Brave shell.
 *
 * Left: static page title/subtitle (per active view). Right (design order):
 * TripAdvisor session pill · read-only "Origem {source}" · divider · motor
 * label + on/off switch. The motor switch + TA pill are wired to the REAL
 * engine-api through the BFF (mirrors EngineControl's mutation pattern). The
 * source switch modal is deferred (read-only this slice, per 17-CONTEXT). All
 * colors resolve from the scoped `.painel-light` CSS vars.
 */

interface PainelTopbarProps {
  title: string;
  subtitle: string;
}

const STATE_LABEL: Record<EngineState, string> = {
  idle: "Desligado",
  running: "Ligado",
  stopping: "Parando…",
};

/** PT-BR error explainer — ported from EngineControl (not exported there). */
function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    if (err.status === 409) return "Motor já está em execução.";
    return err.message;
  }
  return "Falha ao controlar o motor.";
}

/** TA session display label — ported from EngineControl. */
function sessionLabel(s: TASessionStatus): string {
  if (s.present) return "Pronta";
  if (s.reason === "needs_bootstrap") return "Precisa bootstrap";
  return "Expirada";
}

/** TA session dot/text color (scoped status var) — ported intent from EngineControl. */
function sessionColor(s: TASessionStatus): string {
  if (s.present) return "var(--status-mar)";
  if (s.reason === "needs_bootstrap") return "var(--status-dlq)";
  return "var(--status-descarte)";
}

export function PainelTopbar({ title, subtitle }: PainelTopbarProps) {
  const qc = useQueryClient();

  const { data } = useQuery({
    queryKey: engineKeys.status,
    queryFn: fetchEngineStatus,
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });

  const { data: sessionStatus } = useQuery({
    queryKey: taSessionKeys.status,
    queryFn: fetchTASessionStatus,
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });

  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: engineKeys.status });

  const start = useMutation({
    mutationFn: () => startEngine(),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => toast.success("Motor ligado — varredura iniciada"),
    onSettled: invalidate,
  });

  const stop = useMutation({
    mutationFn: () => stopEngine(),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => toast.success("Parando — drenando a fila atual"),
    onSettled: invalidate,
  });

  const state: EngineState = data?.state ?? "idle";
  const source: EngineSource = data?.source ?? "default";
  const pending = start.isPending || stop.isPending;
  const motorOn = state !== "idle";

  const onToggleMotor = () => {
    if (pending) return;
    if (state === "idle") {
      if (window.confirm("Ligar o motor de coleta?")) start.mutate();
    } else {
      stop.mutate();
    }
  };

  return (
    <div
      className="z-[5] flex h-[58px] flex-shrink-0 items-center justify-between gap-[16px] border-b bg-[var(--card)] px-[22px]"
      style={{ borderColor: "var(--painel-border-outer)" }}
      data-testid="painel-topbar"
    >
      {/* Page title / subtitle */}
      <div className="flex min-w-0 flex-col leading-[1.2]">
        <span className="text-[15.5px] font-bold tracking-[-0.3px]">
          {title}
        </span>
        <span className="text-[11.5px] text-[var(--painel-muted)]">
          {subtitle}
        </span>
      </div>

      {/* Right controls */}
      <div className="flex flex-shrink-0 items-center gap-[10px]">
        {/* TripAdvisor session pill (click = no-op this slice) */}
        {sessionStatus !== undefined && (
          <button
            type="button"
            data-testid="painel-ta-pill"
            aria-label="Sessão TripAdvisor"
            className="flex h-[34px] items-center gap-[7px] rounded-[8px] border bg-[var(--card)] px-[12px] text-[12px] font-medium"
            style={{ borderColor: "var(--painel-border-outer)" }}
          >
            <span
              className="h-[7px] w-[7px] rounded-full"
              style={{ background: sessionColor(sessionStatus) }}
              aria-hidden
            />
            <span style={{ color: sessionColor(sessionStatus) }}>
              {sessionLabel(sessionStatus)}
            </span>
          </button>
        )}

        {/* Origem {source} — read-only (switch modal deferred per 17-CONTEXT) */}
        <button
          type="button"
          data-testid="painel-source"
          className="flex h-[34px] items-center gap-[8px] rounded-[8px] border bg-[var(--card)] px-[12px] text-[12.5px] font-medium text-[var(--painel-text)]"
          style={{ borderColor: "var(--painel-border-outer)" }}
        >
          <span
            className="h-[7px] w-[7px] rounded-[2px]"
            style={{ background: "var(--painel-navy)" }}
            aria-hidden
          />
          Origem <strong className="font-semibold">{SOURCE_LABELS[source]}</strong>
        </button>

        {/* Divider */}
        <div
          className="h-[24px] w-px"
          style={{ background: "var(--painel-border-outer)" }}
          aria-hidden
        />

        {/* Motor label + switch */}
        <div className="flex items-center gap-[9px]">
          <span
            className="text-[12px] font-medium text-[var(--painel-muted)]"
            data-testid="painel-motor-state"
          >
            Motor · {STATE_LABEL[state]}
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={motorOn}
            aria-label="Ligar/desligar motor"
            data-testid="painel-motor-switch"
            disabled={pending}
            onClick={onToggleMotor}
            className="relative h-[22px] w-[40px] flex-shrink-0 rounded-full transition-colors disabled:opacity-60"
            style={{
              background: motorOn
                ? "var(--painel-navy)"
                : "var(--painel-border-outer)",
            }}
          >
            <span
              className="absolute top-[2px] h-[18px] w-[18px] rounded-full bg-white transition-all"
              style={{ left: motorOn ? "20px" : "2px" }}
              aria-hidden
            />
          </button>
        </div>
      </div>
    </div>
  );
}
