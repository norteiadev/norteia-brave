"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { PainelOrigem } from "@/components/painel/PainelOrigem";
import { TA_WARN_SECONDS, fmtMMSS, useRemaining } from "@/components/painel/PainelStatusBar";
import {
  Btn,
  Kpi,
  Panel,
  Pill,
  Seg,
  type Tone,
  UfBadge,
} from "@/components/painel/telas-ui";
import { ApiError } from "@/lib/api-client";
import {
  DEPTH_LABELS,
  ENGINE_REFETCH_INTERVAL_MS,
  type EngineDepth,
  engineKeys,
  fetchEngineStatus,
  fetchTASessionStatus,
  startEngine,
  taSessionKeys,
} from "@/lib/engine-api";
import { BR_UFS } from "@/lib/painel-data";
import {
  type RunItem,
  type RunsFilters,
  fetchRuns,
  formatCount,
  recentRuns,
  reprocessRun,
  runsKeys,
  totalFailed,
  totalRuns,
  totalSynced,
} from "@/lib/runs-api";

/**
 * PainelVarreduras — "Varreduras" (spec #82 Phase 0, POC view-varreduras).
 *
 *   - Nova varredura: the one place that starts a collection (#120). A sweep
 *     (UFs or all Brazil, depth, optional per-UF cap), the per-UF descriptions
 *     and the backfill resume, all through POST /engine/start. The server's
 *     409/422 reasons surface as toasts. A cold Ligar in the motor menu lands
 *     here with the focus on this panel.
 *   - Sessão TripAdvisor: presence + TTL from /tripadvisor/session/status; the
 *     cURL injection reuses PainelOrigem (distinct 422 vs 503 messages).
 *   - 7-day KPIs and the run history (GET /runs), with Fonte/Profundidade/UF
 *     filters and the run reprocess PATCH.
 *
 * Not here yet: UF "já varrida"/queue states, estimates, cookie/canary details,
 * status filter, per-run cost and partial reason (Phase 1/3/4 backend).
 */

const SOURCE_OPTIONS = [
  { key: "", label: "Todas" },
  { key: "tripadvisor", label: "TripAdvisor" },
] as const;

const DEPTH_OPTIONS = [
  { key: "", label: "Todas" },
  { key: "nascente", label: "Nascente" },
  { key: "nascente_rio", label: "Rio" },
  { key: "nascente_rio_mar", label: "Mar" },
] as const;

const START_DEPTHS: { key: EngineDepth; label: string }[] = (
  ["nascente", "nascente_rio", "nascente_rio_mar"] as const
).map((k) => ({ key: k, label: k === "nascente" ? "Nascente" : DEPTH_LABELS[k] }));

const DEPTH_LABEL: Record<string, string> = {
  nascente: "Nascente",
  nascente_rio: "Nascente → Rio",
  nascente_rio_mar: "Nascente → Rio → Mar",
  descricao: "Descrição",
  imagens: "Imagens",
};

const SOURCE_LABEL: Record<string, string> = {
  tripadvisor: "TripAdvisor",
  ta_backfill: "Backfill TripAdvisor",
  descricao: "Descrição",
  imagens: "Imagens",
};

const STATUS: Record<string, { tone: Tone; label: string }> = {
  concluido: { tone: "ok", label: "Concluído" },
  parcial: { tone: "warn", label: "Parcial" },
  falha: { tone: "bad", label: "Falha" },
  running: { tone: "run", label: "Em andamento" },
};


function explainError(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function fmtStarted(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  return new Date(t).toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtDuration(run: RunItem): string {
  if (!run.ended_at) return "em andamento";
  const min = Math.round((Date.parse(run.ended_at) - Date.parse(run.started_at)) / 60_000);
  if (Number.isNaN(min)) return "—";
  if (min < 1) return "< 1 min";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  return min % 60 ? `${h} h ${min % 60} min` : `${h} h`;
}

export function PainelVarreduras({ focusStart = 0 }: { focusStart?: number }) {
  const qc = useQueryClient();
  const [source, setSource] = useState("");
  const [depth, setDepth] = useState("");
  const [uf, setUf] = useState("");

  const filters: RunsFilters = { source: source || undefined, depth: depth || undefined };
  const { data } = useQuery({
    queryKey: runsKeys.list(filters),
    queryFn: () => fetchRuns(filters),
  });

  const reprocess = useMutation({
    mutationFn: (run: RunItem) => reprocessRun(run.id),
    onError: (err) => toast.error(explainError(err, "Falha ao reexecutar a varredura.")),
    onSuccess: () => toast.success("Reexecução da varredura iniciada."),
    onSettled: () => void qc.invalidateQueries({ queryKey: runsKeys.all }),
  });

  const allItems: RunItem[] = data?.items ?? [];
  // UF options come from the loaded set; the uf filter runs client-side.
  const ufOptions = Array.from(new Set(allItems.flatMap((r) => r.ufs))).sort();
  const rows = uf ? allItems.filter((r) => r.ufs.includes(uf)) : allItems;
  const recent = recentRuns(rows);
  const partials = recent.filter((r) => r.status === "parcial").length;
  const processed = recent.reduce((s, r) => s + r.total, 0);

  return (
    <div className="h-full overflow-y-auto px-[22px] pb-7 pt-[18px]">
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <NovaVarredura focusStart={focusStart} />
          <SessaoTA />
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Kpi
            label="Varreduras (7 dias)"
            testId="runs-stat-total"
            value={formatCount(totalRuns(recent))}
            sub={`${partials} ${partials === 1 ? "parcial" : "parciais"}`}
          />
          <Kpi
            label="Sincronizados (7 dias)"
            testId="runs-stat-synced"
            value={formatCount(totalSynced(recent))}
            sub={`de ${formatCount(processed)} processados`}
          />
          <Kpi
            label="Falhas (7 dias)"
            testId="runs-stat-failed"
            value={formatCount(totalFailed(recent))}
          />
        </div>

        <Panel title="Histórico de varreduras" hint="runs do motor, mais recentes primeiro">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Seg
              label="Fonte"
              options={SOURCE_OPTIONS}
              value={source}
              onChange={setSource}
              testIdPrefix="runs-source"
            />
            <Seg
              label="Profundidade"
              options={DEPTH_OPTIONS}
              value={depth}
              onChange={setDepth}
              testIdPrefix="runs-depth"
            />
            {ufOptions.length > 0 && (
              <Seg
                label="UF do histórico"
                options={[{ key: "", label: "Todas UF" }, ...ufOptions.map((u) => ({ key: u, label: u }))]}
                value={uf}
                onChange={setUf}
                testIdPrefix="runs-uf"
              />
            )}
          </div>

          {rows.length === 0 ? (
            <div data-testid="runs-empty" className="px-4 py-10 text-center text-[var(--ink-3)]">
              <div className="text-[14px] font-semibold text-[var(--ink-2)]">
                Nenhuma varredura registrada
              </div>
              <div className="mt-1 text-[12px]">
                As varreduras do motor aparecem aqui assim que executadas.
              </div>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-[12.5px]">
                <thead>
                  <tr className="text-left text-[10.5px] uppercase tracking-[0.5px] text-[var(--ink-3)]">
                    <Th>Run</Th>
                    <Th>Início</Th>
                    <Th>UFs</Th>
                    <Th>Tipo</Th>
                    <Th right>Total</Th>
                    <Th right>Sincr.</Th>
                    <Th right>Falhas</Th>
                    <Th>Duração</Th>
                    <Th>Status</Th>
                    <Th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((run) => {
                    const st = STATUS[run.status] ?? { tone: "neutral" as const, label: run.status };
                    return (
                      <tr key={run.id} data-testid="runs-row">
                        <Td>
                          <span className="font-mono text-[12px]" title={run.id}>
                            {run.id.slice(0, 8)}
                          </span>
                        </Td>
                        <Td>
                          <span className="font-mono text-[var(--ink-3)]">{fmtStarted(run.started_at)}</span>
                        </Td>
                        <Td>
                          <span className="inline-flex flex-wrap gap-1">
                            {run.ufs.length ? run.ufs.map((u) => <UfBadge key={u} uf={u} />) : "—"}
                          </span>
                        </Td>
                        <Td>
                          {DEPTH_LABEL[run.depth] ?? run.depth}
                          <div className="text-[11.5px] text-[var(--ink-3)]">
                            {SOURCE_LABEL[run.source] ?? run.source}
                          </div>
                        </Td>
                        <Td right mono>{formatCount(run.total)}</Td>
                        <Td right mono>{formatCount(run.synced)}</Td>
                        <Td right mono>
                          <span className={run.failed ? "font-semibold text-[var(--falha-ink)]" : ""}>
                            {formatCount(run.failed)}
                          </span>
                        </Td>
                        <Td mono>{fmtDuration(run)}</Td>
                        <Td>
                          <Pill tone={st.tone} data-testid="runs-status-pill" data-status={run.status}>
                            {st.label}
                          </Pill>
                        </Td>
                        <Td>
                          <Btn
                            small
                            data-testid="runs-reprocess"
                            disabled={run.failed === 0 || reprocess.isPending}
                            onClick={() => reprocess.mutate(run)}
                            title={
                              run.failed
                                ? "Reexecuta o escopo inteiro do run (UFs × profundidade), não só as falhas"
                                : "Sem falhas neste run"
                            }
                          >
                            Reexecutar
                          </Btn>
                        </Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

function useSession() {
  return useQuery({
    queryKey: taSessionKeys.status,
    queryFn: fetchTASessionStatus,
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });
}

/** PT-BR error for an engine call (start or mode). */
export function explainEngineError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    if (err.status === 409) return err.message || "Motor já está em execução.";
    return err.message;
  }
  return "Falha ao controlar o motor.";
}

/** One start request + its success copy. */
type StartRequest = { body: Parameters<typeof startEngine>[0]; done: string };

/** POST /engine/start for this panel and the pause banner's Continuar. */
export function useStartEngine(onStarted?: () => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: StartRequest) => startEngine(req.body),
    onError: (err) => toast.error(explainEngineError(err)),
    onSuccess: (_res, req) => {
      toast.success(req.done);
      onStarted?.();
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: engineKeys.status });
      void qc.invalidateQueries({ queryKey: runsKeys.all });
    },
  });
}

function NovaVarredura({ focusStart }: { focusStart: number }) {
  const [ufs, setUfs] = useState<ReadonlySet<string>>(new Set());
  const [allBrazil, setAllBrazil] = useState(false);
  const [depth, setDepth] = useState<EngineDepth>("nascente_rio");
  // Optional per-UF attraction cap (test-run throttle). "" = no cap.
  const [maxPerUf, setMaxPerUf] = useState("");
  const firstRef = useRef<HTMLButtonElement | null>(null);

  // Each cold Ligar bumps focusStart: land the keyboard on the scope picker.
  useEffect(() => {
    if (focusStart) firstRef.current?.focus();
  }, [focusStart]);

  const { data: engine } = useQuery({
    queryKey: engineKeys.status,
    queryFn: fetchEngineStatus,
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });
  const { data: session } = useSession();
  // The server refuses a TripAdvisor sweep or backfill without a live session (409).
  const taReady = !!session?.present && (session.expires_in ?? 0) > 0;
  const runningUf = engine?.state === "running" ? engine.current_uf : null;

  const start = useStartEngine(() => {
    setUfs(new Set());
    setAllBrazil(false);
  });

  const toggle = (u: string) => {
    setAllBrazil(false);
    setUfs((s) => {
      const next = new Set(s);
      if (next.has(u)) next.delete(u);
      else next.add(u);
      return next;
    });
  };

  const selected = BR_UFS.filter((u) => ufs.has(u));
  const hasScope = allBrazil || selected.length > 0;
  // No ufs = the server's all 27. Number("") is 0, so the >= 1 guard drops an empty cap.
  const cap = Number(maxPerUf);
  const scope = {
    ufs: allBrazil ? undefined : selected,
    max_atrativos_per_uf: Number.isInteger(cap) && cap >= 1 ? cap : undefined,
  };
  const where = allBrazil ? "todo o Brasil" : selected.join(", ");

  return (
    <Panel title="Nova varredura" hint="TripAdvisor por UF, descrições e backfill">
      <div className="flex flex-col gap-[14px]">
        <div>
          <div className="mb-2 text-[12px] text-[var(--ink-3)]">
            UFs
            {runningUf && (
              <span className="ml-2">
                <span className="mr-1 inline-block h-2 w-2 rounded-full bg-[var(--rio)]" />
                em andamento: {runningUf}
              </span>
            )}
          </div>
          <div role="group" aria-label="UFs" className="flex flex-wrap gap-[6px]">
            <button
              ref={firstRef}
              type="button"
              aria-pressed={allBrazil}
              onClick={() => {
                setAllBrazil((v) => !v);
                setUfs(new Set());
              }}
              className={`h-8 cursor-pointer rounded-[7px] border px-2.5 text-[12px] font-semibold ${
                allBrazil
                  ? "border-[var(--navy)] bg-[var(--navy-soft)] text-[var(--navy)]"
                  : "border-[var(--line)] bg-[var(--surface)] text-[var(--ink-2)]"
              }`}
            >
              Todo o Brasil
            </button>
            {BR_UFS.map((u) => {
              const on = ufs.has(u);
              return (
                <button
                  key={u}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggle(u)}
                  className={`relative h-8 min-w-[46px] cursor-pointer rounded-[7px] border px-2 font-mono text-[12px] font-semibold ${
                    on
                      ? "border-[var(--navy)] bg-[var(--navy-soft)] text-[var(--navy)]"
                      : "border-[var(--line)] bg-[var(--surface)] text-[var(--ink-2)]"
                  }`}
                >
                  {u}
                  {u === runningUf && (
                    <span
                      aria-hidden
                      className="absolute right-1 top-1 h-[6px] w-[6px] rounded-full bg-[var(--rio)]"
                    />
                  )}
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-[10px]">
          <span className="text-[12px] text-[var(--ink-3)]">Até onde vai</span>
          <Seg label="Profundidade da varredura" options={START_DEPTHS} value={depth} onChange={setDepth} />
        </div>

        <label className="flex flex-wrap items-center gap-[10px] text-[12px] text-[var(--ink-3)]">
          Máx. atrativos por UF
          <input
            type="number"
            min={1}
            step={1}
            value={maxPerUf}
            placeholder="Sem limite"
            onChange={(e) => setMaxPerUf(e.target.value)}
            className="h-8 w-[120px] rounded-[7px] border border-[var(--line)] bg-[var(--surface)] px-2 text-[12.5px] text-[var(--ink)]"
          />
        </label>

        <div className="flex flex-wrap items-center gap-2">
          <Btn
            variant="primary"
            disabled={!hasScope || !taReady || start.isPending}
            onClick={() =>
              start.mutate({
                body: { depth, source: "tripadvisor", ...scope },
                done: `Varredura de ${where} iniciada.`,
              })
            }
          >
            Iniciar varredura
          </Btn>
          <Btn
            disabled={!hasScope || start.isPending}
            title="Gera a descrição editorial dos atrativos sem descrição, por UF. Não usa a sessão do TripAdvisor."
            onClick={() =>
              start.mutate({ body: { action: "describe", ...scope }, done: `Gerando descrições de ${where}.` })
            }
          >
            Gerar descrições
          </Btn>
          <Btn
            disabled={!hasScope || start.isPending}
            title="Busca capa e galeria no Wikidata e no Wikimedia Commons para os atrativos e destinos, por UF. Não usa a sessão do TripAdvisor."
            onClick={() =>
              start.mutate({ body: { action: "images", ...scope }, done: `Buscando imagens de ${where}.` })
            }
          >
            Buscar imagens
          </Btn>
          <Btn
            disabled={!taReady || start.isPending}
            title="Retoma o backfill do TripAdvisor (por exemplo, depois de trocar a chave do Places)."
            onClick={() => start.mutate({ body: { action: "backfill" }, done: "Backfill retomado." })}
          >
            Retomar backfill
          </Btn>
          {session && !taReady && (
            <span className="text-[12px] text-[var(--dlq-ink)]">
              Injete uma sessão do TripAdvisor para varrer ou retomar o backfill.
            </span>
          )}
        </div>
      </div>
    </Panel>
  );
}

function SessaoTA() {
  const [open, setOpen] = useState(false);
  const { data: s, dataUpdatedAt } = useSession();

  // Same local countdown (and states) as the status bar's TA pill, so the two
  // readouts on this screen never disagree between polls.
  const ttl = useRemaining(s?.present ? s.expires_in : undefined, dataUpdatedAt);
  const state: { tone: Tone; label: string } = !s
    ? { tone: "neutral", label: "Verificando" }
    : !s.present
      ? s.reason === "needs_bootstrap"
        ? { tone: "warn", label: "Precisa bootstrap" }
        : { tone: "bad", label: "Expirada" }
      : ttl === undefined
        ? { tone: "ok", label: "Ativa" }
        : ttl <= 0
          ? { tone: "bad", label: "Expirada" }
          : ttl <= TA_WARN_SECONDS
            ? { tone: "warn", label: "Expirando" }
            : { tone: "ok", label: "Ativa" };

  return (
    <Panel title="Sessão TripAdvisor" right={<Pill tone={state.tone}>{state.label}</Pill>} testId="ta-session">
      <div className="flex flex-col gap-3">
        {s?.present && ttl !== undefined && (
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-[26px] font-semibold tracking-[-1px]">{fmtMMSS(ttl)}</span>
            <span className="text-[12px] text-[var(--ink-3)]">até expirar</span>
          </div>
        )}
        <div>
          <Btn onClick={() => setOpen(true)}>Atualizar sessão (cURL)</Btn>
        </div>
        <p className="m-0 text-[12px] text-[var(--ink-3)]">
          Sem sessão válida, a varredura do TripAdvisor não inicia. Cole o cURL da
          requisição GraphQL autenticada (DevTools → Network → Copiar como cURL).
        </p>
      </div>
      <PainelOrigem open={open} onClose={() => setOpen(false)} initialSource="tripadvisor" />
    </Panel>
  );
}

function Th({ children, right }: { children?: React.ReactNode; right?: boolean }) {
  return (
    <th className={`whitespace-nowrap px-[10px] pb-2 font-semibold ${right ? "text-right" : "text-left"}`}>
      {children}
    </th>
  );
}

function Td({
  children,
  right,
  mono,
}: {
  children?: React.ReactNode;
  right?: boolean;
  mono?: boolean;
}) {
  return (
    <td
      className={`whitespace-nowrap border-t border-[var(--line-2)] px-[10px] py-2 align-middle ${
        right ? "text-right" : "text-left"
      } ${mono ? "font-mono" : ""}`}
    >
      {children}
    </td>
  );
}
