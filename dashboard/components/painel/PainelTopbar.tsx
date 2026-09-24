"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Terminal } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import {
  DEPTH_LABELS,
  ENGINE_REFETCH_INTERVAL_MS,
  SOURCE_LABELS,
  engineKeys,
  fetchEngineStatus,
  fetchTASessionStatus,
  setEngineMode,
  startEngine,
  taSessionKeys,
  type EngineDepth,
  type EngineSource,
  type EngineState,
  type TASessionStatus,
} from "@/lib/engine-api";
import { ENGINE_MODE_LABELS, type EngineMode } from "@/lib/config-api";
import { BR_UFS } from "@/lib/painel-data";
import { PainelLogs } from "@/components/painel/PainelLogs";
import { PainelOrigem } from "@/components/painel/PainelOrigem";

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

/** The tri-state motor modes, in Ligar/Pausar/Desligar order. */
const MOTOR_MODES: EngineMode[] = ["LIGADO", "PAUSADO", "DESLIGADO"];

/**
 * Per-mode button metadata: the imperative ACTION label/testid (Ligar/Pausar/
 * Desligar) — distinct from the state adjective (Ligado/Pausado/Desligado) shown
 * in the "Motor · …" label.
 */
const MOTOR_ACTION_META: Record<EngineMode, { label: string; testid: string }> = {
  LIGADO: { label: "Ligar", testid: "painel-motor-ligar" },
  PAUSADO: { label: "Pausar", testid: "painel-motor-pausar" },
  DESLIGADO: { label: "Desligar", testid: "painel-motor-desligar" },
};

/** Depth options offered when starting the motor (order = least → most spend). */
const DEPTH_ORDER: EngineDepth[] = [
  "nascente",
  "nascente_rio",
  "nascente_rio_mar",
];

/** TA session expiry warn band (seconds) — mirrors the design's 5-min warn. */
const TA_WARN_SECONDS = 5 * 60;

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

/** PT-BR pause-reason banner text (quick-260918-ohm) — reason + which provider tripped it. */
function pauseReasonLabel(reason: { reason: string; provider: string | null }): string {
  if (reason.reason === "daily_budget") return "Motor pausado: orçamento diário atingido";
  if (reason.provider) return `Motor pausado: sem saldo (${reason.provider})`;
  return "Motor pausado: sem saldo";
}

export function PainelTopbar({ title, subtitle }: PainelTopbarProps) {
  const qc = useQueryClient();
  const [origemOpen, setOrigemOpen] = useState(false);
  const [depthMenuOpen, setDepthMenuOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  // Bug 2: cold-start UF scope. "" = Todo o Brasil (backend uses all 27 UFs when
  // ufs is omitted); a UF code scopes the sweep to a single UF. Source-independent.
  const [startUf, setStartUf] = useState<string>("");
  // Optional per-UF attraction cap (test-run throttle). "" = no cap (full sweep);
  // a positive integer stops each UF after N attractions are processed.
  const [maxPerUf, setMaxPerUf] = useState<string>("");

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
  // source is already in scope from line below (data?.source ?? "tripadvisor")
  // and is passed so the selected origem lane actually reaches the sweep orchestrator.
  // action "describe" (Gerar descrições) needs no depth/source — the backend
  // runs the per-UF description lane instead of a sweep.
  const start = useMutation({
    mutationFn: (
      vars:
        | { action: "sweep"; depth: EngineDepth; ufs?: string[]; maxPerUf?: number }
        | { action: "describe"; ufs?: string[]; maxPerUf?: number },
    ) =>
      startEngine(
        vars.action === "describe"
          ? {
              action: "describe",
              ufs: vars.ufs,
              max_atrativos_per_uf: vars.maxPerUf,
            }
          : {
              depth: vars.depth,
              source,
              ufs: vars.ufs,
              max_atrativos_per_uf: vars.maxPerUf,
            },
      ),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: (_res, vars) =>
      toast.success(
        vars.action === "describe"
          ? "Motor ligado — gerando descrições"
          : "Motor ligado — varredura iniciada",
      ),
    onSettled: invalidate,
  });

  // Tri-state motor mode (phase H edit-lock) — POST /api/v1/engine/mode. Pausar /
  // Desligar go straight through here; a warm Ligar (resume from Pausado) too. A
  // cold Ligar (engine off) still runs the depth-picker start below.
  const setMode = useMutation({
    mutationFn: (mode: EngineMode) => setEngineMode(mode),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: (res) =>
      toast.success(`Modo · ${ENGINE_MODE_LABELS[res.mode]}`),
    onSettled: invalidate,
  });

  const state: EngineState = data?.state ?? "idle";
  const source: EngineSource = data?.source ?? "tripadvisor";
  const mode: EngineMode = data?.mode ?? "DESLIGADO";
  const pending = start.isPending || setMode.isPending;
  // motorOn is driven by the operator-intent latch (enabled), not the transient
  // dispatch state. This keeps the switch ON when state returns to "idle" mid-run
  // (workers still processing) and only clears it when /stop is explicitly called.
  const motorOn = data?.enabled ?? (state !== "idle");

  // Tri-state sync phase for the topbar indicator. Prefer the backend-computed
  // sync_phase (idle | syncing | synced); fall back to deriving from motorOn
  // (syncing while the motor is on, else idle) so a status payload without the
  // field still renders a sensible two-state indicator.
  const syncPhase: "idle" | "syncing" | "synced" =
    data?.sync_phase ?? (motorOn ? "syncing" : "idle");

  // R2 client gate: when source is tripadvisor, require a valid session before
  // enabling the Varredura depth buttons. Reuses the sessionStatus query (present && expires_in > 0).
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
  // (R1: session expired during sweep, engine latched off by the worker). Only
  // when the session really is gone — a run that simply finished (e.g. Gerar
  // descrições, which never uses the TA session) also flips enabled→false.
  const prevEnabledRef = useRef<boolean | undefined>(undefined);
  useEffect(() => {
    const enabled = data?.enabled;
    if (prevEnabledRef.current === true && enabled === false && taBlocked) {
      toast.warning(
        "Motor TripAdvisor desligado — sessão expirada. Injete um cURL para reiniciar.",
      );
    }
    prevEnabledRef.current = enabled;
  }, [data?.enabled, taBlocked]);

  // Close the depth menu on any click outside its wrapper (Ligar included, so the
  // Ligar toggle keeps working) or on Escape. Mirrors PainelFilters' popover pattern.
  const motorRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!depthMenuOpen) return;
    function onDown(e: MouseEvent) {
      if (motorRef.current && !motorRef.current.contains(e.target as Node)) {
        setDepthMenuOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setDepthMenuOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [depthMenuOpen]);

  const onLigar = () => {
    if (pending) return;
    // A GENUINELY-running LIGADO is a no-op. But a DESYNCED LIGADO — mode says LIGADO
    // while the engine is really off (motorOn=false, e.g. after an R1 session-expiry
    // auto-off or an idle /stop that left mode=LIGADO) — must RECOVER, not swallow the
    // click: fall through to the cold-start path so the operator can restart the motor.
    if (mode === "LIGADO" && motorOn) return;
    if (motorOn) {
      // Warm resume (a run is still active, e.g. from PAUSADO) — just relock via mode.
      setMode.mutate("LIGADO");
      return;
    }
    // Engine is off — open the start popover. The R2 TA-session gate (taBlocked)
    // only disables the Varredura section; Descrição needs no TA session.
    setDepthMenuOpen((v) => !v);
  };

  const onPausar = () => {
    if (pending || mode === "PAUSADO") return;
    setMode.mutate("PAUSADO");
  };

  const onDesligar = () => {
    if (pending || mode === "DESLIGADO") return;
    setMode.mutate("DESLIGADO");
  };

  // Shared popover scope: the picked UF (or omit ufs for Todo o Brasil) + the
  // optional per-UF cap, forwarded only as a positive integer (empty / invalid ⇒
  // no cap). Number("") is 0, so the >= 1 guard covers it.
  const startScope = () => {
    const parsedMax = Number(maxPerUf);
    return {
      ufs: startUf ? [startUf] : undefined,
      maxPerUf:
        Number.isInteger(parsedMax) && parsedMax >= 1 ? parsedMax : undefined,
    };
  };

  const onPickDepth = (depth: EngineDepth) => {
    setDepthMenuOpen(false);
    start.mutate({ action: "sweep", depth, ...startScope() });
  };

  const onDescribe = () => {
    setDepthMenuOpen(false);
    start.mutate({ action: "describe", ...startScope() });
  };

  // Continuar (quick-260918-ohm): resume the paused action after a manual confirm
  // that the provider's balance/quota was recharged. Rebuilds the start body from
  // the server-polled pause_reason.action — never trusts client-held depth state.
  // A null action (a chain task hit the wall, no run to resume) only lifts the
  // pause: mode LIGADO clears pause_reason server-side and starts nothing.
  const pauseReason = data?.pause_reason ?? null;
  const onContinuar = () => {
    if (!pauseReason) return;
    if (!window.confirm("Saldo recarregado?")) return;
    if (pauseReason.action === "describe") {
      start.mutate({ action: "describe" });
    } else if (pauseReason.action === "sweep") {
      start.mutate({ action: "sweep", depth: data?.depth ?? "nascente_rio" });
    } else {
      setMode.mutate("LIGADO");
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

      {/* Reasoned-pause banner (quick-260918-ohm) — provider balance wall / daily budget */}
      {pauseReason && (
        <div
          data-testid="painel-pause-banner"
          className="flex h-[34px] flex-shrink-0 items-center gap-[10px] rounded-[8px] border px-[12px] text-[12px] font-medium"
          style={{
            borderColor: "var(--status-dlq)",
            background: "var(--status-dlq)",
            color: "white",
          }}
        >
          <span>{pauseReasonLabel(pauseReason)}</span>
          <button
            type="button"
            data-testid="painel-pause-continuar"
            disabled={pending}
            onClick={onContinuar}
            className="rounded-[6px] bg-white/20 px-[8px] py-[3px] text-[11.5px] font-semibold hover:bg-white/30 disabled:opacity-60"
          >
            Continuar
          </button>
        </div>
      )}

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

        {/* Runtime (execução) indicator — reads from the existing engine/status query (no second poll).
            Tri-state sync_phase: syncing (yellow, Sincronizando + UF progress) · synced (green,
            "Sincronizado") · idle (muted "Execução parada"). This is the RUNTIME axis (is a sweep
            dispatching / has one just completed) — distinct from the "Modo · …" operator-mode label
            below. aria-live polite so screen readers announce state changes. */}
        <div
          data-testid="sync-indicator"
          className="flex min-w-[160px] flex-col items-end gap-[3px]"
        >
          {syncPhase === "syncing" && (
            <>
              <div className="flex items-center gap-[6px]">
                <span
                  className="h-[7px] w-[7px] flex-shrink-0 animate-pulse rounded-full"
                  style={{ background: "var(--status-dlq)" }}
                  aria-hidden
                />
                <span
                  aria-live="polite"
                  data-testid="sync-indicator-label"
                  data-phase={syncPhase}
                  className="whitespace-nowrap text-[11.5px] font-medium"
                  style={{ color: "var(--status-dlq)" }}
                >
                  Sincronizando {SOURCE_LABELS[source]} · UF{" "}
                  {data?.ufs_done ?? 0}/{data?.ufs_total ?? 0}
                  {data?.current_uf ? ` · ${data.current_uf}` : ""}
                </span>
              </div>
              {(data?.ufs_total ?? 0) > 0 && (
                <div
                  className="h-[3px] w-full overflow-hidden rounded-full"
                  style={{ background: "var(--painel-border-outer)" }}
                  aria-hidden
                >
                  <div
                    className="h-full rounded-full transition-[width]"
                    style={{
                      background: "var(--status-dlq)",
                      width: `${Math.min(100, Math.round(((data?.ufs_done ?? 0) / (data?.ufs_total ?? 1)) * 100))}%`,
                    }}
                  />
                </div>
              )}
            </>
          )}
          {syncPhase === "synced" && (
            <div className="flex items-center gap-[6px]">
              <span
                className="h-[7px] w-[7px] flex-shrink-0 rounded-full"
                style={{ background: "var(--status-mar)" }}
                aria-hidden
              />
              <span
                aria-live="polite"
                data-testid="sync-indicator-label"
                data-phase={syncPhase}
                className="whitespace-nowrap text-[11.5px] font-medium"
                style={{ color: "var(--status-mar)" }}
              >
                Sincronizado
              </span>
            </div>
          )}
          {syncPhase === "idle" && (
            <span
              aria-live="polite"
              data-testid="sync-indicator-label"
              data-phase={syncPhase}
              className="whitespace-nowrap text-[11.5px]"
              style={{ color: "var(--painel-muted)" }}
            >
              Execução parada
            </span>
          )}
        </div>

        {/* Logs sidebar toggle — opens the per-source log ring buffer viewer */}
        <button
          type="button"
          data-testid="logs-icon-btn"
          aria-label="Ver logs de sincronização"
          aria-pressed={logsOpen}
          onClick={() => setLogsOpen((v) => !v)}
          className="flex h-[34px] w-[34px] flex-shrink-0 items-center justify-center rounded-[8px] border bg-[var(--card)] hover:bg-[var(--painel-chip)]"
          style={{ borderColor: "var(--painel-border-outer)" }}
        >
          <Terminal
            className="h-[15px] w-[15px]"
            style={{ color: logsOpen ? "var(--painel-navy)" : "var(--painel-muted)" }}
          />
        </button>

        {/* Divider */}
        <div
          className="h-[24px] w-px"
          style={{ background: "var(--painel-border-outer)" }}
          aria-hidden
        />

        {/* Motor tri-state: Ligar / Pausar / Desligar (POST /api/v1/engine/mode) */}
        <div className="flex items-center gap-[9px]">
          <span
            className="text-[12px] font-medium text-[var(--painel-muted)]"
            data-testid="painel-motor-state"
          >
            {/* Operator-MODE axis (edit-lock source of truth), orthogonal to the
                runtime "Execução …" indicator above — hence "Modo · …", not "Motor · …". */}
            Modo · {ENGINE_MODE_LABELS[mode]}
          </span>
          <div className="relative" ref={motorRef}>
            <div
              role="group"
              aria-label="Modo do motor"
              data-testid="painel-motor-modes"
              className="inline-flex gap-0.5 rounded-[9px] border p-[3px]"
              style={{
                borderColor: "var(--painel-border-outer)",
                background: "var(--painel-chip)",
              }}
            >
              {MOTOR_MODES.map((m) => {
                const active = m === mode;
                const onClick =
                  m === "LIGADO"
                    ? onLigar
                    : m === "PAUSADO"
                      ? onPausar
                      : onDesligar;
                const meta = MOTOR_ACTION_META[m];
                return (
                  <button
                    key={m}
                    type="button"
                    data-testid={meta.testid}
                    aria-pressed={active}
                    disabled={pending}
                    onClick={onClick}
                    className="rounded-[6px] px-[10px] py-[4px] text-[12px] font-semibold transition-colors disabled:opacity-60"
                    style={{
                      background: active ? "var(--card)" : "transparent",
                      color: active
                        ? "var(--painel-navy)"
                        : "var(--painel-muted)",
                      boxShadow: active ? "0 1px 2px rgba(15,23,42,.10)" : "none",
                    }}
                  >
                    {meta.label}
                  </button>
                );
              })}
            </div>

            {/* Start popover — a COLD Ligar (engine off). Shared UF + cap on top,
                then Varredura (needs a depth; backend 422s without one) and Descrição
                (action "describe"). Only shown when the engine is off (motorOn=false). */}
            {depthMenuOpen && !motorOn && (
              <div
                role="menu"
                data-testid="painel-depth-menu"
                className="absolute right-0 top-[30px] z-[20] w-[208px] rounded-[10px] border bg-[var(--card)] p-[6px] shadow-lg"
                style={{ borderColor: "var(--painel-border-outer)" }}
              >
                {/* Bug 2: UF scope — source-independent. "" ⇒ all 27 UFs. */}
                <div className="px-[8px] pb-[3px] pt-[5px] text-[10px] font-semibold uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
                  Abrangência
                </div>
                <select
                  data-testid="painel-uf-select"
                  value={startUf}
                  onChange={(e) => setStartUf(e.target.value)}
                  className="mb-[6px] block w-full rounded-[7px] border bg-[var(--card)] px-[8px] py-[6px] text-[12.5px] font-medium text-[var(--painel-text)]"
                  style={{ borderColor: "var(--painel-border-outer)" }}
                >
                  <option value="">Todo o Brasil (27 UFs)</option>
                  {BR_UFS.map((uf) => (
                    <option key={uf} value={uf}>
                      {uf}
                    </option>
                  ))}
                </select>
                {/* Optional per-UF attraction cap (test-run throttle). Empty ⇒ full
                    sweep. TripAdvisor lane only. */}
                <div className="px-[8px] pb-[3px] pt-[2px] text-[10px] font-semibold uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
                  Máx. atrativos por UF
                </div>
                <input
                  type="number"
                  data-testid="painel-max-per-uf"
                  value={maxPerUf}
                  min={1}
                  step={1}
                  placeholder="Sem limite"
                  onChange={(e) => setMaxPerUf(e.target.value)}
                  className="mb-[6px] block w-full rounded-[7px] border bg-[var(--card)] px-[8px] py-[6px] text-[12.5px] font-medium text-[var(--painel-text)]"
                  style={{ borderColor: "var(--painel-border-outer)" }}
                />
                <div className="px-[8px] py-[5px] text-[10px] font-semibold uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
                  Varredura
                </div>
                {/* R2 gate: the sweep needs a valid TA session; Descrição does not. */}
                {taBlocked && (
                  <div
                    data-testid="painel-sweep-blocked"
                    className="px-[8px] pb-[4px] text-[11px] text-[var(--painel-muted)]"
                  >
                    Injete uma sessão TripAdvisor válida para varrer.
                  </div>
                )}
                {DEPTH_ORDER.map((depth) => (
                  <button
                    key={depth}
                    type="button"
                    role="menuitem"
                    data-testid={`painel-depth-${depth}`}
                    disabled={pending || taBlocked}
                    onClick={() => onPickDepth(depth)}
                    className="block w-full rounded-[7px] px-[8px] py-[7px] text-left text-[12.5px] font-medium text-[var(--painel-text)] hover:bg-[var(--painel-chip)] disabled:opacity-50"
                  >
                    {DEPTH_LABELS[depth]}
                  </button>
                ))}
                <div className="px-[8px] py-[5px] text-[10px] font-semibold uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
                  Descrição
                </div>
                <button
                  type="button"
                  role="menuitem"
                  data-testid="painel-describe"
                  disabled={pending}
                  onClick={onDescribe}
                  className="block w-full rounded-[7px] px-[8px] py-[7px] text-left text-[12.5px] font-medium text-[var(--painel-text)] hover:bg-[var(--painel-chip)] disabled:opacity-50"
                >
                  Gerar descrições
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Source-pick + TA cURL (re)inject modal */}
      <PainelOrigem
        open={origemOpen}
        onClose={() => setOrigemOpen(false)}
        initialSource="tripadvisor"
      />

      {/* Log ring buffer sidebar */}
      <PainelLogs
        open={logsOpen}
        onClose={() => setLogsOpen(false)}
        source={source}
      />
    </div>
  );
}
