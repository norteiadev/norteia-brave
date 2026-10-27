"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  AtrativoDetailPanel,
  BTN,
  FalhaDetailPanel,
  Flag,
} from "@/components/painel/RegistroDetail";
import { usePainelTheme } from "@/components/painel/PainelShell";
import { apiDownLabel } from "@/components/painel/PainelStatusBar";
import { LoadError } from "@/components/painel/telas-ui";
import type { RegistrosTab } from "@/components/painel/nav";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  atrativoCountQuery,
  atrativoKeys,
  fetchAtrativoList,
  fetchFailureCards,
  promoteBulkAtrativos,
  type AtrativoListItem,
  type FailureCard,
  type PromoteBulkDryRunResult,
  type PromoteBulkRunResult,
} from "@/lib/atrativos-api";
import { configKeys, fetchConfig } from "@/lib/config-api";
import {
  ENGINE_REFETCH_INTERVAL_MS,
  engineKeys,
  fetchEngineStatus,
  repushPendingMar,
  setEngineMode,
  type EngineStatus,
} from "@/lib/engine-api";
import { explainError } from "@/lib/painel-actions";
import { BR_UFS } from "@/lib/painel-data";
import { STAGE_LABELS, fmt1, scorePotential } from "@/lib/registros";

/**
 * Registros (#88) — replaces the kanban: Revisão (routing dlq) / Mar / Falha
 * tabs, a dense list and a detail panel. Phase 0 runs on the current API:
 * the list has no server sort, search or motivo, so it is a flat list in the
 * server's order (no client-side fake grouping), filtered by UF server-side.
 * Stage age is not exposed by the API yet, so rows carry no age. Neither is a
 * per-row duplicate signal: GET /dedup/pairs pairs only one page of candidates,
 * so a "Possível duplicado" flag would be missing on most real duplicates. It
 * comes back with Phase 2's duplicate_place; until then, Duplicados is the queue.
 */

const PAGE = 25;
const STEP = 50;
// GET /atrativos caps `limit` at 500; past that the operator narrows by UF.
const MAX_LIMIT = 500;
const LIST_REFETCH_MS = 30_000;

const TABS: { key: RegistrosTab; label: string; sub: string; color: string }[] = [
  { key: "revisao", label: "Revisão", sub: "DLQ · validação humana", color: "var(--dlq)" },
  { key: "mar", label: "Mar", sub: "canônico · vai para a API", color: "var(--mar)" },
  { key: "falha", label: "Falha", sub: "quarentena", color: "var(--falha)" },
];
const ROUTING = { revisao: "dlq", mar: "mar" } as const;

/** Same key as the sidebar badge when no UF is set, so they share one request. */
const countQuery = (routing: "dlq" | "mar", uf: string) => ({
  ...atrativoCountQuery(routing, uf),
  refetchInterval: LIST_REFETCH_MS,
});

function ScoreBar({ score, potential, threshold }: { score: number; potential: number; threshold?: number }) {
  const t = threshold ?? 101;
  const fill = score >= t ? "var(--mar)" : potential >= t ? "var(--dlq)" : "var(--falha)";
  const title =
    threshold == null
      ? `Score ${fmt1(score)}`
      : potential > score
        ? `Score ${fmt1(score)} · com validação ${fmt1(potential)} · limiar ${threshold}`
        : `Score ${fmt1(score)} · limiar ${threshold}`;
  return (
    <div className="grid grid-cols-[30px_minmax(0,1fr)] items-center gap-2" title={title}>
      <span className="font-mono text-[12.5px] font-semibold">{Math.round(score)}</span>
      <div className="relative h-2 rounded-full bg-[var(--surface-2)]">
        <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${score}%`, background: fill }} />
        {potential > score ? (
          <span
            aria-hidden
            className="absolute inset-y-0 rounded-r-full"
            style={{
              left: `${score}%`,
              width: `${potential - score}%`,
              background:
                "repeating-linear-gradient(135deg, color-mix(in oklab, var(--mar) 55%, transparent) 0 2px, transparent 2px 4px)",
            }}
          />
        ) : null}
        {threshold != null ? (
          <span aria-hidden className="absolute -inset-y-[3px] w-0.5 rounded-sm bg-[var(--ink)]" style={{ left: `${threshold}%` }} />
        ) : null}
      </div>
    </div>
  );
}

function ApiSync({ api }: { api: NonNullable<EngineStatus["norteia_api"]> }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const reenviar = async () => {
    setBusy(true);
    try {
      const { dispatched } = await repushPendingMar();
      toast.success(`${dispatched} reenviados para a norteia-api.`);
      void qc.invalidateQueries({ queryKey: engineKeys.status });
    } catch (err) {
      toast.error(explainError(err));
    } finally {
      setBusy(false);
    }
  };
  if (api.up === null) return null; // real externals off: nothing to probe
  return (
    <div className="flex items-center gap-2 text-[12px] text-[var(--ink-3)]">
      <span aria-hidden className={`size-2 rounded-full ${api.up ? "bg-[var(--mar)]" : "bg-[var(--falha)]"}`} />
      <span data-testid="api-sync-label">
        norteia-api {api.up ? "online" : apiDownLabel(api.reason)}
        {api.pending > 0 ? ` · ${api.pending} ${api.pending === 1 ? "pendente" : "pendentes"} de envio` : ""}
      </span>
      {(api.rejected ?? 0) > 0 ? (
        <span data-testid="api-sync-rejected" title="Recusados pela norteia-api (4xx) — reenvio automático diário">
          <Flag tone="bad">Pushes rejeitados: {api.rejected}</Flag>
        </span>
      ) : null}
      {api.pending > 0 ? (
        <button
          type="button"
          className={BTN}
          disabled={busy || !api.up}
          title={api.up ? undefined : "norteia-api indisponível — reenvio automático quando voltar"}
          onClick={() => void reenviar()}
        >
          Reenviar pendentes
        </button>
      ) : null}
    </div>
  );
}

/** Promover em lote: dry-run → confirm → real run, scoped to the UF filter. */
function PromoverLote({ uf, locked, apiDown }: { uf: string; locked: boolean; apiDown: boolean }) {
  const qc = useQueryClient();
  const theme = usePainelTheme();
  const [busy, setBusy] = useState(false);
  const [dry, setDry] = useState<PromoteBulkDryRunResult | null>(null);
  const scope = uf || null;
  const run = async (dryRun: boolean) => {
    setBusy(true);
    try {
      const res = await promoteBulkAtrativos({ uf: scope, dry_run: dryRun });
      if (dryRun) {
        const d = res as PromoteBulkDryRunResult;
        if (d.would_promote === 0)
          toast.error("Nenhum atrativo elegível para promoção em lote com os filtros atuais.");
        else setDry(d);
      } else {
        const r = res as PromoteBulkRunResult;
        toast.success(
          `${r.promoted} promovidos, ${r.held.length} retidos, ${r.failed.length} falharam. Restam ${r.remaining}.`,
        );
        void qc.invalidateQueries({ queryKey: atrativoKeys.all });
        void qc.invalidateQueries({ queryKey: engineKeys.status });
      }
    } catch (err) {
      toast.error(explainError(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button
        type="button"
        data-testid="promover-lote-btn"
        className={BTN}
        disabled={busy || locked}
        onClick={() => void run(true)}
      >
        Promover em lote
      </button>
      <AlertDialog open={dry !== null} onOpenChange={(open) => !open && setDry(null)}>
        {/* Portal renders outside the /painel subtree — re-scope the tokens and theme. */}
        <AlertDialogContent className="painel-light" data-painel-theme={theme} data-testid="promover-lote-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Promover {dry?.would_promote} atrativos para o Mar?</AlertDialogTitle>
            <AlertDialogDescription>
              {dry?.candidates} elegíveis{scope ? ` em ${scope}` : ""}. Excluídos: {dry?.excluded.verdict ?? 0} por
              veredito do DLQ, {dry?.excluded.below_score} por score, {dry?.excluded.no_description} sem descrição,{" "}
              {dry?.excluded.recency} sem review recente. Cada promoção fica registrada como validação humana e é
              publicada na norteia-api.
              {apiDown
                ? " A norteia-api está indisponível: os registros entram no Mar agora e são enviados quando ela voltar."
                : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="promover-lote-cancel">Cancelar</AlertDialogCancel>
            <AlertDialogAction
              data-testid="promover-lote-confirm"
              onClick={() => {
                setDry(null);
                void run(false);
              }}
            >
              Promover
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export function PainelRegistros({
  tab,
  onTabChange,
}: {
  tab: RegistrosTab;
  onTabChange: (tab: RegistrosTab) => void;
}) {
  const qc = useQueryClient();
  const [uf, setUf] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [active, setActive] = useState<string | null>(null);

  // A new tab (from here or the sidebar) starts at the top with no detail open.
  const [prevTab, setPrevTab] = useState(tab);
  if (prevTab !== tab) {
    setPrevTab(tab);
    setLimit(PAGE);
    setActive(null);
  }

  const { data: engine } = useQuery({
    queryKey: engineKeys.status,
    queryFn: fetchEngineStatus,
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
  });
  // Unknown status ⇒ unlocked; the server 423 stays the backstop.
  const locked = engine?.editing_unlocked === false;
  const { data: config } = useQuery({ queryKey: configKeys.snapshot, queryFn: fetchConfig });
  const score = config?.score;

  const dlqTotal = useQuery(countQuery("dlq", uf));
  const marTotal = useQuery(countQuery("mar", uf));
  // Same key/request as the sidebar badge; no server UF filter, so narrow here.
  const failures = useQuery({
    queryKey: engineKeys.failures,
    queryFn: fetchFailureCards,
    refetchInterval: LIST_REFETCH_MS,
  });
  const falhaRows = (failures.data ?? []).filter((f) => !uf || f.uf === uf);
  const totals: Record<RegistrosTab, number | undefined> = {
    revisao: dlqTotal.data,
    mar: marTotal.data,
    falha: failures.data ? falhaRows.length : undefined,
  };

  const routing = tab === "falha" ? null : ROUTING[tab];
  const list = useQuery({
    queryKey: atrativoKeys.list({ registros: routing, uf, limit }),
    queryFn: () => fetchAtrativoList({ routing: routing!, uf: uf || undefined, limit }),
    enabled: routing !== null,
    // Keep the rows only while "Mostrar mais" grows `limit`: another tab or UF
    // must never show the previous one's rows (or let them be acted on).
    placeholderData: (prev, prevQuery) => {
      const k = prevQuery?.queryKey[2] as { registros?: string | null; uf?: string } | undefined;
      return k?.registros === routing && k?.uf === uf ? prev : undefined;
    },
    refetchInterval: LIST_REFETCH_MS,
  });

  const atrativos = routing ? (list.data?.items ?? []) : [];
  const falhas = routing ? [] : falhaRows.slice(0, limit);
  const total = routing ? list.data?.total : totals.falha;
  const ids = routing ? atrativos.map((a) => a.id) : falhas.map((f) => f.source_ref);
  const shown = ids.length;
  const current = routing ? list : failures;
  const more = total == null ? 0 : Math.min(STEP, MAX_LIMIT - limit, total - shown);
  const activeRow = atrativos.find((a) => a.id === active);
  const activeFalha = falhas.find((f) => f.source_ref === active);

  // J/K walk the list (and open the row), Esc closes the detail.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // A dialog or menu owns its keys (its Esc must not also close the detail).
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      if (document.querySelector("[role=dialog], [role=alertdialog], [role=menu]")) return;
      const target = e.target as HTMLElement | null;
      if (target?.closest?.("input, textarea, select, [contenteditable='true']")) return;
      const k = e.key.toLowerCase();
      if (k === "escape") return setActive(null);
      if (k !== "j" && k !== "k") return;
      e.preventDefault();
      if (ids.length === 0) return;
      const i = ids.indexOf(active ?? "");
      const next = i === -1 ? 0 : Math.max(0, Math.min(ids.length - 1, i + (k === "j" ? 1 : -1)));
      setActive(ids[next]);
      document.querySelector(`[data-row-id="${CSS.escape(ids[next])}"]`)?.scrollIntoView?.({ block: "nearest" });
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [ids, active]);

  const pauseMotor = () =>
    setEngineMode("PAUSADO").then(
      () => void qc.invalidateQueries({ queryKey: engineKeys.status }),
      (err) => toast.error(explainError(err)),
    );

  const rowCls = (id: string) =>
    `grid cursor-pointer items-center gap-2.5 border-t border-[var(--line-2)] px-3 py-2 min-h-12 first:border-t-0 ${
      active === id ? "bg-[var(--navy-soft)] shadow-[inset_3px_0_0_var(--navy)]" : "hover:bg-[var(--surface-3)]"
    }`;

  return (
    <section
      data-testid="painel-registros"
      data-tab={tab}
      className="grid h-full min-h-0 grid-rows-[auto_auto_minmax(0,1fr)]"
    >
      <div role="tablist" aria-label="Etapa" className="flex flex-wrap gap-1.5 px-[22px] pt-3">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => onTabChange(t.key)}
            className={`inline-flex min-h-10 items-center gap-2 rounded-[10px] border bg-[var(--surface)] px-3 py-2 text-[13px] font-semibold ${
              tab === t.key
                ? "border-[var(--ink-3)] text-[var(--ink)] shadow-[var(--shadow)]"
                : "border-[var(--line)] text-[var(--ink-2)]"
            }`}
          >
            <i aria-hidden className="size-[9px] rounded-[3px]" style={{ background: t.color }} />
            {t.label}
            <span data-testid={`registros-tab-count-${t.key}`} className="font-mono text-[12px] text-[var(--ink-3)]">
              {totals[t.key]?.toLocaleString("pt-BR") ?? ""}
            </span>
            <small className="text-[11.5px] font-medium text-[var(--ink-3)]">{t.sub}</small>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--line)] px-[22px] py-2.5">
        <label className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-2.5 text-[12.5px]">
          <span className="text-[12px] text-[var(--ink-3)]">UF</span>
          <select
            aria-label="UF"
            value={uf}
            onChange={(e) => {
              setUf(e.target.value);
              setLimit(PAGE);
              setActive(null);
            }}
            className="bg-transparent text-[12.5px] outline-none"
          >
            <option value="">Todas</option>
            {BR_UFS.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        </label>
        {tab === "revisao" ? <PromoverLote uf={uf} locked={locked} apiDown={engine?.norteia_api?.up === false} /> : null}
        {tab === "mar" && engine?.norteia_api ? <ApiSync api={engine.norteia_api} /> : null}
        <span className="flex-1" />
        <span className="text-[11.5px] text-[var(--ink-3)]">
          <kbd className="rounded border border-b-2 border-[var(--line)] px-1 font-mono text-[10.5px]">J</kbd>{" "}
          <kbd className="rounded border border-b-2 border-[var(--line)] px-1 font-mono text-[10.5px]">K</kbd> navegar ·{" "}
          <kbd className="rounded border border-b-2 border-[var(--line)] px-1 font-mono text-[10.5px]">Esc</kbd> fecha
        </span>
      </div>

      <div className="grid min-h-0 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_420px]">
        <div className="min-h-0 overflow-y-auto px-[22px] pb-20 pt-2.5">
          {locked && tab === "revisao" ? (
            <div
              role="status"
              className="mb-2.5 flex flex-wrap items-center gap-2.5 rounded-[10px] border border-[var(--dlq)] bg-[var(--dlq-soft)] px-3 py-2 text-[12.5px] text-[var(--dlq-ink)]"
            >
              <span>
                <b className="text-[var(--ink)]">Motor ligado.</b> Promover, descartar e editar ficam travados enquanto
                o sweep roda (a API responde 423).
              </span>
              <button type="button" className={`${BTN} ml-auto`} onClick={() => void pauseMotor()}>
                Pausar motor
              </button>
            </div>
          ) : null}

          {shown === 0 && current.isError ? (
            <LoadError what="os registros" onRetry={() => void current.refetch()} />
          ) : shown === 0 ? (
            <div className="px-4 py-10 text-center text-[var(--ink-3)]">
              {current.isPending ? "Carregando…" : "Nenhum registro nesta etapa."}
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--surface)]">
              <div role="listbox" aria-label="Registros" className="flex flex-col">
                {atrativos.map((a) => (
                  <AtrativoRow
                    key={a.id}
                    a={a}
                    cls={rowCls(a.id)}
                    selected={active === a.id}
                    threshold={score?.threshold_mar}
                    weight={tab === "revisao" ? score?.weight_validacao_humana : undefined}
                    onOpen={() => setActive(a.id)}
                  />
                ))}
                {falhas.map((f) => (
                  <FalhaRow key={f.source_ref} f={f} cls={rowCls(f.source_ref)} selected={active === f.source_ref} onOpen={() => setActive(f.source_ref)} />
                ))}
              </div>
              <div className="flex items-center gap-2.5 border-t border-[var(--line-2)] px-3 py-2 text-[12px] text-[var(--ink-3)]">
                <span>
                  Mostrando {shown.toLocaleString("pt-BR")} de {(total ?? shown).toLocaleString("pt-BR")}
                </span>
                {more > 0 ? (
                  <button
                    type="button"
                    className="font-semibold text-[var(--navy)] underline-offset-2 hover:underline"
                    onClick={() => setLimit((l) => Math.min(MAX_LIMIT, l + STEP))}
                  >
                    Mostrar mais {more}
                  </button>
                ) : total != null && shown < total ? (
                  <span>Filtre por UF para ver o restante.</span>
                ) : null}
              </div>
            </div>
          )}
        </div>

        <aside
          aria-label="Detalhe do registro"
          className="flex min-h-0 flex-col overflow-y-auto border-l border-[var(--line)] bg-[var(--surface)]"
        >
          {activeRow ? (
            <AtrativoDetailPanel
              key={activeRow.id}
              row={activeRow}
              editable={activeRow.routing === "dlq"}
              locked={locked}
              score={score}
            />
          ) : activeFalha ? (
            <FalhaDetailPanel key={activeFalha.source_ref} card={activeFalha} />
          ) : (
            <div className="m-auto max-w-[300px] px-6 py-10 text-center text-[13px] text-[var(--ink-3)]">
              Escolha um registro para ver o score, o motivo e a linha do tempo.
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}

function AtrativoRow({
  a,
  cls,
  selected,
  threshold,
  weight,
  onOpen,
}: {
  a: AtrativoListItem;
  cls: string;
  selected: boolean;
  threshold?: number;
  weight?: number;
  onOpen: () => void;
}) {
  const where = [a.municipio, a.uf].filter(Boolean).join(" · ");
  return (
    <div
      role="option"
      aria-selected={selected}
      data-testid="registros-row"
      data-row-id={a.id}
      onClick={onOpen}
      className={`${cls} grid-cols-[minmax(0,2.4fr)_150px_minmax(0,1.3fr)]`}
    >
      <div className="min-w-0">
        <b className="block truncate text-[13px] font-semibold">{a.name ?? "Sem nome"}</b>
        {where ? <span className="block truncate text-[11.5px] text-[var(--ink-3)]">{where}</span> : null}
      </div>
      {a.score != null ? (
        <ScoreBar score={a.score} potential={weight != null ? scorePotential(a.score, weight) : a.score} threshold={threshold} />
      ) : (
        <span className="text-[12px] text-[var(--ink-3)]">sem score</span>
      )}
      <div className="flex min-w-0 flex-wrap gap-1">
        {a.description_pending ? <Flag>Sem descrição</Flag> : null}
        {a.temporarily_closed ? <Flag tone="bad">Fechado temporariamente</Flag> : null}
      </div>
    </div>
  );
}

function FalhaRow({ f, cls, selected, onOpen }: { f: FailureCard; cls: string; selected: boolean; onOpen: () => void }) {
  return (
    <div
      role="option"
      aria-selected={selected}
      data-testid="registros-row"
      data-row-id={f.source_ref}
      onClick={onOpen}
      className={`${cls} grid-cols-[minmax(0,2fr)_minmax(0,1.6fr)]`}
    >
      <div className="min-w-0">
        <b className="block truncate text-[13px] font-semibold">{f.name ?? f.source_ref}</b>
        <span className="block truncate text-[11.5px] text-[var(--ink-3)]">
          {[f.uf, STAGE_LABELS[f.last_stage] ?? f.last_stage].filter(Boolean).join(" · ")}
        </span>
      </div>
      <div className="flex min-w-0 flex-wrap gap-1">
        {f.error ? (
          <span className="truncate" title={f.error}>
            <Flag tone="bad">{f.error}</Flag>
          </span>
        ) : null}
      </div>
    </div>
  );
}
