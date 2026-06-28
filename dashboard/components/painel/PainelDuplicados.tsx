"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import {
  type DedupAction,
  type DedupDivergedField,
  type DedupPairItem,
  dedupKeys,
  fetchDedupPairs,
  resolveDedupPair,
} from "@/lib/dedup-api";

/**
 * PainelDuplicados — the "Revisão de Duplicados" view (Painel light theme).
 *
 * The validation layer surface: each pending candidate Rio that resembles an
 * already-published Mar record is shown as a pair card (candidate ≈ Mar) with a
 * "coincide" chip per matched field, a "diverge" chip per diverged field (with
 * the candidate vs Mar values), and a labeled similarity. The operator resolves
 * each via Mesclar / Manter ambos / Descartar, which fires the REAL resolve PATCH
 * through the BFF and invalidates the dedup query (TanStack invalidation, not the
 * design's mock simStep).
 *
 * Similarity is a compute-on-read stand-in (RESEARCH A1 — embeddings are a
 * zero-stub), so `similarity_source` is surfaced so the operator knows what the
 * number means. Pure scoped `--painel-*` token styling; the green Mar accent and
 * yellow validation banner use the design's exact oklch literals (no token).
 */

const ACTION_TOAST: Record<DedupAction, string> = {
  merge: "Mesclado no registro do Mar",
  keep: "Par mantido — ambos os registros preservados",
  discard: "Candidato descartado",
};

function explainError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "Falha ao resolver o duplicado.";
}

/** Human label for a similarity_source code (embedding_stub → readable). */
function similaritySourceLabel(source: string): string {
  if (source === "embedding_stub") return "sobreposição de tokens (embedding pendente)";
  return source;
}

export function PainelDuplicados() {
  const qc = useQueryClient();

  const { data } = useQuery({
    queryKey: dedupKeys.pairs(),
    queryFn: () => fetchDedupPairs(),
  });

  const resolve = useMutation({
    mutationFn: (vars: { pair: DedupPairItem; action: DedupAction }) =>
      resolveDedupPair(vars.pair.candidate_rio_id, {
        action: vars.action,
        mar_id: vars.pair.mar_id,
      }),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: (_res, vars) => toast.success(ACTION_TOAST[vars.action]),
    onSettled: () =>
      void qc.invalidateQueries({ queryKey: dedupKeys.all }),
  });

  const pairs: DedupPairItem[] = data?.items ?? [];

  return (
    <div className="h-full overflow-y-auto px-[22px] pb-7 pt-5">
      {/* Validation-layer banner (design lines 177-180). */}
      <div
        className="mb-[18px] flex max-w-[560px] items-start gap-[11px] rounded-[11px] px-[15px] py-[13px]"
        style={{
          background: "color-mix(in oklch, oklch(0.72 0.15 75) 9%, white)",
          border: "1px solid color-mix(in oklch, oklch(0.72 0.15 75) 26%, white)",
        }}
      >
        <span
          className="text-[15px] font-bold"
          style={{ color: "oklch(0.6 0.14 75)" }}
        >
          ⚠
        </span>
        <p className="m-0 text-[12px] leading-[1.5]" style={{ color: "#6b5418" }}>
          Camada de validação: cada par abaixo é um registro entrando que se
          assemelha a um já publicado no <strong>Mar</strong>. Resolva antes de
          promover para impedir duplicidade na plataforma.
        </p>
      </div>

      {pairs.length === 0 ? (
        <div
          data-testid="dedup-empty"
          className="px-5 py-[60px] text-center text-[var(--painel-muted-2)]"
        >
          <div className="mb-2 text-[30px]" style={{ color: "oklch(0.55 0.15 150)" }}>
            ✓
          </div>
          <div className="text-[14px] font-semibold text-[var(--painel-muted)]">
            Nenhum duplicado pendente
          </div>
          <div className="mt-[4px] text-[12px]">
            Todos os pares foram resolvidos.
          </div>
        </div>
      ) : (
        <div className="flex max-w-[880px] flex-col gap-[14px]">
          {pairs.map((pair) => (
            <PairCard
              key={pair.candidate_rio_id}
              pair={pair}
              pending={resolve.isPending}
              onResolve={(action) => resolve.mutate({ pair, action })}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function PairCard({
  pair,
  pending,
  onResolve,
}: {
  pair: DedupPairItem;
  pending: boolean;
  onResolve: (action: DedupAction) => void;
}) {
  const simPct = `${(pair.similarity * 100).toFixed(0)}%`;

  return (
    <div
      data-testid="dedup-pair"
      className="rounded-[13px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-[18px] py-4"
    >
      {/* Header: entity chip + UF · labeled similarity */}
      <div className="mb-[14px] flex items-center justify-between gap-[10px]">
        <div className="flex items-center gap-[9px]">
          <span
            className="rounded-[5px] px-[8px] py-[1px] text-[10.5px] font-semibold"
            style={{
              color: "oklch(0.55 0.13 75)",
              background: "color-mix(in oklch, oklch(0.72 0.15 75) 16%, white)",
            }}
          >
            {pair.entity_type}
          </span>
          <span className="rounded-[5px] bg-[var(--painel-chip)] px-[7px] py-[1px] font-mono text-[10.5px] font-semibold text-[var(--painel-muted-2)]">
            {pair.uf}
          </span>
        </div>
        <div className="flex items-center gap-[7px]">
          <span className="text-[11px] text-[var(--painel-muted-2)]">
            Similaridade
          </span>
          <span
            data-testid="dedup-similarity"
            title={similaritySourceLabel(pair.similarity_source)}
            className="font-mono text-[13px] font-semibold text-[var(--painel-navy)]"
          >
            {simPct}
          </span>
        </div>
      </div>

      {/* Candidate ≈ Mar */}
      <div className="flex items-stretch gap-[14px]">
        <div className="min-w-0 flex-1 rounded-[10px] border border-dashed border-[var(--painel-border-outer)] px-[13px] py-3">
          <div className="mb-[7px] text-[10px] font-semibold uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
            Entrando · candidato
          </div>
          <div className="mb-[6px] text-[14px] font-semibold">
            {pair.municipio ?? "—"}
          </div>
          <div className="flex flex-col gap-[3px] text-[11.5px] text-[var(--painel-muted)]">
            <span>UF: {pair.uf}</span>
            <span className="font-mono">{pair.candidate_rio_id}</span>
          </div>
        </div>
        <div className="flex items-center text-[18px] text-[var(--painel-hint)]">
          ≈
        </div>
        <div
          className="min-w-0 flex-1 rounded-[10px] px-[13px] py-3"
          style={{
            border: "1px solid color-mix(in oklch, oklch(0.62 0.17 150) 30%, white)",
            background: "color-mix(in oklch, oklch(0.62 0.17 150) 5%, white)",
          }}
        >
          <div
            className="mb-[7px] text-[10px] font-semibold uppercase tracking-[0.4px]"
            style={{ color: "oklch(0.5 0.13 150)" }}
          >
            Já publicado · Mar
          </div>
          <div className="mb-[6px] text-[14px] font-semibold">
            {pair.municipio ?? "—"}
          </div>
          <div className="flex flex-col gap-[3px] text-[11.5px] text-[var(--painel-muted)]">
            <span>UF: {pair.uf}</span>
            <span className="font-mono">{pair.mar_id}</span>
          </div>
        </div>
      </div>

      {/* Matched / diverged chips */}
      <div className="mt-[13px] flex flex-wrap items-center gap-[6px]">
        <span className="mr-[2px] text-[11px] text-[var(--painel-muted-2)]">
          Coincide:
        </span>
        {pair.matched_fields.length === 0 ? (
          <span className="text-[10.5px] text-[var(--painel-muted-2)]">—</span>
        ) : (
          pair.matched_fields.map((field) => (
            <span
              key={field}
              data-testid="dedup-matched-chip"
              className="rounded-[5px] px-[8px] py-[1px] text-[10.5px] font-semibold"
              style={{
                color: "oklch(0.5 0.13 150)",
                background: "color-mix(in oklch, oklch(0.62 0.17 150) 13%, white)",
              }}
            >
              {field}
            </span>
          ))
        )}
        <span className="mx-[2px] ml-[8px] text-[11px] text-[var(--painel-muted-2)]">
          Diverge:
        </span>
        {pair.diverged_fields.length === 0 ? (
          <span className="text-[10.5px] text-[var(--painel-muted-2)]">—</span>
        ) : (
          pair.diverged_fields.map((d) => (
            <span
              key={d.field}
              data-testid="dedup-diverged-chip"
              title={`candidato: ${fmtVal(d.candidate)} · mar: ${fmtVal(d.mar)}`}
              className="rounded-[5px] bg-[var(--painel-chip)] px-[8px] py-[1px] text-[10.5px] font-semibold text-[var(--painel-muted)]"
            >
              {d.field}
            </span>
          ))
        )}
      </div>

      {/* Resolve actions */}
      <div className="mt-[15px] flex items-center gap-[9px] border-t border-[var(--painel-border-inner)] pt-[14px]">
        <button
          type="button"
          data-testid="dedup-merge"
          disabled={pending}
          onClick={() => onResolve("merge")}
          className="h-[34px] cursor-pointer rounded-[8px] border-none bg-[var(--painel-navy)] px-[15px] text-[12.5px] font-semibold text-white disabled:opacity-50"
        >
          Mesclar no existente
        </button>
        <button
          type="button"
          data-testid="dedup-keep"
          disabled={pending}
          onClick={() => onResolve("keep")}
          className="h-[34px] cursor-pointer rounded-[8px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-[14px] text-[12.5px] font-semibold text-[var(--painel-text)] disabled:opacity-50"
        >
          Manter ambos
        </button>
        <button
          type="button"
          data-testid="dedup-discard"
          disabled={pending}
          onClick={() => onResolve("discard")}
          className="h-[34px] cursor-pointer rounded-[8px] border-none bg-transparent px-[14px] text-[12.5px] font-semibold disabled:opacity-50"
          style={{ color: "oklch(0.55 0.20 27)" }}
        >
          Descartar candidato
        </button>
      </div>
    </div>
  );
}

/** Render a diverged-field value compactly for the chip tooltip. */
function fmtVal(value: DedupDivergedField["candidate"]): string {
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
