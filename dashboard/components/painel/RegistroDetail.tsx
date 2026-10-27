"use client";

import { useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useDeferredCommit } from "@/components/painel/useDeferredCommit";
import {
  atrativoKeys,
  editAtrativo,
  fetchAtrativoDetail,
  fetchFailureCardLog,
  type AtrativoListItem,
  type FailureCard,
  type RecordEvent,
} from "@/lib/atrativos-api";
import { WEIGHT_KEYS, WEIGHT_LABELS, type ConfigScore } from "@/lib/config-api";
import { engineKeys, transition } from "@/lib/engine-api";
import { STAGE_LABELS, explainReason, fmt1, scorePotential, type ReasonTone } from "@/lib/registros";

/**
 * Registros detail panel (#88): motivo + next step, the 5-component score
 * breakdown, the canonical fields (missing ones highlighted) and the event
 * timeline. Revisão records get promote / discard / edit through the deferred
 * commit (undo); Mar is read-only; Falha shows its quarantine log.
 *
 * LGPD: the phone comes ONLY from the list row's contacts_summary.phone_masked
 * (masked by the backend). normalized.phone / contacts are never read, and no
 * raw normalized JSON is rendered.
 */

export const BTN =
  "inline-flex h-8 items-center gap-1.5 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-[12.5px] font-semibold text-[var(--ink-2)] hover:bg-[var(--surface-3)] disabled:cursor-not-allowed disabled:opacity-50";
export const BTN_PRIMARY =
  "inline-flex h-8 items-center gap-1.5 rounded-lg border border-[var(--navy)] bg-[var(--navy)] px-3 text-[12.5px] font-semibold text-[var(--on-primary)] disabled:cursor-not-allowed disabled:opacity-50";

const TONE: Record<ReasonTone | "neutral", string> = {
  ok: "bg-[var(--mar-soft)] text-[var(--mar-ink)]",
  warn: "bg-[var(--dlq-soft)] text-[var(--dlq-ink)]",
  bad: "bg-[var(--falha-soft)] text-[var(--falha-ink)]",
  neutral: "bg-[var(--surface-2)] text-[var(--ink-2)]",
};
const REASON_BG: Record<ReasonTone, string> = {
  ok: "bg-[var(--mar-soft)]",
  warn: "bg-[var(--dlq-soft)]",
  bad: "bg-[var(--falha-soft)]",
};

export function Flag({ tone = "neutral", children }: { tone?: ReasonTone | "neutral"; children: ReactNode }) {
  return (
    <span className={`whitespace-nowrap rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold ${TONE[tone]}`}>
      {children}
    </span>
  );
}

const str = (v: unknown) => (typeof v === "string" && v.trim() ? v : null);
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : null;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.6px] text-[var(--ink-3)]">{title}</h3>
      {children}
    </section>
  );
}

function Head({ stage, tone, uf, name, where, children }: {
  stage: string;
  tone: ReasonTone;
  uf: string | null;
  name: string | null;
  where?: string | null;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Flag tone={tone}>{stage}</Flag>
        {uf ? <Flag>{uf}</Flag> : null}
      </div>
      <h2 className="text-[18px] font-semibold leading-tight tracking-[-0.3px] text-[var(--ink)]">{name ?? "Sem nome"}</h2>
      {where ? <div className="text-[12.5px] text-[var(--ink-3)]">{where}</div> : null}
      {children}
    </div>
  );
}

function Timeline({ events }: { events: RecordEvent[] }) {
  if (events.length === 0)
    return <p className="text-[12.5px] text-[var(--ink-3)]">Nenhum evento de pipeline registrado ainda.</p>;
  const dot = { ok: "border-[var(--mar)]", skip: "border-[var(--dlq)]", fail: "border-[var(--falha)] bg-[var(--falha)]" };
  return (
    <ol aria-label="Linha do tempo" className="flex flex-col">
      {events.map((e, i) => (
        <li key={`${e.stage}-${i}`} className="grid grid-cols-[14px_minmax(0,1fr)_auto] gap-2.5 pb-2.5 text-[12.5px]">
          <span className={`mt-0.5 size-3.5 rounded-full border-2 bg-[var(--surface)] ${dot[e.status] ?? dot.ok}`} />
          <div className="min-w-0">
            <span>{STAGE_LABELS[e.stage] ?? e.stage}</span>
            {e.message ? <small className="block break-words text-[11.5px] text-[var(--ink-3)]">{e.message}</small> : null}
          </div>
          {e.created_at ? <time className="font-mono text-[11.5px] text-[var(--ink-3)]">{when(e.created_at)}</time> : null}
        </li>
      ))}
    </ol>
  );
}

function Field({ id, label, value }: { id: string; label: string; value: ReactNode | null }) {
  const missing = value == null;
  return (
    <>
      <dt className="text-[var(--ink-3)]">{label}</dt>
      <dd
        data-testid={`registros-field-${id}`}
        data-missing={missing ? "true" : undefined}
        className={`m-0 min-w-0 [overflow-wrap:anywhere] ${missing ? "text-[var(--falha-ink)]" : ""}`}
      >
        {missing ? "ausente" : value}
      </dd>
    </>
  );
}

const EDITABLE = [
  { key: "name", label: "Nome" },
  { key: "address", label: "Endereço" },
  { key: "website", label: "Site" },
  { key: "descricao_editorial", label: "Descrição editorial" },
] as const;
type EditKey = (typeof EDITABLE)[number]["key"];

/** Detail of a Revisão (editable) or Mar (read-only) atrativo. */
export function AtrativoDetailPanel({
  row,
  editable,
  locked,
  score,
}: {
  row: AtrativoListItem;
  editable: boolean;
  locked: boolean;
  score?: ConfigScore;
}) {
  const qc = useQueryClient();
  const commit = useDeferredCommit();
  const [editing, setEditing] = useState<Record<EditKey, string> | null>(null);
  const { data: d } = useQuery({
    queryKey: atrativoKeys.detail(row.id),
    queryFn: () => fetchAtrativoDetail(row.id),
  });
  const n = d?.normalized ?? {};
  const name = row.name ?? str(n.name);
  const label = name ?? "Registro";
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: atrativoKeys.all });
    void qc.invalidateQueries({ queryKey: engineKeys.status });
  };
  const move = (to: "mar" | "descarte", message: string) =>
    commit({ message, run: () => transition("atrativo", row.id, { to, expected: "dlq" }), onDone: refresh });

  const current: Record<EditKey, string> = {
    name: name ?? "",
    address: str(n.address) ?? "",
    website: str(n.website) ?? "",
    descricao_editorial: str(n.descricao_editorial) ?? "",
  };
  const save = () => {
    if (!editing) return;
    const fields = Object.fromEntries(
      EDITABLE.filter(({ key }) => editing[key] !== current[key]).map(({ key }) => [key, editing[key]]),
    );
    setEditing(null);
    if (Object.keys(fields).length === 0) return void toast("Nada mudou.");
    commit({ message: `Edição de ${label} salva.`, run: () => editAtrativo(row.id, fields), onDone: refresh });
  };

  const reason = explainReason(d?.dlq_reason ?? null, score?.weight_validacao_humana ?? 0);
  const total = d?.score ?? row.score;
  const hours = Array.isArray(n.weekday_text) ? n.weekday_text.filter((h): h is string => typeof h === "string") : [];
  const reviews = num(n.review_count);
  const rating = num(n.rating);
  const lastReview = str(n.most_recent_review_at);
  const site = str(n.website) ?? row.contacts_summary?.website ?? null;
  const description = str(n.descricao_editorial);

  return (
    <>
      <div className="flex flex-col gap-[18px] p-[18px]">
        <Head
          stage={editable ? "Revisão" : "Mar"}
          tone={editable ? "warn" : "ok"}
          uf={row.uf}
          name={name}
          where={[row.municipio, row.uf].filter(Boolean).join(", ") || null}
        >
          <div className="flex flex-wrap gap-1">
            {row.temporarily_closed ? <Flag tone="bad">Fechado temporariamente</Flag> : null}
            {row.description_pending ? <Flag>Sem descrição</Flag> : null}
          </div>
        </Head>

        {d?.dlq_reason ? (
          <div
            data-testid="registros-motivo"
            className={`flex flex-col gap-1.5 rounded-[10px] p-3 text-[12.5px] text-[var(--ink)] ${REASON_BG[reason?.tone ?? "warn"]}`}
          >
            <b className="text-[13px]">{reason?.label ?? "Motivo não mapeado"}</b>
            {reason ? <span>{reason.why}</span> : null}
            {reason ? (
              <span className="text-[var(--ink-2)]">
                <b className="text-[12px]">Próximo passo:</b> {reason.next}
              </span>
            ) : null}
            <code className="break-words font-mono text-[11.5px]">dlq_reason = {d.dlq_reason}</code>
          </div>
        ) : null}

        {d ? (
          <Section title="Score de confiabilidade">
            <div data-testid="registros-breakdown" className="flex flex-col gap-2">
              {WEIGHT_KEYS.map((wk) => {
                const key = wk.replace("weight_", "");
                // score_breakdown already holds weighted points (score engine).
                const pts = Number(d.score_breakdown?.[key] ?? 0);
                const weight = score?.[wk];
                return (
                  <div key={key} data-testid={`registros-bd-${key}`} className="grid grid-cols-[130px_minmax(0,1fr)_54px] items-center gap-2.5 text-[12.5px]">
                    <span>
                      {WEIGHT_LABELS[wk]}{" "}
                      {weight != null ? <span className="text-[11px] text-[var(--ink-3)]">peso {weight}</span> : null}
                    </span>
                    <span className="h-1.5 overflow-hidden rounded-full bg-[var(--surface-2)]">
                      <i
                        className="block h-full rounded-full bg-[var(--navy)]"
                        style={{ width: `${weight ? Math.min(100, (pts / weight) * 100) : 0}%` }}
                      />
                    </span>
                    <span className="text-right font-mono text-[12px] font-semibold">{fmt1(pts)}</span>
                  </div>
                );
              })}
              <div data-testid="registros-bd-total" className="flex items-baseline justify-between border-t border-[var(--line-2)] pt-2 text-[12.5px]">
                <span>
                  {score && total != null && editable ? (
                    <BdPotential score={total} weight={score.weight_validacao_humana} threshold={score.threshold_mar} />
                  ) : score ? (
                    `Limiar do Mar: ${score.threshold_mar}`
                  ) : null}
                </span>
                <b className="font-mono text-[15px]">{total != null ? fmt1(total) : "—"}</b>
              </div>
            </div>
          </Section>
        ) : (
          <p className="text-[12.5px] text-[var(--ink-3)]">Carregando detalhe…</p>
        )}

        {d ? (
          <Section title="Campos">
            <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[12.5px]">
              <Field id="address" label="Endereço" value={str(n.address)} />
              <Field id="hours" label="Horário" value={hours.length ? hours.map((h) => <span key={h} className="block">{h}</span>) : null} />
              <Field id="site" label="Site" value={site} />
              <Field id="phone" label="Telefone" value={row.contacts_summary?.phone_masked ? <span className="font-mono">{row.contacts_summary.phone_masked}</span> : null} />
              <Field id="last-review" label="Última review" value={lastReview ? new Date(lastReview).toLocaleDateString("pt-BR") : null} />
              <Field id="reviews" label="Nº de reviews" value={reviews != null ? reviews.toLocaleString("pt-BR") : null} />
              <Field id="rating" label="Nota" value={rating != null ? rating.toLocaleString("pt-BR", { maximumFractionDigits: 1 }) : null} />
              <Field id="description" label="Descrição" value={description} />
            </dl>
          </Section>
        ) : null}

        {editing ? (
          <form
            aria-label="Editar campos canônicos"
            className="flex flex-col gap-2 rounded-[10px] border border-[var(--line)] p-3"
            onSubmit={(e) => {
              e.preventDefault();
              save();
            }}
          >
            {EDITABLE.map(({ key, label: l }) => (
              <label key={key} className="flex flex-col gap-1 text-[12px] text-[var(--ink-3)]">
                {l}
                {key === "descricao_editorial" ? (
                  <textarea
                    rows={4}
                    value={editing[key]}
                    onChange={(e) => setEditing({ ...editing, [key]: e.target.value })}
                    className="rounded-lg border border-[var(--line)] bg-[var(--surface)] px-2 py-1.5 text-[12.5px] text-[var(--ink)]"
                  />
                ) : (
                  <input
                    value={editing[key]}
                    onChange={(e) => setEditing({ ...editing, [key]: e.target.value })}
                    className="h-8 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-2 text-[12.5px] text-[var(--ink)]"
                  />
                )}
              </label>
            ))}
            <div className="flex gap-2">
              <button type="submit" className={BTN_PRIMARY} disabled={locked}>Salvar</button>
              <button type="button" className={BTN} onClick={() => setEditing(null)}>Cancelar</button>
            </div>
          </form>
        ) : null}

        {d ? (
          <Section title="Linha do tempo">
            <Timeline events={d.events ?? []} />
          </Section>
        ) : null}
      </div>

      {editable ? (
        <div className="sticky bottom-0 mt-auto flex flex-wrap gap-2 border-t border-[var(--line)] bg-[var(--surface)] px-[18px] py-3">
          <button type="button" className={BTN_PRIMARY} disabled={locked} onClick={() => move("mar", `${label} vai para o Mar.`)}>
            Promover ao Mar
          </button>
          <button type="button" className={BTN} disabled={locked || !d} onClick={() => setEditing(current)}>
            Editar
          </button>
          <button
            type="button"
            className={`${BTN} text-[var(--falha-ink)]`}
            disabled={locked}
            onClick={() => move("descarte", `${label} descartado.`)}
          >
            Descartar
          </button>
          {locked ? (
            <span className="basis-full text-[11.5px] text-[var(--dlq-ink)]">
              Motor ligado: pause para promover, editar ou descartar.
            </span>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

function BdPotential({ score, weight, threshold }: { score: number; weight: number; threshold: number }) {
  const pot = scorePotential(score, weight);
  const gap = threshold - pot;
  return (
    <>
      Com validação humana: <b className="font-mono">{fmt1(pot)}</b> ·{" "}
      {gap > 0 ? (
        <span className="text-[var(--falha-ink)]">faltam {fmt1(gap)}</span>
      ) : (
        <span className="text-[var(--mar-ink)]">passa do limiar {threshold}</span>
      )}
    </>
  );
}

/** Detail of a Falha card: the quarantine error and its log. */
export function FalhaDetailPanel({ card }: { card: FailureCard }) {
  const { data } = useQuery({
    queryKey: ["failures", "log", card.source_ref],
    queryFn: () => fetchFailureCardLog(card.source_ref),
  });
  return (
    <div className="flex flex-col gap-[18px] p-[18px]">
      <Head stage="Falha" tone="bad" uf={card.uf} name={card.name} />
      <div className="flex flex-col gap-1.5 rounded-[10px] bg-[var(--falha-soft)] p-3 text-[12.5px]">
        <b className="text-[13px]">Falha no processamento</b>
        {card.error ? <code className="break-words font-mono text-[11.5px]">{card.error}</code> : null}
        <span className="text-[12px] text-[var(--ink-3)]">
          Etapa: {STAGE_LABELS[card.last_stage] ?? card.last_stage}
          {card.quarantined_at ? ` · em quarentena desde ${when(card.quarantined_at)}` : ""}
        </span>
      </div>
      <Section title="Log">
        {data ? <Timeline events={data.events} /> : <p className="text-[12.5px] text-[var(--ink-3)]">Carregando log…</p>}
      </Section>
    </div>
  );
}
