"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";

import { fetchFailureCards } from "@/lib/atrativos-api";
import { type CostData, costKeys, fetchCost, totalUsd } from "@/lib/cost-api";
import {
  DEPTH_LABELS,
  ENGINE_REFETCH_INTERVAL_MS,
  engineKeys,
  SOURCE_LABELS,
  type EngineMode,
  type EngineStatus,
  type TASessionStatus,
  fetchEngineStatus,
  fetchTASessionStatus,
  taSessionKeys,
} from "@/lib/engine-api";

import { CostMeter } from "./CostMeter";
import { useCurrency } from "./currency";
import type { PainelViewKey, RegistrosTab } from "./nav";

/**
 * Visão geral (#82 Phase 0): only the parts the current API backs. The run card,
 * the attention list and the pipeline tiles read GET /engine/status (+ TA session
 * and failure cards); the cost block reads GET /cost. Hidden until their data
 * source exists (Phase 1+): hourly Rio chart, live feed, Revisão reasons, UF
 * coverage, sync steps, page X of Y, ETA and pace.
 */

const AGGREGATE_REFETCH_MS = 60_000;
const TA_REFETCH_MS = 30_000;
/** TA session expiry warn band (seconds), same 5 min as the status bar. */
const TA_WARN_SECONDS = 5 * 60;
const LLM_WINDOW_HOURS = 24;

const MODE_PILL: Record<EngineMode, { label: string; bg: string; fg: string }> = {
  LIGADO: { label: "Ligado", bg: "var(--mar-soft)", fg: "var(--mar-ink)" },
  PAUSADO: { label: "Pausado", bg: "var(--dlq-soft)", fg: "var(--dlq-ink)" },
  DESLIGADO: { label: "Desligado", bg: "var(--surface-2)", fg: "var(--ink-2)" },
};

const STATE_LABEL: Record<EngineStatus["state"], string> = {
  running: "Coletando",
  stopping: "Parando",
  idle: "Parado",
};

const nf = (n: number) => n.toLocaleString("pt-BR");

function hhmm(iso: string): string {
  return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function mmss(seconds: number): string {
  const t = Math.max(0, Math.floor(seconds));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

// Same wording as the board's API-sync strip.
function apiDownLabel(reason?: string | null): string {
  if (reason === "ingest:401" || reason === "ingest:403") return "recusou o token de ingestão (expirado?)";
  if (reason === "ingest:404") return "sem a rota de ingestão (branch errada?)";
  if (reason?.startsWith("unhealthy")) return "com dependência fora (health 503)";
  return "fora do ar";
}

// Same wording as the status bar's pause banner.
function pauseLabel(r: { reason: string; provider: string | null }): string {
  if (r.reason === "daily_budget") return "Motor pausado: orçamento diário atingido";
  if (r.reason === "manual") return "Motor pausado: backfill interrompido";
  if (r.provider) return `Motor pausado: sem saldo (${r.provider})`;
  return "Motor pausado: sem saldo";
}

type Severity = "crit" | "warn" | "info";

interface Attention {
  id: string;
  sev: Severity;
  title: string;
  detail: string;
  action?: { label: string; run: () => void };
}

const SEV: Record<Severity, { label: string; bar: string; ink: string }> = {
  crit: { label: "Crítico", bar: "var(--falha)", ink: "var(--falha-ink)" },
  warn: { label: "Atenção", bar: "var(--dlq)", ink: "var(--dlq-ink)" },
  info: { label: "Info", bar: "var(--rio)", ink: "var(--rio)" },
};

/** The attention list, built only from real signals, most severe first. */
function buildAttention(
  s: EngineStatus | undefined,
  ta: TASessionStatus | undefined,
  openTab: (tab: RegistrosTab) => void,
  openView?: (key: PainelViewKey) => void,
): Attention[] {
  const items: Attention[] = [];
  const view = (label: string, key: PainelViewKey) =>
    openView ? { label, run: () => openView(key) } : undefined;
  const api = s?.norteia_api;

  if (api && (api.rejected ?? 0) > 0)
    items.push({
      id: "rejected",
      sev: "crit",
      title: `${nf(api.rejected ?? 0)} pushes rejeitados pela norteia-api`,
      detail: "Recusados com 4xx; o reenvio automático roda uma vez por dia.",
      action: { label: "Ver no Mar", run: () => openTab("mar") },
    });
  if (api?.up === false)
    items.push({
      id: "api-down",
      sev: "crit",
      title: `norteia-api ${apiDownLabel(api.reason)}`,
      detail: `Motivo: ${api.reason ?? "sem resposta"}. Os pushes esperam a API voltar.`,
      action: { label: "Ver no Mar", run: () => openTab("mar") },
    });
  if (api && api.pending > 0)
    items.push({
      id: "pending",
      sev: "warn",
      title: `${nf(api.pending)} registros do Mar pendentes de envio`,
      detail: "Ainda não aceitos pela norteia-api.",
      action: { label: "Ver no Mar", run: () => openTab("mar") },
    });
  if (s?.pause_reason)
    items.push({
      id: "pause",
      sev: "warn",
      title: pauseLabel(s.pause_reason),
      detail: `Desde ${hhmm(s.pause_reason.at)}. Retome em “Continuar”, na barra de status.`,
      action: s.pause_reason.reason === "manual" ? undefined : view("Ver tetos", "custo"),
    });
  if (ta) {
    const expiring =
      ta.present && ta.expires_in != null && ta.expires_in > 0 && ta.expires_in <= TA_WARN_SECONDS;
    const title = !ta.present
      ? ta.reason === "needs_bootstrap"
        ? "Sessão TripAdvisor precisa de bootstrap"
        : "Sessão TripAdvisor expirada"
      : expiring
        ? `Sessão TripAdvisor expira em ${mmss(ta.expires_in as number)}`
        : null;
    if (title)
      items.push({
        id: "ta",
        sev: "warn",
        title,
        detail: "Sem sessão válida o sweep TripAdvisor não coleta.",
        action: view("Renovar sessão", "varreduras"),
      });
  }
  for (const b of s?.beat_errors ?? [])
    items.push({
      id: `beat-${b.task}`,
      sev: "info",
      title: `Beat ${b.task} falhou`,
      detail: `${b.error_type} às ${hhmm(b.at)}.`,
      action: view("Ver monitor", "monitor"),
    });
  const dlq = s?.counts.rio.dlq ?? 0;
  if (dlq > 0)
    items.push({
      id: "revisao",
      sev: "info",
      title: `${nf(dlq)} registros esperando revisão`,
      detail: "Na fila de Revisão.",
      action: { label: "Abrir fila", run: () => openTab("revisao") },
    });
  return items;
}

export function PainelVisaoGeral({
  onOpenRegistros,
  onOpenView,
}: {
  onOpenRegistros: (tab: RegistrosTab) => void;
  /** Opens another Painel view (attention actions). Without it those buttons hide. */
  onOpenView?: (key: PainelViewKey) => void;
}) {
  const { data: status } = useQuery({
    queryKey: engineKeys.status,
    queryFn: fetchEngineStatus,
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
  });
  const { data: ta } = useQuery({
    queryKey: taSessionKeys.status,
    queryFn: fetchTASessionStatus,
    refetchInterval: TA_REFETCH_MS,
  });
  // Same key + select as the sidebar badge, so both share one cache entry.
  const { data: falha } = useQuery({
    queryKey: engineKeys.failures,
    queryFn: () => fetchFailureCards(),
    select: (cards) => cards.length,
    refetchInterval: AGGREGATE_REFETCH_MS,
  });
  const { data: cost } = useQuery({
    queryKey: costKeys.data("lane", LLM_WINDOW_HOURS),
    queryFn: () => fetchCost("lane", LLM_WINDOW_HOURS),
    refetchInterval: AGGREGATE_REFETCH_MS,
  });

  const attention = status ? buildAttention(status, ta, onOpenRegistros, onOpenView) : [];

  return (
    <div data-testid="painel-visao-geral" className="flex h-full flex-col gap-4 overflow-y-auto px-[22px] pb-7 pt-[18px]">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <RunCard status={status} />
        <Panel title="Precisa de atenção" hint={status ? `${attention.length} itens` : undefined}>
          {!status ? null : attention.length === 0 ? (
            <p className="m-0 text-[12.5px] text-[var(--ink-3)]">Nada precisa de atenção</p>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {attention.map((a) => (
                <AttentionRow key={a.id} item={a} />
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <section aria-label="Pipeline">
        <div className="mb-3 flex flex-wrap items-baseline gap-x-[10px]">
          <h2 className="text-[13.5px] font-semibold text-[var(--ink)]">Pipeline</h2>
          <span className="text-[12px] text-[var(--ink-3)]">clique numa etapa para abrir os registros</span>
        </div>
        <PipelineTiles status={status} falha={falha} onOpen={onOpenRegistros} />
      </section>

      <Panel
        title="Custo"
        hint="LLM nas últimas 24 h · Places e Apify no dia UTC e no total"
        className="lg:max-w-[560px]"
      >
        {cost ? <CostBlock data={cost} /> : null}
      </Panel>
    </div>
  );
}

function Panel({
  title,
  hint,
  className = "",
  children,
}: {
  title: string;
  hint?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className={`min-w-0 rounded-[12px] border border-[var(--line)] bg-[var(--surface)] p-4 ${className}`}
    >
      <div className="mb-3 flex flex-wrap items-baseline gap-x-[10px]">
        <h2 className="text-[13.5px] font-semibold tracking-[-0.1px] text-[var(--ink)]">{title}</h2>
        {hint ? <span className="text-[12px] text-[var(--ink-3)]">{hint}</span> : null}
      </div>
      {children}
    </section>
  );
}

function RunCard({ status }: { status: EngineStatus | undefined }) {
  const pill = status ? MODE_PILL[status.mode] : null;
  const hasRun = !!status && (status.state !== "idle" || status.ufs_total > 0);
  const pct = status && status.ufs_total > 0 ? (status.ufs_done / status.ufs_total) * 100 : 0;
  return (
    <section
      aria-label="Run"
      className="flex min-w-0 flex-col gap-3 rounded-[12px] border border-[var(--line)] bg-[var(--surface)] p-4"
    >
      <div className="flex flex-wrap items-baseline gap-x-[10px]">
        <h2 className="text-[13.5px] font-semibold text-[var(--ink)]">Run</h2>
        {status ? (
          <span className="text-[12px] text-[var(--ink-3)]">{STATE_LABEL[status.state]}</span>
        ) : null}
        {pill ? (
          <span
            className="ml-auto rounded-full px-2 py-[2px] text-[11.5px] font-semibold"
            style={{ background: pill.bg, color: pill.fg }}
          >
            {pill.label}
          </span>
        ) : null}
      </div>
      {!status ? null : !hasRun ? (
        <p className="m-0 text-[12.5px] text-[var(--ink-3)]">Nenhum run em andamento.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-[10px]">
            {status.current_uf ? (
              <span className="rounded-[5px] bg-[var(--surface-2)] px-[6px] py-[2px] font-mono text-[11px] font-semibold text-[var(--ink-2)]">
                {status.current_uf}
              </span>
            ) : null}
            <span className="text-[12.5px] text-[var(--ink-2)]">
              {[
                status.source ? SOURCE_LABELS[status.source] : null,
                status.depth ? DEPTH_LABELS[status.depth] : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </div>
          {status.ufs_total > 0 ? (
            <div>
              <div className="mb-[6px] flex justify-between text-[12px] text-[var(--ink-3)]">
                <span>
                  UFs <b className="font-mono text-[var(--ink)]">{status.ufs_done}</b> de{" "}
                  <span className="font-mono">{status.ufs_total}</span>
                </span>
                <span className="font-mono">{Math.round(pct)}%</span>
              </div>
              <div
                role="progressbar"
                aria-label="UFs concluídas"
                aria-valuemin={0}
                aria-valuemax={status.ufs_total}
                aria-valuenow={status.ufs_done}
                className="h-2 overflow-hidden rounded-full bg-[var(--surface-2)]"
              >
                <i className="block h-full rounded-full bg-[var(--navy)]" style={{ width: `${pct}%` }} />
              </div>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function AttentionRow({ item }: { item: Attention }) {
  const sev = SEV[item.sev];
  return (
    <li
      data-testid={`attention-${item.id}`}
      className="grid grid-cols-[4px_minmax(0,1fr)_auto] items-center gap-[10px] overflow-hidden rounded-[10px] border border-[var(--line)] bg-[var(--surface-3)] py-[9px] pl-0 pr-[10px]"
    >
      <span className="self-stretch rounded-r-[3px]" style={{ background: sev.bar }} aria-hidden />
      <div className="min-w-0">
        <b className="block text-[12.5px] text-[var(--ink)]">
          <span
            className="mr-[6px] text-[10.5px] font-bold uppercase tracking-[0.5px]"
            style={{ color: sev.ink }}
          >
            {sev.label}
          </span>
          {item.title}
        </b>
        <span className="text-[12px] text-[var(--ink-3)]">{item.detail}</span>
      </div>
      {item.action ? (
        <button
          type="button"
          onClick={item.action.run}
          className="inline-flex min-h-[26px] items-center whitespace-nowrap rounded-[8px] border border-[var(--line)] bg-[var(--surface)] px-[9px] text-[12px] font-semibold text-[var(--ink)] hover:bg-[var(--surface-3)]"
        >
          {item.action.label}
        </button>
      ) : (
        <span />
      )}
    </li>
  );
}

interface TileDef {
  id: string;
  label: string;
  color: string;
  value: number | undefined;
  meta?: ReactNode;
  tab?: RegistrosTab;
  tone?: "attn" | "crit";
}

function PipelineTiles({
  status,
  falha,
  onOpen,
}: {
  status: EngineStatus | undefined;
  falha: number | undefined;
  onOpen: (tab: RegistrosTab) => void;
}) {
  const c = status?.counts;
  const api = status?.norteia_api;
  const tiles: TileDef[] = [
    { id: "nascente", label: "Nascente", color: "var(--descarte)", value: c?.nascente, meta: "ingeridos" },
    { id: "rio", label: "Rio · processando", color: "var(--rio)", value: c?.rio.in_progress, meta: "enriquecimento e score" },
    {
      id: "revisao",
      label: "Revisão",
      color: "var(--dlq)",
      value: c?.rio.dlq,
      meta: "na fila de revisão",
      tab: "revisao",
      tone: c?.rio.dlq ? "attn" : undefined,
    },
    {
      id: "mar",
      label: "Mar",
      color: "var(--mar)",
      value: c?.mar,
      meta: api ? (
        <>
          <b className="font-semibold text-[var(--ink)]">{nf(api.pending)}</b> pendentes ·{" "}
          <b className="font-semibold text-[var(--ink)]">{nf(api.rejected ?? 0)}</b> rejeitados
        </>
      ) : (
        "publicados"
      ),
      tab: "mar",
    },
    {
      id: "falha",
      label: "Falha",
      color: "var(--falha)",
      value: falha,
      meta: "em quarentena",
      tab: "falha",
      tone: falha ? "crit" : undefined,
    },
    { id: "descarte", label: "Descarte", color: "var(--descarte)", value: c?.rio.descarte, meta: "fora do escopo" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
      {tiles.map((t) => {
        const border =
          t.tone === "crit"
            ? "color-mix(in oklab, var(--falha) 55%, var(--line))"
            : t.tone === "attn"
              ? "color-mix(in oklab, var(--dlq) 55%, var(--line))"
              : "var(--line)";
        const body = (
          <>
            <span className="flex items-center gap-[7px] text-[12px] font-semibold text-[var(--ink-2)]">
              <i className="h-[9px] w-[9px] rounded-[3px]" style={{ background: t.color }} aria-hidden />
              {t.label}
            </span>
            <span className="font-mono text-[26px] font-semibold leading-[1.1] tracking-[-1px] text-[var(--ink)]">
              {t.value === undefined ? "—" : nf(t.value)}
            </span>
            <span className="text-[12px] text-[var(--ink-3)]">{t.meta}</span>
            {t.tab ? (
              <ArrowRight className="absolute right-3 top-3 h-[14px] w-[14px] text-[var(--ink-4)]" aria-hidden />
            ) : null}
          </>
        );
        const cls =
          "relative flex min-w-0 flex-col gap-[6px] rounded-[12px] border bg-[var(--surface)] p-[14px] text-left";
        const tab = t.tab;
        return tab ? (
          <button
            key={t.id}
            type="button"
            data-testid={`tile-${t.id}`}
            onClick={() => onOpen(tab)}
            className={`${cls} cursor-pointer hover:border-[var(--ink-4)]`}
            style={{ borderColor: border }}
          >
            {body}
          </button>
        ) : (
          <div key={t.id} data-testid={`tile-${t.id}`} className={cls} style={{ borderColor: border }}>
            {body}
          </div>
        );
      })}
    </div>
  );
}

function CostBlock({ data }: { data: CostData }) {
  const { format } = useCurrency();
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-[10px]">
        <span className="text-[12.5px] font-medium text-[var(--ink)]">
          LLM<small className="ml-1 text-[11.5px] font-normal text-[var(--ink-3)]">últimas 24 h, por lane</small>
        </span>
        <span className="font-mono text-[12px] font-semibold text-[var(--ink)]">
          {format(totalUsd(data.rows), { digits: 4 })}
        </span>
      </div>
      <CostMeter testId="cap-places-today" label="Places hoje" hint="dia UTC" used={data.places.usd_today} cap={data.places.daily_cap_usd} />
      <CostMeter testId="cap-places-total" label="Places total" hint="crédito da chave" used={data.places.usd_total} cap={data.places.total_cap_usd} />
      <CostMeter testId="cap-apify-today" label="Apify hoje" hint="dia UTC" used={data.apify.usd_today} cap={data.apify.daily_cap_usd} />
      <CostMeter testId="cap-apify-total" label="Apify total" hint="desde o primeiro uso" used={data.apify.usd_total} cap={data.apify.total_cap_usd} />
    </div>
  );
}
