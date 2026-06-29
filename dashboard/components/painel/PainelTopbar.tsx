"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import {
  DEPTH_LABELS,
  ENGINE_REFETCH_INTERVAL_MS,
  SOURCE_LABELS,
  engineKeys,
  fetchEngineStatus,
  fetchTASessionStatus,
  startEngine,
  stopEngine,
  taSessionKeys,
  type EngineDepth,
  type EngineSource,
  type EngineState,
  type TASessionStatus,
} from "@/lib/engine-api";
import { PainelOrigem, type OrigemSource } from "@/components/painel/PainelOrigem";

/**
 * PainelTopbar — 58px chrome row of the Painel Brave shell.
 *
 * Left: static page title/subtitle (per active view). Right (design order):
 * TripAdvisor session pill · read-only "Origem {source}" · divider · motor
 * label + on/off switch. The motor switch + TA pill are wired to the REAL
 * engine-api through the BFF (mirrors EngineControl's mutation pattern). The
 * "Origem {source}" button opens the PainelOrigem modal (source pick + TA cURL
 * (re)inject); the motor switch collects a pipeline DEPTH before starting (the
 * backend 422s a depthless start); the TA pill + expiry toast are driven by the
 * real session `expires_in` (warn at 5 min). All colors resolve from the scoped
 * `.painel-light` CSS vars.
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

/** Depth options offered when starting the motor (order = least → most spend). */
const DEPTH_ORDER: EngineDepth[] = [
  "nascente",
  "nascente_rio",
  "nascente_rio_mar",
];

/** TA session expiry warn band (seconds) — mirrors the design's 5-min warn. */
const TA_WARN_SECONDS = 5 * 60;

/** Map the engine source enum onto the Origem modal's source preselect. */
function origemSourceFor(source: EngineSource): OrigemSource {
  return source === "tripadvisor" ? "tripadvisor" : "mtur";
}

/** Format a seconds count as m:ss. */
function fmtMMSS(seconds: number): string {
  const t = Math.max(0, Math.floor(seconds));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

/** PT-BR error explainer — ported from EngineControl (not exported there). */
function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    if (err.status === 409) return err.message || "Motor já está em execução.";
    return err.message;
  }
  return "Falha ao controlar o motor.";
}

/** True when a present session is inside the 5-min expiry warn band. */
function sessionWarning(s: TASessionStatus): boolean {
  return (
    s.present &&
    s.expires_in != null &&
    s.expires_in > 0 &&
    s.expires_in <= TA_WARN_SECONDS
  );
}

/**
 * TA session display label — driven by the REAL `expires_in` (warn at 5 min),
 * not a hardcoded clock. A present session inside the warn band shows the live
 * remaining mm:ss so the operator can recreate it before it lapses.
 */
function sessionLabel(s: TASessionStatus): string {
  if (s.present) {
    if (sessionWarning(s)) return `Expira em ${fmtMMSS(s.expires_in as number)}`;
    return "Pronta";
  }
  if (s.reason === "needs_bootstrap") return "Precisa bootstrap";
  return "Expirada";
}

/** TA session dot/text color (scoped status var) — amber in the warn band. */
function sessionColor(s: TASessionStatus): string {
  if (s.present) return sessionWarning(s) ? "var(--status-dlq)" : "var(--status-mar)";
  if (s.reason === "needs_bootstrap") return "var(--status-dlq)";
  return "var(--status-descarte)";
}

export function PainelTopbar({ title, subtitle }: PainelTopbarProps) {
  const qc = useQueryClient();
  const [origemOpen, setOrigemOpen] = useState(false);
  const [depthMenuOpen, setDepthMenuOpen] = useState(false);

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

  // START requires a pipeline depth (backend 422s a depthless start) — the
  // operator picks one from the depth menu, which is threaded into startEngine.
  const start = useMutation({
    mutationFn: (depth: EngineDepth) => startEngine({ depth }),
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
  // motorOn is driven by the operator-intent latch (enabled), not the transient
  // dispatch state. This keeps the switch ON when state returns to "idle" mid-run
  // (workers still processing) and only clears it when /stop is explicitly called.
  const motorOn = data?.enabled ?? (state !== "idle");

  // R2 client gate: when source is tripadvisor, require a valid session before
  // enabling the depth menu. Reuses the sessionStatus query (present && expires_in > 0).
  const taBlocked =
    source === "tripadvisor" &&
    (!sessionStatus?.present || (sessionStatus?.expires_in ?? 0) <= 0);

  // One-shot expiry toast driven by the real expires_in (warn at 5 min). The
  // ref guards against re-toasting on every 10s poll while inside the band.
  const warnedRef = useRef(false);
  useEffect(() => {
    if (sessionStatus && sessionWarning(sessionStatus)) {
      if (!warnedRef.current) {
        warnedRef.current = true;
        toast.warning(
          `Sessão TripAdvisor expira em ${fmtMMSS(sessionStatus.expires_in as number)} — recrie pelo modal Origem.`,
        );
      }
    } else {
      warnedRef.current = false;
    }
  }, [sessionStatus]);

  // Auto-off toast: when engine transitions from enabled→disabled mid-run
  // (R1: session expired during sweep, engine latched off by the worker).
  const prevEnabledRef = useRef<boolean | undefined>(undefined);
  useEffect(() => {
    const enabled = data?.enabled;
    if (prevEnabledRef.current === true && enabled === false) {
      toast.warning(
        "Motor TripAdvisor desligado — sessão expirada. Injete um cURL para reiniciar.",
      );
    }
    prevEnabledRef.current = enabled;
  }, [data?.enabled]);

  const onToggleMotor = () => {
    if (pending) return;
    if (motorOn) {
      // Engine is on (operator intent) — stop it.
      stop.mutate();
    } else {
      // R2: block if source=tripadvisor and no valid session
      if (taBlocked) {
        toast.error(
          "Injete uma sessão TripAdvisor válida antes de ligar o motor.",
        );
        return;
      }
      // Engine is off — open the depth picker to start.
      setDepthMenuOpen((v) => !v);
    }
  };

  const onPickDepth = (depth: EngineDepth) => {
    setDepthMenuOpen(false);
    start.mutate(depth);
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

        {/* Origem {source} — opens the source-pick + TA cURL (re)inject modal */}
        <button
          type="button"
          data-testid="painel-source"
          aria-haspopup="dialog"
          onClick={() => setOrigemOpen(true)}
          className="flex h-[34px] items-center gap-[8px] rounded-[8px] border bg-[var(--card)] px-[12px] text-[12.5px] font-medium text-[var(--painel-text)] hover:bg-[var(--painel-chip)]"
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
            {/* motorLabel is driven by motorOn (enabled latch), not state, so that
                enabled=true + state=idle still renders "Ligado" rather than "Desligado". */}
            Motor · {motorOn ? (state === "stopping" ? "Parando…" : "Ligado") : "Desligado"}
          </span>
          <div className="relative">
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

            {/* Depth picker — START requires a depth (backend 422s without one).
                Only shown when the engine is off (motorOn=false). */}
            {depthMenuOpen && !motorOn && (
              <div
                role="menu"
                data-testid="painel-depth-menu"
                className="absolute right-0 top-[30px] z-[20] w-[208px] rounded-[10px] border bg-[var(--card)] p-[6px] shadow-lg"
                style={{ borderColor: "var(--painel-border-outer)" }}
              >
                <div className="px-[8px] py-[5px] text-[10px] font-semibold uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
                  Profundidade da varredura
                </div>
                {DEPTH_ORDER.map((depth) => (
                  <button
                    key={depth}
                    type="button"
                    role="menuitem"
                    data-testid={`painel-depth-${depth}`}
                    disabled={pending}
                    onClick={() => onPickDepth(depth)}
                    className="block w-full rounded-[7px] px-[8px] py-[7px] text-left text-[12.5px] font-medium text-[var(--painel-text)] hover:bg-[var(--painel-chip)] disabled:opacity-50"
                  >
                    {DEPTH_LABELS[depth]}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Source-pick + TA cURL (re)inject modal */}
      <PainelOrigem
        open={origemOpen}
        onClose={() => setOrigemOpen(false)}
        initialSource={origemSourceFor(source)}
      />
    </div>
  );
}
