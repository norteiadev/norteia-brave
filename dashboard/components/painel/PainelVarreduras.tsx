"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
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
 * PainelVarreduras — the "Varreduras" runs-history view (Painel light theme).
 *
 * Surfaces the durable engine-run trail (plan 17.1-02): each row is one engine
 * sweep with Início (started_at), UF(s), Fonte (source), Profundidade (depth),
 * Total, Sincr. (synced), Falhas (failed) and a colored Status pill
 * (concluido/parcial/falha/running). Two segmented controls (Fonte, Profundidade)
 * drive the real `GET /api/v1/runs` query through the BFF; the UF chips filter the
 * loaded set client-side over each run's `ufs` array.
 *
 * The "↺ Falhas" action fires the REAL reprocess PATCH
 * (`PATCH /api/v1/runs/{id}/reprocess`, steward-guarded) and invalidates the runs
 * query (TanStack invalidation). 7-day stat cards summarize the recent run set
 * (reusing the runs-api window helpers).
 *
 * Pure scoped `--painel-*` token styling; the status-pill accents use the
 * design's exact oklch literals (no token exists for them).
 */

/** Fonte (source) filter options — "" = Todas. */
const SOURCE_OPTIONS: { key: string; label: string }[] = [
  { key: "", label: "Todas" },
  { key: "tripadvisor", label: "TripAdvisor" },
];

/** Profundidade (depth) filter options — "" = Todas. */
const DEPTH_OPTIONS: { key: string; label: string }[] = [
  { key: "", label: "Todas" },
  { key: "nascente", label: "Nascente" },
  { key: "nascente_rio", label: "Rio" },
  { key: "nascente_rio_mar", label: "Mar" },
];

const DEPTH_LABEL: Record<string, string> = {
  nascente: "Nascente",
  nascente_rio: "Nascente → Rio",
  nascente_rio_mar: "Nascente → Rio → Mar",
};

/** Status pill accent (text + background) by run status. */
const STATUS_STYLE: Record<string, { text: string; bg: string; label: string }> = {
  concluido: {
    text: "oklch(0.5 0.13 150)",
    bg: "color-mix(in oklch, oklch(0.62 0.17 150) 14%, white)",
    label: "Concluído",
  },
  parcial: {
    text: "oklch(0.55 0.13 75)",
    bg: "color-mix(in oklch, oklch(0.72 0.15 75) 16%, white)",
    label: "Parcial",
  },
  falha: {
    text: "oklch(0.5 0.18 27)",
    bg: "color-mix(in oklch, oklch(0.55 0.20 27) 13%, white)",
    label: "Falha",
  },
  running: {
    text: "var(--painel-navy)",
    bg: "var(--painel-chip)",
    label: "Em execução",
  },
};

function explainError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "Falha ao reprocessar a varredura.";
}

/** Format an ISO timestamp as a compact pt-BR date+time, or "—" when absent. */
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

export function PainelVarreduras() {
  const qc = useQueryClient();
  const [source, setSource] = useState("");
  const [depth, setDepth] = useState("");
  const [uf, setUf] = useState("");

  const filters: RunsFilters = {
    source: source || undefined,
    depth: depth || undefined,
  };

  const { data } = useQuery({
    queryKey: runsKeys.list(filters),
    queryFn: () => fetchRuns(filters),
  });

  const reprocess = useMutation({
    mutationFn: (run: RunItem) => reprocessRun(run.id),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => toast.success("Reprocessamento das falhas iniciado"),
    onSettled: () => void qc.invalidateQueries({ queryKey: runsKeys.all }),
  });

  const allItems: RunItem[] = data?.items ?? [];

  // UF chips are derived from the loaded set; uf filtering is applied client-side
  // over each run's `ufs` array (the backend filters uf in Python too).
  const ufOptions = Array.from(new Set(allItems.flatMap((r) => r.ufs))).sort();
  const rows = uf ? allItems.filter((r) => r.ufs.includes(uf)) : allItems;

  const recent = recentRuns(rows);

  return (
    <div className="h-full overflow-y-auto px-[22px] pb-7 pt-5">
      {/* Filters: Fonte + Profundidade + UF */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SegGroup
          prefix="runs-source"
          options={SOURCE_OPTIONS}
          active={source}
          onSelect={setSource}
        />
        <SegGroup
          prefix="runs-depth"
          options={DEPTH_OPTIONS}
          active={depth}
          onSelect={setDepth}
        />
        {ufOptions.length > 0 && (
          <SegGroup
            prefix="runs-uf"
            options={[{ key: "", label: "Todas UF" }, ...ufOptions.map((u) => ({ key: u, label: u }))]}
            active={uf}
            onSelect={setUf}
          />
        )}
      </div>

      {/* 7-day stat cards */}
      <div className="mb-[18px] grid grid-cols-3 gap-[14px]">
        <SummaryCard label="Varreduras (7 dias)">
          <span
            data-testid="runs-stat-total"
            className="font-mono text-[26px] font-semibold tracking-[-0.5px] text-[var(--painel-navy)]"
          >
            {formatCount(totalRuns(recent))}
          </span>
        </SummaryCard>
        <SummaryCard label="Sincronizados (7 dias)">
          <span
            data-testid="runs-stat-synced"
            className="font-mono text-[26px] font-semibold tracking-[-0.5px]"
            style={{ color: "oklch(0.5 0.13 150)" }}
          >
            {formatCount(totalSynced(recent))}
          </span>
        </SummaryCard>
        <SummaryCard label="Falhas (7 dias)">
          <span
            data-testid="runs-stat-failed"
            className="font-mono text-[26px] font-semibold tracking-[-0.5px]"
            style={{ color: "oklch(0.55 0.20 27)" }}
          >
            {formatCount(totalFailed(recent))}
          </span>
        </SummaryCard>
      </div>

      {/* Runs table */}
      {rows.length === 0 ? (
        <div
          data-testid="runs-empty"
          className="px-5 py-[60px] text-center text-[var(--painel-muted-2)]"
        >
          <div className="mb-2 text-[28px]">🛰️</div>
          <div className="text-[14px] font-semibold text-[var(--painel-muted)]">
            Nenhuma varredura registrada
          </div>
          <div className="mt-[4px] text-[12px]">
            As varreduras do motor aparecem aqui assim que executadas.
          </div>
        </div>
      ) : (
        <div className="max-w-[1100px] overflow-hidden rounded-[13px] border border-[var(--painel-border-outer)] bg-[var(--card)]">
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr className="text-left text-[10.5px] uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
                <Th>Início</Th>
                <Th>UF</Th>
                <Th>Fonte</Th>
                <Th>Profundidade</Th>
                <Th right>Total</Th>
                <Th right>Sincr.</Th>
                <Th right>Falhas</Th>
                <Th>Status</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {rows.map((run) => {
                const st = STATUS_STYLE[run.status] ?? STATUS_STYLE.running;
                const hasFailures = run.failed > 0;
                return (
                  <tr
                    key={run.id}
                    data-testid="runs-row"
                    className="border-t border-[var(--painel-border-inner)]"
                  >
                    <Td>
                      <span className="font-mono text-[var(--painel-muted)]">
                        {fmtStarted(run.started_at)}
                      </span>
                    </Td>
                    <Td>
                      <span className="font-mono font-semibold">
                        {run.ufs.join(", ") || "—"}
                      </span>
                    </Td>
                    <Td>{run.source}</Td>
                    <Td>
                      <span title={DEPTH_LABEL[run.depth] ?? run.depth}>
                        {DEPTH_LABEL[run.depth] ?? run.depth}
                      </span>
                    </Td>
                    <Td right>
                      <span className="font-mono">{formatCount(run.total)}</span>
                    </Td>
                    <Td right>
                      <span
                        className="font-mono"
                        style={{ color: "oklch(0.5 0.13 150)" }}
                      >
                        {formatCount(run.synced)}
                      </span>
                    </Td>
                    <Td right>
                      <span
                        className="font-mono"
                        style={{
                          color: hasFailures
                            ? "oklch(0.55 0.20 27)"
                            : "var(--painel-muted-2)",
                        }}
                      >
                        {formatCount(run.failed)}
                      </span>
                    </Td>
                    <Td>
                      <span
                        data-testid="runs-status-pill"
                        data-status={run.status}
                        className="inline-flex rounded-[5px] px-[8px] py-[2px] text-[10.5px] font-semibold"
                        style={{ color: st.text, background: st.bg }}
                      >
                        {st.label}
                      </span>
                    </Td>
                    <Td>
                      <button
                        type="button"
                        data-testid="runs-reprocess"
                        disabled={!hasFailures || reprocess.isPending}
                        onClick={() => reprocess.mutate(run)}
                        title={
                          hasFailures
                            ? "Reprocessar as falhas desta varredura"
                            : "Sem falhas para reprocessar"
                        }
                        className="h-[28px] cursor-pointer rounded-[7px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-[10px] text-[11.5px] font-semibold text-[var(--painel-text)] disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        ↺ Falhas
                      </button>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SegGroup({
  prefix,
  options,
  active,
  onSelect,
}: {
  prefix: string;
  options: { key: string; label: string }[];
  active: string;
  onSelect: (key: string) => void;
}) {
  return (
    <div className="inline-flex gap-0.5 rounded-[9px] bg-[var(--painel-chip)] p-[3px]">
      {options.map((opt) => (
        <Seg
          key={opt.key || "all"}
          testId={`${prefix}-${opt.key || "all"}`}
          active={active === opt.key}
          onClick={() => onSelect(opt.key)}
        >
          {opt.label}
        </Seg>
      ))}
    </div>
  );
}

function Seg({
  testId,
  active,
  onClick,
  children,
}: {
  testId: string;
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      data-active={active ? "true" : "false"}
      aria-pressed={active}
      onClick={onClick}
      className={`flex h-7 items-center rounded-[7px] px-[11px] text-[12.5px] font-semibold transition-colors ${
        active
          ? "bg-[var(--card)] text-[var(--painel-navy)] shadow-sm"
          : "text-[var(--painel-muted)]"
      }`}
    >
      {children}
    </button>
  );
}

function SummaryCard({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-[12px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-[17px] py-[15px]">
      <div className="mb-[7px] text-[10.5px] uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
        {label}
      </div>
      {children}
    </div>
  );
}

function Th({
  children,
  right,
}: {
  children?: React.ReactNode;
  right?: boolean;
}) {
  return (
    <th
      className={`px-[14px] py-[10px] font-semibold ${right ? "text-right" : "text-left"}`}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  right,
}: {
  children?: React.ReactNode;
  right?: boolean;
}) {
  return (
    <td className={`px-[14px] py-[11px] ${right ? "text-right" : "text-left"}`}>
      {children}
    </td>
  );
}
