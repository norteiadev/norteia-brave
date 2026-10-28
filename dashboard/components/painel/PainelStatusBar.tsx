"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Check, ChevronDown, Pause, Play, Square, type LucideIcon } from "lucide-react";

import { SOURCE_LABELS, type EngineSource, type EngineStatus, type TASessionStatus } from "@/lib/engine-api";
import type { EngineMode } from "@/lib/config-api";

/**
 * Pieces of the Painel status bar (POC .statusbar): collection live chip, TA
 * session TTL, norteia-api health and the compact motor menu. All read the
 * engine/TA status the topbar already polls; nothing here fetches.
 */

/** TA session expiry warn band (seconds) — mirrors the design's 5-min warn. */
export const TA_WARN_SECONDS = 5 * 60;

/** Format a seconds count as m:ss. */
export function fmtMMSS(seconds: number): string {
  const t = Math.max(0, Math.floor(seconds));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

export function StatusSep() {
  return <span aria-hidden className="mx-[2px] h-[18px] w-px flex-none bg-[var(--line)]" />;
}

function Dot({ color, pulse = false }: { color: string; pulse?: boolean }) {
  return (
    <span
      aria-hidden
      className={`h-[8px] w-[8px] flex-none rounded-full ${pulse ? "animate-pulse" : ""}`}
      style={{ background: color }}
    />
  );
}

const ITEM =
  "inline-flex h-[28px] items-center gap-[6px] whitespace-nowrap rounded-[6px] px-[8px] text-[12px] font-medium text-[var(--ink-2)]";

// ---------------------------------------------------------------------------
// Collection live chip
// ---------------------------------------------------------------------------

/**
 * "Coletando" only while the motor is LIGADO and a sweep is dispatching; a sweep
 * still draining under PAUSADO reads as paused. A verb, not "Ao vivo": every
 * screen polls, so "live" read as "this page refreshes". sync_phase falls back to
 * the enabled latch for servers that omit it.
 */
export function LiveChip({ status }: { status: EngineStatus | undefined }) {
  const enabled = status?.enabled ?? (status ? status.state !== "idle" : false);
  const phase = status?.sync_phase ?? (enabled ? "syncing" : "idle");
  const source: EngineSource = status?.source ?? "tripadvisor";
  const total = status?.ufs_total ?? 0;
  const progress =
    total > 0 ? `UF ${status?.ufs_done ?? 0}/${total}${status?.current_uf ? ` · ${status.current_uf}` : ""}` : "";

  let label = "Coleta parada";
  let color = "var(--ink-4)";
  let pulse = false;
  let title = "Ligue o motor para retomar a coleta.";
  if (phase === "syncing") {
    const live = status?.mode === "LIGADO";
    label = live ? "Coletando" : "Coleta pausada";
    color = live ? "var(--mar)" : "var(--dlq)";
    pulse = live;
    title = `${live ? "Sincronizando" : "Pausado após a tarefa atual"} ${SOURCE_LABELS[source]}${progress ? ` · ${progress}` : ""}`;
  } else if (phase === "synced") {
    label = "Sincronizado";
    color = "var(--mar)";
    title = "Varredura concluída.";
  }

  return (
    <span data-testid="sync-indicator" className={ITEM} title={title}>
      <Dot color={color} pulse={pulse} />
      <span aria-live="polite" data-testid="sync-indicator-label" data-phase={phase}>
        {label}
      </span>
      {phase === "syncing" && progress && (
        <span className="font-mono text-[11.5px] text-[var(--ink-3)]">{progress}</span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// TA session TTL
// ---------------------------------------------------------------------------

type TaState = "ok" | "expiring" | "expired" | "needs_bootstrap";

/**
 * Seconds left, counted down locally every second from the last poll: the
 * server's expires_in is anchored to the query's dataUpdatedAt, clamped at 0.
 */
export function useRemaining(expiresIn: number | undefined, anchor: number): number | undefined {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (expiresIn == null) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [expiresIn, anchor]);
  if (expiresIn == null) return undefined;
  return Math.max(0, expiresIn - Math.floor(Math.max(0, now - anchor) / 1000));
}

const TA_META: Record<TaState, { color: string; title: string }> = {
  ok: { color: "var(--mar)", title: "Sessão TripAdvisor válida. O TTL renova a cada requisição." },
  expiring: { color: "var(--dlq)", title: "Sessão TripAdvisor perto de expirar — recrie pelo modal Origem." },
  expired: { color: "var(--falha)", title: "Sessão TripAdvisor expirada — injete um cURL pelo modal Origem." },
  needs_bootstrap: { color: "var(--dlq)", title: "Sessão TripAdvisor ainda não criada — injete um cURL pelo modal Origem." },
};

export function TaSessionItem({ session, updatedAt }: { session: TASessionStatus; updatedAt: number }) {
  const remaining = useRemaining(session.present ? session.expires_in : undefined, updatedAt);
  const state: TaState = !session.present
    ? session.reason === "needs_bootstrap"
      ? "needs_bootstrap"
      : "expired"
    : remaining === undefined
      ? "ok"
      : remaining <= 0
        ? "expired"
        : remaining <= TA_WARN_SECONDS
          ? "expiring"
          : "ok";
  const meta = TA_META[state];
  const value =
    state === "expired"
      ? "Expirada"
      : state === "needs_bootstrap"
        ? "Precisa bootstrap"
        : remaining === undefined
          ? "Pronta"
          : fmtMMSS(remaining);

  return (
    <span data-testid="painel-ta-pill" data-state={state} className={ITEM} title={meta.title}>
      <Dot color={meta.color} />
      <span className="text-[var(--ink-3)]">TA</span>
      <span
        className="font-mono tabular-nums"
        style={{ color: state === "ok" ? undefined : `var(--${state === "expired" ? "falha" : "dlq"}-ink)` }}
      >
        {value}
      </span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// norteia-api health
// ---------------------------------------------------------------------------

/** PT-BR pause-reason banner text (quick-260918-ohm) — reason + which provider tripped it. */
export function pauseReasonLabel(reason: { reason: string; provider: string | null }): string {
  if (reason.reason === "daily_budget") return "Motor pausado: orçamento diário atingido";
  if (reason.reason === "manual") return "Motor pausado: backfill interrompido";
  if (reason.provider) return `Motor pausado: sem saldo (${reason.provider})`;
  return "Motor pausado: sem saldo";
}

/** Long copy for the probe's failure reason (brave/core/mar/sync.py::_probe). */
export function apiDownLabel(reason?: string | null): string {
  if (reason === "ingest:401" || reason === "ingest:403") return "recusou o token de ingestão (expirado?)";
  if (reason === "ingest:404") return "sem a rota de ingestão (branch errada?)";
  if (reason?.startsWith("unhealthy")) return "com dependência fora (health 503)";
  return "fora do ar";
}

/** Short visible reason: "unreachable" → inacessível, "ingest:401" → ingest 401. */
function apiDownShort(reason?: string | null): string {
  if (!reason || reason === "unreachable") return "inacessível";
  return reason.replace(":", " ");
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function ApiHealthItem({ api }: { api: NonNullable<EngineStatus["norteia_api"]> }) {
  const state = api.up === null ? "disabled" : api.up ? "up" : "down";
  const rejected = api.rejected ?? 0;
  const title =
    state === "disabled"
      ? "Externals desligados: nada é enviado à norteia-api."
      : state === "up"
        ? "norteia-api no ar (GET /api/v1/health + POST de ingestão vazio)."
        : `norteia-api ${apiDownLabel(api.reason)} (${api.reason ?? "unreachable"}). O Mar envia quando ela voltar.`;
  const color = state === "up" ? "var(--mar)" : state === "down" ? "var(--falha)" : "var(--ink-4)";
  const text = state === "up" ? "no ar" : state === "down" ? apiDownShort(api.reason) : "desligada";

  return (
    <span data-testid="painel-api-status" data-state={state} className={ITEM} title={title}>
      <Dot color={color} />
      <span className="text-[var(--ink-3)]">API</span>
      <span style={{ color: state === "down" ? "var(--falha-ink)" : undefined }}>{text}</span>
      {api.pending > 0 && (
        <span className="text-[var(--dlq-ink)]">· {plural(api.pending, "pendente", "pendentes")}</span>
      )}
      {rejected > 0 && (
        <span className="text-[var(--falha-ink)]">· {plural(rejected, "rejeitado", "rejeitados")}</span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Compact motor button + menu
// ---------------------------------------------------------------------------

const MOTOR_MODES: EngineMode[] = ["LIGADO", "PAUSADO", "DESLIGADO"];

const MOTOR_META: Record<
  EngineMode,
  { label: string; desc: string; testid: string; Icon: LucideIcon; bg: string; fg: string; icon: string }
> = {
  LIGADO: {
    label: "Ligado",
    desc: "Coleta contínua. Editar registros fica travado.",
    testid: "painel-motor-ligar",
    Icon: Play,
    bg: "var(--mar-soft)",
    fg: "var(--mar-ink)",
    icon: "var(--mar-ink)",
  },
  PAUSADO: {
    label: "Pausado",
    desc: "Para após a tarefa atual e libera a edição. Retoma de onde parou.",
    testid: "painel-motor-pausar",
    Icon: Pause,
    bg: "var(--dlq-soft)",
    fg: "var(--dlq-ink)",
    icon: "var(--dlq-ink)",
  },
  DESLIGADO: {
    label: "Desligado",
    desc: "Encerra o run e libera a edição. Ligar de novo pede escopo e profundidade.",
    testid: "painel-motor-desligar",
    Icon: Square,
    bg: "var(--surface-2)",
    fg: "var(--ink-2)",
    icon: "var(--ink-3)",
  },
};

/**
 * One button showing the current mode; opens a role="menu" of menuitemradio.
 * Arrows move (wrapping), Enter/Space select (native button), Esc closes and
 * returns focus, outside click closes. Every pick reaches onSelect — the
 * topbar decides (a desynced LIGADO must still recover via a cold start).
 * `children` renders inside the anchor (the cold-start popover); `onOpen` lets
 * the owner close it, since both open at the same anchor.
 */
export function MotorMenu({
  mode,
  disabled,
  onSelect,
}: {
  mode: EngineMode;
  disabled: boolean;
  onSelect: (mode: EngineMode) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const menuId = useId();
  const meta = MOTOR_META[mode];

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
    function onDown(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const close = () => {
    setOpen(false);
    btnRef.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? []);
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : items.length - 1;
      items[i < 0 ? 0 : (i + step) % items.length]?.focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div ref={wrapRef} className="relative">
      <button
        ref={btnRef}
        type="button"
        data-testid="painel-motor-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Motor ${meta.label}. Trocar modo`}
        disabled={disabled}
        onClick={() => setOpen(!open)}
        className="inline-flex h-[28px] items-center gap-[6px] rounded-[7px] px-[7px] text-[12px] font-semibold disabled:opacity-60"
        style={{ background: meta.bg, color: meta.fg }}
      >
        <meta.Icon aria-hidden className="h-[12px] w-[12px]" fill="currentColor" />
        <span data-testid="painel-motor-state">{meta.label}</span>
        <ChevronDown
          aria-hidden
          className={`h-[13px] w-[13px] opacity-70 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label="Modo do motor"
          onKeyDown={onKeyDown}
          className="absolute right-0 top-[calc(100%+8px)] z-[30] flex w-[260px] flex-col rounded-[10px] border border-[var(--line)] bg-[var(--surface)] p-[4px]"
          style={{ boxShadow: "var(--shadow)" }}
        >
          <div aria-hidden className="px-[8px] pb-[4px] pt-[6px] text-[10.5px] uppercase tracking-[0.6px] text-[var(--ink-3)]">
            Modo do motor
          </div>
          {MOTOR_MODES.map((m) => {
            const item = MOTOR_META[m];
            const checked = m === mode;
            return (
              <button
                key={m}
                type="button"
                role="menuitemradio"
                aria-checked={checked}
                tabIndex={-1}
                data-testid={item.testid}
                onClick={() => {
                  close();
                  onSelect(m);
                }}
                className="grid grid-cols-[14px_minmax(0,1fr)_14px] items-center gap-x-[10px] gap-y-[2px] rounded-[7px] p-[8px] text-left text-[var(--ink)] outline-none hover:bg-[var(--surface-3)] focus-visible:bg-[var(--surface-3)]"
              >
                <item.Icon aria-hidden className="h-[12px] w-[12px]" style={{ color: item.icon }} fill="currentColor" />
                <b className="text-[12.5px] font-semibold">{item.label}</b>
                <Check
                  aria-hidden
                  className={`h-[14px] w-[14px] text-[var(--navy)] ${checked ? "visible" : "invisible"}`}
                  strokeWidth={2.4}
                />
                <small className="col-start-2 text-[11.5px] leading-[1.35] text-[var(--ink-3)]">{item.desc}</small>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
