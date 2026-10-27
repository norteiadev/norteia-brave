"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Btn, LoadError, Pill, UfBadge } from "@/components/painel/telas-ui";
import { useDeferredCommit, usePendingCommits } from "@/components/painel/useDeferredCommit";
import {
  type DedupAction,
  type DedupDivergedField,
  type DedupPairItem,
  dedupKeys,
  fetchDedupPairs,
  resolveDedupPair,
} from "@/lib/dedup-api";
import { BR_UFS } from "@/lib/painel-data";

/**
 * PainelDuplicados — "Duplicados" (spec #82 Phase 0, POC view-duplicados).
 *
 * Master/detail over the CURRENT dedup contract: the list of candidate (Rio) ↔
 * Mar pairs and, for the selected one, the matched / diverged field names and
 * the similarity with what the API says it is. Mesclar / Manter ambos /
 * Descartar go through a deferred commit ("Desfazer" for ~6 s, then the PATCH).
 *
 * Not here yet (Phase 2 backend): field-by-field values, distance, merge
 * preview, candidate score and Mar push status. The diff the API returns today
 * is computed over raw payloads (LGPD), so values are shown only for plain
 * attraction fields; phone/contacts never render.
 */

const ACTION_TOAST: Record<DedupAction, string> = {
  merge: "Mesclado no registro do Mar.",
  keep: "Par mantido: os dois registros continuam.",
  discard: "Candidato descartado.",
};

/** Fields whose values are public attraction data; anything else shows its name only. */
const SAFE_VALUE_FIELDS = new Set([
  "name",
  "nome",
  "municipio",
  "address",
  "endereco",
  "category",
  "tipo",
  "source",
]);

/** Human label for a similarity_source code. */
function similaritySourceLabel(source: string): string {
  if (source === "embedding_stub") return "sobreposição de tokens (embedding pendente)";
  return source;
}

const dedupCommitKey = (candidateRioId: string) => `dedup:${candidateRioId}`;

const fmtSim = (s: number) =>
  s.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function safeValue(d: DedupDivergedField, v: unknown): string | null {
  if (!SAFE_VALUE_FIELDS.has(d.field)) return null;
  if (typeof v === "string" || typeof v === "number") return String(v);
  return null;
}

export function PainelDuplicados() {
  const qc = useQueryClient();
  const commit = useDeferredCommit();
  const [uf, setUf] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  // Resolved inside the undo window (or in flight): out of the list until
  // undone. Module-level, so leaving and coming back keeps the pair hidden.
  const pending = usePendingCommits();

  const { data, isError, refetch } = useQuery({
    queryKey: dedupKeys.pairs(uf || undefined),
    queryFn: () => fetchDedupPairs(uf || undefined),
  });

  const pairs = (data?.items ?? []).filter((p) => !pending.has(dedupCommitKey(p.candidate_rio_id)));
  const active = pairs.find((p) => p.candidate_rio_id === activeId) ?? pairs[0];
  // The server pairs only the first `limit` candidates; say so when it cut some.
  const truncated = data != null && data.total > data.limit;

  function resolve(pair: DedupPairItem, action: DedupAction) {
    const id = pair.candidate_rio_id;
    const i = pairs.indexOf(pair);
    setActiveId((pairs[i + 1] ?? pairs[i - 1])?.candidate_rio_id ?? null);
    commit({
      key: dedupCommitKey(id),
      message: ACTION_TOAST[action],
      run: () => resolveDedupPair(id, { action, mar_id: pair.mar_id }),
      // "Manter ambos" only writes an audit row today: the pair comes back
      // after this refetch.
      onDone: () => qc.invalidateQueries({ queryKey: dedupKeys.all }),
      onUndo: () => setActiveId(id),
    });
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-[var(--line)] px-[22px] py-3">
        <label className="flex items-center gap-2 text-[12px] font-semibold text-[var(--ink-2)]">
          UF
          <select
            value={uf}
            onChange={(e) => setUf(e.target.value)}
            className="h-8 rounded-[7px] border border-[var(--line)] bg-[var(--surface)] px-2 text-[12.5px] text-[var(--ink)]"
          >
            <option value="">Todas</option>
            {BR_UFS.map((u) => (
              <option key={u}>{u}</option>
            ))}
          </select>
        </label>
        <span className="text-[12px] text-[var(--ink-3)]">
          Pares de um candidato do Rio com um registro do Mar no mesmo município.
        </span>
      </div>

      {!data ? (
        isError ? (
          <LoadError what="os pares" onRetry={() => void refetch()} />
        ) : (
          <div className="px-4 py-10 text-center text-[13px] text-[var(--ink-3)]">
            Carregando pares…
          </div>
        )
      ) : pairs.length === 0 && !truncated ? (
        <div data-testid="dedup-empty" className="px-4 py-10 text-center text-[var(--ink-3)]">
          <div className="text-[14px] font-semibold text-[var(--ink-2)]">
            Nenhum duplicado pendente
          </div>
          <div className="mt-1 text-[12px]">Nenhum par com esses filtros.</div>
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(280px,.8fr)_minmax(0,1.2fr)]">
          <div className="min-h-0 overflow-y-auto px-[22px] py-3">
            <section className="overflow-hidden rounded-[12px] border border-[var(--line)] bg-[var(--surface)]">
              <div className="flex items-center gap-2 px-3 py-[10px]">
                <span className="text-[13px] font-semibold">Pares para revisar</span>
                <span className="font-mono text-[12px] text-[var(--ink-3)]">{pairs.length}</span>
              </div>
              {truncated ? (
                <p data-testid="dedup-truncated" className="m-0 border-t border-[var(--line-2)] px-3 py-2 text-[12px] text-[var(--dlq-ink)]">
                  Só os primeiros {data.limit.toLocaleString("pt-BR")} de {data.total.toLocaleString("pt-BR")} candidatos
                  pendentes foram comparados. Filtre por UF para ver o restante.
                </p>
              ) : null}
              <div role="listbox" aria-label="Pares suspeitos">
                {pairs.map((p) => {
                  const sel = p === active;
                  return (
                    <div
                      key={p.candidate_rio_id}
                      role="option"
                      tabIndex={0}
                      aria-selected={sel}
                      data-testid="dedup-pair"
                      onClick={() => setActiveId(p.candidate_rio_id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") setActiveId(p.candidate_rio_id);
                      }}
                      className={`grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-[2px] border-t border-[var(--line-2)] px-3 py-[10px] hover:bg-[var(--surface-3)] ${
                        sel ? "bg-[var(--navy-soft)] shadow-[inset_3px_0_0_var(--navy)]" : ""
                      }`}
                    >
                      <b className="truncate text-[13px] font-semibold">{p.municipio ?? "—"}</b>
                      <span
                        className={`row-span-2 text-right font-mono text-[13px] font-semibold ${
                          p.similarity >= 0.9
                            ? "text-[var(--falha-ink)]"
                            : p.similarity >= 0.75
                              ? "text-[var(--dlq-ink)]"
                              : "text-[var(--ink-3)]"
                        }`}
                      >
                        {fmtSim(p.similarity)}
                      </span>
                      <span className="truncate text-[11.5px] text-[var(--ink-3)]">
                        {p.entity_type} · {p.uf} · {p.candidate_rio_id}
                      </span>
                    </div>
                  );
                })}
              </div>
            </section>
          </div>

          {active && <PairDetail pair={active} onResolve={(a) => resolve(active, a)} />}
        </div>
      )}
    </div>
  );
}

function PairDetail({
  pair,
  onResolve,
}: {
  pair: DedupPairItem;
  onResolve: (action: DedupAction) => void;
}) {
  return (
    <aside
      data-testid="dedup-detail"
      aria-label="Comparação do par"
      className="flex min-h-0 flex-col overflow-y-auto border-l border-[var(--line)] bg-[var(--surface)]"
    >
      <div className="flex flex-col gap-[18px] p-[18px]">
        <div className="flex flex-wrap items-center gap-[6px]">
          <Pill tone={pair.similarity >= 0.9 ? "bad" : "warn"}>
            Similaridade <span data-testid="dedup-similarity">{fmtSim(pair.similarity)}</span>
          </Pill>
          <UfBadge uf={pair.uf} />
          <span className="text-[12px] text-[var(--ink-3)]">
            {pair.municipio ?? "—"} · {similaritySourceLabel(pair.similarity_source)}
          </span>
        </div>

        <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-[10px]">
          <NameBox title="Candidato · Rio" id={pair.candidate_rio_id} />
          <span aria-hidden className="font-mono text-[15px] font-semibold">
            ≈
          </span>
          <NameBox title="Já no Mar" id={pair.mar_id} />
        </div>

        <section>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.6px] text-[var(--ink-3)]">
            Campos
          </h3>
          <div className="flex flex-wrap items-center gap-[6px] text-[12px]">
            <span className="text-[var(--ink-3)]">Coincide:</span>
            {pair.matched_fields.length === 0 && <span className="text-[var(--ink-4)]">—</span>}
            {pair.matched_fields.map((f) => (
              <span
                key={f}
                data-testid="dedup-matched-chip"
                className="rounded-[5px] bg-[var(--mar-soft)] px-[6px] py-[1px] text-[10.5px] font-semibold text-[var(--mar-ink)]"
              >
                {f}
              </span>
            ))}
          </div>
          <ul className="mt-2 flex flex-col gap-[6px] text-[12px]">
            <li className="text-[var(--ink-3)]">
              Diverge:{pair.diverged_fields.length === 0 && " —"}
            </li>
            {pair.diverged_fields.map((d) => {
              const cand = safeValue(d, d.candidate);
              const mar = safeValue(d, d.mar);
              return (
                <li key={d.field} className="flex flex-wrap items-baseline gap-[6px]">
                  <span
                    data-testid="dedup-diverged-chip"
                    className="rounded-[5px] bg-[var(--dlq-soft)] px-[6px] py-[1px] text-[10.5px] font-semibold text-[var(--dlq-ink)]"
                  >
                    {d.field}
                  </span>
                  {(cand !== null || mar !== null) && (
                    <span className="min-w-0 text-[var(--ink-2)] [overflow-wrap:anywhere]">
                      candidato: {cand ?? "—"} · Mar: {mar ?? "—"}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </section>

        <div className="flex flex-col gap-[6px] rounded-[10px] bg-[var(--surface-3)] p-3 text-[12.5px] text-[var(--ink-2)]">
          <b className="text-[13px] text-[var(--ink)]">O que cada ação faz hoje</b>
          <span>
            Mesclar: registra a origem do candidato no registro do Mar e descarta o
            candidato. Não preenche campos do Mar nem o republica.
          </span>
          <span>Manter ambos: só registra a decisão. O par continua nesta lista.</span>
          <span>Descartar: o candidato vai para Descarte.</span>
        </div>
      </div>

      <div className="sticky bottom-0 mt-auto flex flex-wrap gap-2 border-t border-[var(--line)] bg-[var(--surface)] px-[18px] py-3">
        <Btn variant="primary" onClick={() => onResolve("merge")}>
          Mesclar no Mar
        </Btn>
        <Btn onClick={() => onResolve("keep")}>Manter ambos</Btn>
        <Btn variant="danger" onClick={() => onResolve("discard")}>
          Descartar candidato
        </Btn>
      </div>
    </aside>
  );
}

function NameBox({ title, id }: { title: string; id: string }) {
  return (
    <div className="min-w-0 rounded-[10px] border border-[var(--line)] px-3 py-[10px]">
      <small className="mb-[2px] block text-[11px] uppercase tracking-[0.5px] text-[var(--ink-3)]">
        {title}
      </small>
      <span className="block truncate font-mono text-[12px] text-[var(--ink-2)]">{id}</span>
    </div>
  );
}
