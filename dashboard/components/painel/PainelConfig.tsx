"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useCurrency } from "@/components/painel/currency";
import { Panel, Pill, Switch } from "@/components/painel/telas-ui";
import { useDeferredCommit } from "@/components/painel/useDeferredCommit";
import { SOURCE_LABELS } from "@/lib/engine-api";
import {
  type AppConfigSnapshot,
  type ConfigPatchBody,
  type WeightKey,
  WEIGHT_KEYS,
  WEIGHT_LABELS,
  configKeys,
  fetchConfig,
  updateConfig,
  weightsSum,
  weightsValid,
} from "@/lib/config-api";

/**
 * PainelConfig — "Configuração" (spec #82 Phase 0, POC view-config).
 *
 * Over GET/PATCH /api/v1/config:
 *   - Score: the five weights (slider + number, must sum to 100: Salvar is
 *     disabled client-side, the server 422 is the backstop) and `threshold_mar`.
 *   - Lanes: switches for the registered boolean keys only.
 *   - Limites de gasto: env-only caps, read-only (never part of the PATCH).
 * Edits pile up in a draft; the pending bar saves them through a deferred
 * commit, so the PATCH leaves only after the "Desfazer" window.
 *
 * Not here yet: the impact preview (needs POST /config/simulate) and the
 * engine mode (lives in the status bar).
 */

interface Lane {
  key: string;
  label: string;
  desc: string;
  read: (c: AppConfigSnapshot) => unknown;
}

const LANES: Lane[] = [
  ...Object.entries(SOURCE_LABELS).map(([name, label]) => ({
    key: `source.${name}.enabled`,
    label: `Coleta ${label}`,
    desc: "Lane de coleta: sweep por UF.",
    read: (c: AppConfigSnapshot) => c.sources?.[name],
  })),
  {
    key: "places_enrichment_enabled",
    label: "Enriquecimento Google Places",
    desc: "Horário, endereço e reviews. Gasta Places e Apify.",
    read: (c) => c.places_enrichment_enabled,
  },
  {
    key: "description_enrichment_enabled",
    label: "Descrição editorial",
    desc: "Escreve a descrição editorial na voz Norteia. Desligue para coletar sem custo de LLM.",
    read: (c) => c.description_enrichment_enabled,
  },
  {
    key: "atrativo_description_cascade_enabled",
    label: "Cascata de descrição",
    desc: "Busca aberta, gate de menção e escrita mais barata no lugar da busca do Claude.",
    read: (c) => c.atrativo_description_cascade_enabled,
  },
  {
    key: "atrativo_description_batch_enabled",
    label: "Descrição em lote (Batch API)",
    desc: "Rota Claude em lote: 50% mais barata, assíncrona (até 24 h).",
    read: (c) => c.atrativo_description_batch_enabled,
  },
  {
    key: "images_enrichment_enabled",
    label: "Lane de imagens",
    desc: "Capa e galeria do Wikidata e do Commons para o acervo do Brave.",
    read: (c) => c.images_enrichment_enabled,
  },
  {
    key: "images_push_enabled",
    label: "Enviar fotos para a API",
    desc: "Envia as fotos do acervo para a norteia-api. Desligado, elas esperam a revisão.",
    read: (c) => c.images_push_enabled,
  },
];

const ENV_LIMITS: { label: string; env: string; read: (c: AppConfigSnapshot) => unknown }[] = [
  {
    label: "Orçamento LLM diário",
    env: "BRAVE_LLM_USD_DAILY_BUDGET",
    read: (c) => (c.llm as { usd_daily_budget?: unknown } | undefined)?.usd_daily_budget,
  },
  { label: "Crédito do Google Places", env: "PLACES_TOTAL_USD_CAP", read: (c) => c.places_total_usd_cap },
  { label: "Places por dia", env: "PLACES_DAILY_USD_CAP", read: (c) => c.places_daily_usd_cap },
  { label: "Apify por dia", env: "APIFY_DAILY_USD_CAP", read: (c) => c.apify_daily_usd_cap },
  { label: "Apify total", env: "APIFY_TOTAL_USD_CAP", read: (c) => c.apify_total_usd_cap },
];

interface Form {
  weights: Record<WeightKey, number>;
  threshold: number;
  lanes: Record<string, boolean>;
}

function seed(c: AppConfigSnapshot): Form {
  const weights = {} as Record<WeightKey, number>;
  for (const k of WEIGHT_KEYS) weights[k] = Number(c.score[k]) || 0;
  const lanes: Record<string, boolean> = {};
  // A lane the snapshot does not carry is hidden, never guessed.
  for (const l of LANES) {
    const v = l.read(c);
    if (typeof v === "boolean") lanes[l.key] = v;
  }
  return { weights, threshold: Number(c.score.threshold_mar) || 0, lanes };
}

/** Readable diff lines plus the PATCH body: only changed keys, all five weights if any moved. */
function diff(draft: Form, base: Form): { lines: string[]; body: ConfigPatchBody } {
  const lines: string[] = [];
  const body: ConfigPatchBody = {};
  if (draft.threshold !== base.threshold) {
    lines.push(`Limiar ${base.threshold} → ${draft.threshold}`);
    body["score.threshold_mar"] = draft.threshold;
  }
  const moved = WEIGHT_KEYS.filter((k) => draft.weights[k] !== base.weights[k]);
  for (const k of moved)
    lines.push(`${WEIGHT_LABELS[k]} ${base.weights[k]} → ${draft.weights[k]}`);
  // The server checks the merged sum; sending the full set keeps it exact.
  if (moved.length) for (const k of WEIGHT_KEYS) body[`score.${k}`] = draft.weights[k];
  for (const l of LANES) {
    if (!(l.key in draft.lanes) || draft.lanes[l.key] === base.lanes[l.key]) continue;
    lines.push(`${l.label} ${draft.lanes[l.key] ? "ligada" : "desligada"}`);
    body[l.key] = draft.lanes[l.key];
  }
  return { lines, body };
}

export function PainelConfig() {
  const qc = useQueryClient();
  const commit = useDeferredCommit();
  const { format } = useCurrency();

  const { data } = useQuery({ queryKey: configKeys.snapshot, queryFn: fetchConfig });

  const [draft, setDraft] = useState<Form | null>(null);
  // Saved but still inside the undo window: the screen treats it as the baseline.
  const [optimistic, setOptimistic] = useState<Form | null>(null);

  useEffect(() => {
    if (!data) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reseed the draft from the authoritative snapshot (mount + post-save)
    setDraft(seed(data));
  }, [data]);

  if (!data || !draft) {
    return (
      <div className="grid h-full place-items-center text-[13px] text-[var(--ink-3)]">
        Carregando configuração…
      </div>
    );
  }

  const server = seed(data);
  const base = optimistic ?? server;
  const { lines, body } = diff(draft, base);
  const sum = weightsSum(draft.weights);
  const valid = weightsValid(draft.weights);

  const setWeight = (k: WeightKey, v: number) =>
    setDraft({ ...draft, weights: { ...draft.weights, [k]: v } });

  function save() {
    if (!draft) return;
    const saved = draft;
    const n = lines.length;
    setOptimistic(saved);
    commit({
      message: `${n} ${n === 1 ? "alteração será salva" : "alterações serão salvas"}.`,
      run: () => updateConfig(body),
      onDone: () => {
        toast.success(
          `${n} ${n === 1 ? "chave salva" : "chaves salvas"} em config_settings. Os scores já calculados não mudam.`,
        );
        void qc.invalidateQueries({ queryKey: configKeys.snapshot }).then(() =>
          setOptimistic(null),
        );
      },
      // Nothing was sent: back to what the server has.
      onUndo: () => {
        setOptimistic(null);
        setDraft(server);
      },
      // The draft stays, so the pending bar comes back with the server's reason.
      onError: () => setOptimistic(null),
    });
  }

  const shownEnv = ENV_LIMITS.flatMap((e) => {
    const v = e.read(data);
    return typeof v === "number" ? [{ ...e, value: v }] : [];
  });
  const batchOn = draft.lanes.atrativo_description_batch_enabled === true;

  return (
    <div className="h-full overflow-y-auto px-[22px] pb-8 pt-5">
      <div className="flex max-w-[1000px] flex-col gap-4">
        <Panel
          title="Score de confiabilidade"
          hint="os pesos somam 100 · o limiar decide quem pode ir ao Mar"
          right={
            <span data-testid="config-weight-sum" data-valid={valid ? "true" : "false"}>
              <Pill tone={valid ? "ok" : "bad"}>
                {valid ? "Soma 100 ✓" : `Soma ${sum}: precisa ser 100`}
              </Pill>
            </span>
          }
        >
          {WEIGHT_KEYS.map((k) => (
            <WeightRow
              key={k}
              id={`w-${k}`}
              label={WEIGHT_LABELS[k]}
              testId={`config-weight-${k}`}
              value={draft.weights[k]}
              dirty={draft.weights[k] !== base.weights[k]}
              onChange={(v) => setWeight(k, v)}
            />
          ))}
          <div className="mt-2 border-t border-[var(--line-2)] pt-3">
            <WeightRow
              id="w-threshold"
              label="Limiar do Mar"
              testId="config-threshold-mar"
              value={draft.threshold}
              dirty={draft.threshold !== base.threshold}
              onChange={(v) => setDraft({ ...draft, threshold: v })}
            />
          </div>
          <p className="mt-3 text-[12px] text-[var(--ink-3)]">
            Salvar muda a regra para os próximos registros processados. Os scores já
            calculados não são recalculados.
          </p>
        </Panel>

        <Panel title="Lanes e enriquecimento" hint="liga e desliga sem reiniciar o worker">
          {LANES.filter((l) => l.key in draft.lanes).map((l, i) => (
            <div
              key={l.key}
              className={`grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-[2px] py-3 ${
                i ? "border-t border-[var(--line-2)]" : "pt-0"
              }`}
            >
              <div>
                <b id={`lane-${l.key}`} className="text-[13px] font-semibold text-[var(--ink)]">
                  {l.label}
                </b>{" "}
                <span className="font-mono text-[11px] text-[var(--ink-3)]">{l.key}</span>
              </div>
              <Switch
                checked={draft.lanes[l.key]}
                labelledBy={`lane-${l.key}`}
                onChange={(v) => setDraft({ ...draft, lanes: { ...draft.lanes, [l.key]: v } })}
              />
              <p className="col-start-1 m-0 text-[12px] text-[var(--ink-3)]">
                {l.desc}
                {l.key === "atrativo_description_cascade_enabled" && batchOn &&
                  " Sem efeito enquanto a descrição em lote estiver ligada."}
              </p>
            </div>
          ))}
        </Panel>

        {shownEnv.length > 0 && (
          <Panel title="Limites de gasto" hint="somente leitura: vêm do .env" testId="config-env-limits">
            {shownEnv.map((e, i) => (
              <div
                key={e.env}
                className={`grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 py-3 ${
                  i ? "border-t border-[var(--line-2)]" : "pt-0"
                }`}
              >
                <div>
                  <b className="text-[13px] font-semibold text-[var(--ink)]">{e.label}</b>{" "}
                  <span className="font-mono text-[11px] text-[var(--ink-3)]">{e.env}</span>
                </div>
                <span className="rounded-[6px] bg-[var(--surface-2)] px-2 py-1 font-mono text-[13px] font-semibold text-[var(--ink-2)]">
                  {format(e.value)}
                </span>
              </div>
            ))}
            <p className="mt-3 text-[12px] text-[var(--ink-3)]">
              Para mudar, edite o .env e recrie os serviços com{" "}
              <span className="font-mono">docker compose up -d worker api</span> (restart não relê o .env).
            </p>
          </Panel>
        )}

        {lines.length > 0 && (
          <div
            data-testid="config-save-bar"
            className="sticky bottom-3 flex flex-wrap items-center gap-2 rounded-[12px] bg-[var(--ink)] py-2 pl-[14px] pr-[10px] text-[13px] text-[var(--bg)] shadow-[var(--shadow)]"
          >
            <span>
              <b className="font-mono">{lines.length}</b>{" "}
              {lines.length === 1 ? "alteração" : "alterações"}: {lines.slice(0, 3).join(" · ")}
              {lines.length > 3 ? " …" : ""}
            </span>
            <span className="flex-1" />
            {!valid && (
              <span data-testid="config-weight-warning" className="text-[12px] font-semibold">
                Os pesos precisam somar 100
              </span>
            )}
            {/* Inverted bar (POC .bulk): its buttons carry their own colors. */}
            <button
              type="button"
              onClick={() => setDraft(base)}
              className="min-h-[30px] cursor-pointer rounded-[8px] border border-[var(--ink-3)] bg-transparent px-3 text-[12.5px] font-semibold text-[var(--bg)]"
            >
              Descartar
            </button>
            <button
              type="button"
              disabled={!valid}
              onClick={save}
              className="min-h-[30px] cursor-pointer rounded-[8px] border border-[var(--bg)] bg-[var(--bg)] px-3 text-[12.5px] font-semibold text-[var(--ink)] disabled:cursor-not-allowed disabled:opacity-45"
            >
              Salvar
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function WeightRow({
  id,
  label,
  testId,
  value,
  dirty,
  onChange,
}: {
  id: string;
  label: string;
  testId: string;
  value: number;
  dirty: boolean;
  onChange: (v: number) => void;
}) {
  const set = (raw: string) => onChange(Math.round(Number(raw)) || 0);
  const tone = dirty ? "font-bold text-[var(--navy)]" : "text-[var(--ink)]";
  return (
    <div className="grid grid-cols-[150px_minmax(0,1fr)_70px] items-center gap-3 py-[6px] max-sm:grid-cols-[1fr_70px]">
      <label htmlFor={id} className={`text-[13px] font-medium ${tone}`}>
        {label}
      </label>
      <input
        id={id}
        type="range"
        min={0}
        max={100}
        step={1}
        value={value}
        onChange={(e) => set(e.target.value)}
        className="w-full accent-[var(--navy)] max-sm:hidden"
      />
      <input
        type="number"
        min={0}
        max={100}
        step={1}
        aria-label={`${label} (número)`}
        data-testid={testId}
        value={value}
        onChange={(e) => set(e.target.value)}
        className={`h-8 w-[70px] rounded-[7px] border border-[var(--line)] bg-[var(--surface)] px-2 text-right font-mono text-[13px] ${tone}`}
      />
    </div>
  );
}
