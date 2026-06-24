"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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

/** Depth options in cost order — free first. */
const DEPTH_OPTIONS: EngineDepth[] = [
  "nascente",
  "nascente_rio",
  "nascente_rio_mar",
];

/** Source options — standard first. */
const SOURCE_OPTIONS: EngineSource[] = ["default", "tripadvisor"];

/** Brazilian UF codes — 27 states for TripAdvisor UF chip multi-select. */
const BR_UFS = [
  "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO",
  "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR",
  "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
];

/**
 * EngineControl — the operator start/stop panel for the Brave collection sweep.
 *
 * Idle by default: the engine is off until an operator presses "Ligar motor".
 * Start fans out the full destinos+atrativos sweep; Stop drains gracefully
 * (the in-flight UF finishes, then the engine returns to idle). Polls status
 * every 10s for live visual feedback (state, current UF, progress, counts).
 */

const STATE_LABEL: Record<EngineState, string> = {
  idle: "Parado",
  running: "Varrendo",
  stopping: "Parando…",
};

const STATE_DOT: Record<EngineState, string> = {
  idle: "bg-muted-foreground",
  running: "bg-emerald-500 animate-pulse",
  stopping: "bg-amber-500 animate-pulse",
};

function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    if (err.status === 409) return "Motor já está em execução.";
    return err.message;
  }
  return "Falha ao controlar o motor.";
}

/** Derive the PT-BR display label for a TA session status. */
function sessionLabel(s: TASessionStatus): string {
  if (s.present) return "Pronta";
  if (s.reason === "needs_bootstrap") return "Precisa bootstrap";
  return "Expirada";
}

/** Derive the Tailwind colour class for a TA session status. */
function sessionColor(s: TASessionStatus): string {
  if (s.present) return "text-emerald-600";
  if (s.reason === "needs_bootstrap") return "text-amber-600";
  return "text-rose-600";
}

export function EngineControl() {
  const qc = useQueryClient();
  const [selectedDepth, setSelectedDepth] = useState<EngineDepth | undefined>();
  const [selectedSource, setSelectedSource] = useState<EngineSource>("default");
  const [selectedUfs, setSelectedUfs] = useState<string[]>([...BR_UFS]);

  const { data, isPending } = useQuery({
    queryKey: engineKeys.status,
    queryFn: fetchEngineStatus,
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });

  // Show the session-health pill when TripAdvisor is selected OR when the engine
  // is actively running with source=tripadvisor (even after the source selector
  // is hidden by the running-state UI branch).
  const state: EngineState = data?.state ?? "idle";
  const showSessionStatus =
    selectedSource === "tripadvisor" ||
    (state !== "idle" && data?.source === "tripadvisor");

  const { data: sessionStatus } = useQuery({
    queryKey: taSessionKeys.status,
    queryFn: fetchTASessionStatus,
    enabled: showSessionStatus,
    refetchInterval: showSessionStatus ? ENGINE_REFETCH_INTERVAL_MS : false,
    refetchOnWindowFocus: false,
  });

  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: engineKeys.status });

  const start = useMutation({
    mutationFn: (depth: EngineDepth) =>
      startEngine({
        depth,
        source: selectedSource,
        ...(selectedSource === "tripadvisor" ? { ufs: selectedUfs } : {}),
      }),
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

  const pending = start.isPending || stop.isPending;
  const counts = data?.counts;
  const progressPct =
    data && data.ufs_total > 0
      ? Math.round((data.ufs_done / data.ufs_total) * 100)
      : 0;

  return (
    <section
      className="rounded-md border p-4"
      data-testid="engine-control"
      aria-label="Controle do motor Brave"
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span
            className={`inline-block h-2.5 w-2.5 rounded-full ${STATE_DOT[state]}`}
            aria-hidden
          />
          <div>
            <h2 className="text-[14px] font-semibold">Motor de coleta</h2>
            <p className="text-[12px] text-muted-foreground" data-testid="engine-state">
              {isPending ? "Carregando…" : STATE_LABEL[state]}
              {state === "running" && data?.current_uf
                ? ` · UF ${data.current_uf}`
                : ""}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {state === "idle" ? (
            <>
              <div
                className="flex flex-wrap items-center gap-1.5"
                role="radiogroup"
                aria-label="Profundidade da varredura"
                data-testid="engine-depth"
              >
                {DEPTH_OPTIONS.map((depth) => {
                  const active = selectedDepth === depth;
                  return (
                    <button
                      key={depth}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      disabled={pending}
                      onClick={() => setSelectedDepth(depth)}
                      data-testid={`engine-depth-${depth}`}
                      className={`rounded-md border px-2.5 py-1 text-[12px] transition-colors ${
                        active
                          ? "border-primary bg-primary/10 font-medium text-foreground"
                          : "border-border text-muted-foreground hover:bg-muted"
                      }`}
                    >
                      {DEPTH_LABELS[depth]}
                    </button>
                  );
                })}
              </div>
              <div
                className="flex flex-wrap items-center gap-1.5"
                role="radiogroup"
                aria-label="Fonte de coleta"
                data-testid="engine-source"
              >
                {SOURCE_OPTIONS.map((src) => {
                  const active = selectedSource === src;
                  return (
                    <button
                      key={src}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      disabled={pending}
                      onClick={() => setSelectedSource(src)}
                      data-testid={`engine-source-${src}`}
                      className={`rounded-md border px-2.5 py-1 text-[12px] transition-colors ${
                        active
                          ? "border-primary bg-primary/10 font-medium text-foreground"
                          : "border-border text-muted-foreground hover:bg-muted"
                      }`}
                    >
                      {SOURCE_LABELS[src]}
                    </button>
                  );
                })}
              </div>
              {selectedSource === "tripadvisor" && (
                <div
                  className="flex flex-wrap items-center gap-1"
                  data-testid="engine-uf-chips"
                >
                  {BR_UFS.map((uf) => {
                    const selected = selectedUfs.includes(uf);
                    return (
                      <button
                        key={uf}
                        type="button"
                        aria-pressed={selected}
                        disabled={pending}
                        onClick={() =>
                          setSelectedUfs((prev) =>
                            selected
                              ? prev.filter((u) => u !== uf)
                              : [...prev, uf],
                          )
                        }
                        data-testid={`engine-uf-${uf}`}
                        className={`rounded border px-1.5 py-0.5 font-mono text-[11px] transition-colors ${
                          selected
                            ? "border-primary bg-primary/10 font-medium text-foreground"
                            : "border-border text-muted-foreground hover:bg-muted"
                        }`}
                      >
                        {uf}
                      </button>
                    );
                  })}
                </div>
              )}
              <Button
                size="sm"
                disabled={pending || !selectedDepth}
                onClick={() => selectedDepth && start.mutate(selectedDepth)}
                data-testid="engine-start"
              >
                Ligar motor
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              variant="destructive"
              disabled={pending || state === "stopping"}
              onClick={() => stop.mutate()}
              data-testid="engine-stop"
            >
              {state === "stopping" ? "Parando…" : "Parar motor"}
            </Button>
          )}
        </div>
      </div>

      {/* Active-depth read-back — the depth the running sweep was started with */}
      {state !== "idle" && data?.depth && (
        <p
          className="mt-2 text-[12px] text-muted-foreground"
          data-testid="engine-active-depth"
        >
          Profundidade: {DEPTH_LABELS[data.depth]}
        </p>
      )}

      {/* Active-source read-back — the collection source the running sweep was started with */}
      {state !== "idle" && data?.source && (
        <p
          className="mt-1 text-[12px] text-muted-foreground"
          data-testid="engine-active-source"
        >
          Fonte: {SOURCE_LABELS[data.source]}
        </p>
      )}

      {/* Session health pill — visible when TripAdvisor source is selected or running */}
      {showSessionStatus && sessionStatus !== undefined && (
        <div
          className="mt-2 flex items-center gap-2 text-[12px]"
          data-testid="ta-session-health"
        >
          <span className="text-muted-foreground">Sessão TA:</span>
          <span
            data-testid="ta-session-status"
            className={`font-medium ${sessionColor(sessionStatus)}`}
          >
            {sessionLabel(sessionStatus)}
          </span>
          {/* WR-03: only render the minutes badge when there is real remaining
              TTL. expires_in can be 0 (no-TTL or just-expired key) — rendering
              "(0 min)" beside a healthy pill would mislead the operator. */}
          {sessionStatus.present &&
            sessionStatus.expires_in !== undefined &&
            sessionStatus.expires_in > 0 && (
              <span className="text-muted-foreground">
                ({Math.round(sessionStatus.expires_in / 60)} min)
              </span>
            )}
        </div>
      )}

      {/* Progress — UFs fanned out this run */}
      {state !== "idle" && data && data.ufs_total > 0 && (
        <div className="mt-3" data-testid="engine-progress">
          <div className="mb-1 flex justify-between text-[11px] text-muted-foreground tabular-nums">
            <span>
              UFs {data.ufs_done}/{data.ufs_total}
            </span>
            <span>{progressPct}%</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-emerald-500 transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Live pipeline counts — visual feedback of what's flowing */}
      {counts && (
        <div
          className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4"
          data-testid="engine-counts"
        >
          <CountTile label="Nascente" value={counts.nascente} />
          <CountTile label="Mar" value={counts.mar} />
          <CountTile label="DLQ" value={counts.rio.dlq} />
          <CountTile
            label="Atrativos"
            value={Object.values(counts.atrativos_by_sub_state).reduce(
              (a, b) => a + b,
              0,
            )}
          />
        </div>
      )}
    </section>
  );
}

function CountTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-[18px] font-semibold tabular-nums">{value}</div>
    </div>
  );
}
