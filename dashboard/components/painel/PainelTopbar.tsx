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
import { CurrencySwitch } from "@/components/painel/currency";
import { PainelLogs } from "@/components/painel/PainelLogs";
import { PainelOrigem } from "@/components/painel/PainelOrigem";
import {
  ApiHealthItem,
  LiveChip,
  MotorMenu,
  StatusSep,
  TA_WARN_SECONDS,
  TaSessionItem,
  fmtMMSS,
} from "@/components/painel/PainelStatusBar";

/**
 * PainelTopbar — 58px chrome row of the Painel Brave shell.
 *
 * Left: page title/subtitle (per active view). Right: Origem + logs buttons and
 * ONE status bar (POC .statusbar): collection live chip · TA session TTL ·
 * norteia-api health · currency · compact motor menu. Everything reads the
 * engine + TA status polled here at ENGINE_REFETCH_INTERVAL_MS. A cold Ligar
 * (engine off) opens the start popover to pick scope + DEPTH (the backend 422s
 * a depthless start). The "Hoje" spend chip waits for the Phase 1 /cost today
 * block, so it is not rendered.
 */

interface PainelTopbarProps {
  title: string;
  subtitle: string;
}

/** Depth options offered when starting the motor (order = least → most spend). */
const DEPTH_ORDER: EngineDepth[] = [
  "nascente",
  "nascente_rio",
  "nascente_rio_mar",
];

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

/** Continuar's confirm text. A Places key swap only reaches the worker when its container is
 *  RECREATED (env_file is read at create; `restart` keeps the old key and re-pauses at once). */
function continuarConfirmText(reason: { reason: string; provider: string | null }): string {
  if (reason.reason === "manual") return "Retomar o backfill?";
  if (reason.provider === "google_places")
    return "Saldo recarregado? Se trocou BRAVE_PLACES_API_KEY no .env, recrie worker e api antes (docker compose up -d worker api) — restart não relê o .env.";
  if (reason.provider === "apify")
    return "Saldo Apify recarregado? Suba APIFY_TOTAL_USD_CAP (ou troque BRAVE_APIFY_API_KEY) no .env e recrie worker e api antes (docker compose up -d worker api) — restart não relê o .env.";
  return "Saldo recarregado?";
}

/** PT-BR pause-reason banner text (quick-260918-ohm) — reason + which provider tripped it. */
function pauseReasonLabel(reason: { reason: string; provider: string | null }): string {
  if (reason.reason === "daily_budget") return "Motor pausado: orçamento diário atingido";
  if (reason.reason === "manual") return "Motor pausado: backfill interrompido";
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

  const { data: sessionStatus, dataUpdatedAt: sessionUpdatedAt } = useQuery({
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
        | { action: "describe"; ufs?: string[]; maxPerUf?: number }
        | { action: "backfill" },
    ) =>
      startEngine(
        vars.action === "backfill"
          ? { action: "backfill" }
          : vars.action === "describe"
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
        vars.action === "backfill"
          ? "Motor ligado — backfill retomado"
          : vars.action === "describe"
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
  // that the provider's balance/quota was recharged (or, reason "manual", that an engine
  // pause mid-backfill should resume it). Rebuilds the start body from
  // the server-polled pause_reason.action — never trusts client-held depth state.
  // A null action (a chain task hit the wall, no run to resume) only lifts the
  // pause: mode LIGADO clears pause_reason server-side and starts nothing.
  const pauseReason = data?.pause_reason ?? null;
  const onContinuar = () => {
    if (!pauseReason) return;
    if (!window.confirm(continuarConfirmText(pauseReason))) return;
    if (pauseReason.action === "describe") {
      start.mutate({ action: "describe" });
    } else if (pauseReason.action === "backfill") {
      start.mutate({ action: "backfill" });
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
          role="status"
          className="flex h-[34px] flex-shrink-0 items-center gap-[10px] rounded-[8px] border border-[var(--dlq)] bg-[var(--dlq-soft)] px-[12px] text-[12px] font-medium text-[var(--dlq-ink)]"
        >
          <span>{pauseReasonLabel(pauseReason)}</span>
          <button
            type="button"
            data-testid="painel-pause-continuar"
            disabled={pending}
            onClick={onContinuar}
            className="rounded-[6px] bg-[var(--surface)] px-[8px] py-[3px] text-[11.5px] font-semibold text-[var(--ink)] hover:bg-[var(--surface-3)] disabled:opacity-60"
          >
            Continuar
          </button>
        </div>
      )}

      {/* Right controls */}
      <div className="flex flex-shrink-0 items-center gap-[8px]">
        {/* Origem {source} — opens the source-pick + TA cURL (re)inject modal */}
        <button
          type="button"
          data-testid="painel-source"
          aria-haspopup="dialog"
          onClick={() => setOrigemOpen(true)}
          className="flex h-[30px] items-center gap-[8px] rounded-[8px] border border-[var(--line)] bg-[var(--surface)] px-[10px] text-[12px] font-medium text-[var(--ink-2)] hover:bg-[var(--surface-3)]"
        >
          <span className="h-[7px] w-[7px] rounded-[2px] bg-[var(--navy)]" aria-hidden />
          Origem <strong className="font-semibold text-[var(--ink)]">{SOURCE_LABELS[source]}</strong>
        </button>

        {/* Logs sidebar toggle — opens the per-source log ring buffer viewer */}
        <button
          type="button"
          data-testid="logs-icon-btn"
          aria-label="Ver logs de sincronização"
          aria-pressed={logsOpen}
          onClick={() => setLogsOpen((v) => !v)}
          className="grid h-[30px] w-[30px] flex-shrink-0 place-items-center rounded-[8px] border border-[var(--line)] bg-[var(--surface)] hover:bg-[var(--surface-3)]"
        >
          <Terminal
            className="h-[15px] w-[15px]"
            style={{ color: logsOpen ? "var(--navy)" : "var(--ink-3)" }}
          />
        </button>

        {/* Status bar: service state, currency and motor in one block (POC .statusbar) */}
        <div
          role="group"
          aria-label="Status do serviço"
          data-testid="painel-statusbar"
          className="flex h-[38px] items-center gap-[2px] rounded-[10px] border border-[var(--line)] bg-[var(--surface)] px-[4px]"
        >
          <LiveChip status={data} />
          <StatusSep />
          {sessionStatus !== undefined && (
            <TaSessionItem session={sessionStatus} updatedAt={sessionUpdatedAt} />
          )}
          {data?.norteia_api && <ApiHealthItem api={data.norteia_api} />}
          <StatusSep />
          <CurrencySwitch />
          <StatusSep />
          {/* Motor: compact button + mode menu; a cold Ligar opens the start popover */}
          <div ref={motorRef}>
            <MotorMenu
              mode={mode}
              disabled={pending}
              onSelect={(m) =>
                m === "LIGADO" ? onLigar() : m === "PAUSADO" ? onPausar() : onDesligar()
              }
            >
              {/* Start popover — a COLD Ligar (engine off). Shared UF + cap on top,
                  then Varredura (needs a depth; backend 422s without one) and Descrição
                  (action "describe"). Only shown when the engine is off (motorOn=false). */}
              {depthMenuOpen && !motorOn && (
                <div
                  role="menu"
                  data-testid="painel-depth-menu"
                  className="absolute right-0 top-[calc(100%+8px)] z-[20] w-[208px] rounded-[10px] border bg-[var(--card)] p-[6px] shadow-lg"
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
            </MotorMenu>
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
