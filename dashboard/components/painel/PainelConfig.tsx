"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import {
  type ConfigPatchBody,
  type EngineMode,
  type WeightKey,
  ENGINE_MODES,
  ENGINE_MODE_LABELS,
  WEIGHT_KEYS,
  WEIGHT_LABELS,
  configKeys,
  fetchConfig,
  updateConfig,
  weightsSum,
  weightsValid,
} from "@/lib/config-api";

/**
 * PainelConfig — the "Configuração" painel view (phase H).
 *
 * The operator-tunable runtime surface over GET/PATCH /api/v1/config:
 *   - Fontes: per-source on/off (`source.<name>.enabled`) — toggled inline.
 *   - Pesos §7.6: the five score weights + `score.threshold_mar`. The five
 *     weights MUST sum to 100 — enforced CLIENT-SIDE here (Salvar disabled +
 *     live sum indicator) and again server-side (the authoritative 422).
 *   - Motor: `engine.mode` ∈ LIGADO / PAUSADO / DESLIGADO.
 *
 * Only the settable keys above are sent; the snapshot's other blocks (llm,
 * whatsapp, …) are read-only and not surfaced here.
 */

type WeightForm = Record<WeightKey, number>;

const ZERO_WEIGHTS: WeightForm = Object.fromEntries(
  WEIGHT_KEYS.map((k) => [k, 0]),
) as WeightForm;

function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada. Faça login novamente.";
    return err.message; // 422 → the server's weight-sum / range / mode detail
  }
  return "Falha ao atualizar a configuração.";
}

export function PainelConfig() {
  const qc = useQueryClient();

  const { data } = useQuery({
    queryKey: configKeys.snapshot,
    queryFn: fetchConfig,
  });

  // Editable form for the pesos block, seeded from the server snapshot (on mount
  // and after each successful save re-warms the snapshot).
  const [form, setForm] = useState<{ weights: WeightForm; threshold: number }>(
    () => ({ weights: ZERO_WEIGHTS, threshold: 0 }),
  );

  useEffect(() => {
    if (!data) return;
    const seed = { ...ZERO_WEIGHTS };
    for (const k of WEIGHT_KEYS) seed[k] = Number(data.score[k]) || 0;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- seed the editable form from the authoritative server snapshot (mount + post-save)
    setForm({ weights: seed, threshold: Number(data.score.threshold_mar) || 0 });
  }, [data]);

  const patch = useMutation({
    mutationFn: (body: ConfigPatchBody) => updateConfig(body),
    onError: (e) => toast.error(explainError(e)),
    onSuccess: (res) =>
      toast.success(`Configuração atualizada (${res.updated.length})`),
    onSettled: () =>
      void qc.invalidateQueries({ queryKey: configKeys.snapshot }),
  });

  if (!data) {
    return (
      <div className="grid h-full place-items-center text-[13px] text-[var(--painel-muted-2)]">
        Carregando configuração…
      </div>
    );
  }

  const sum = weightsSum(form.weights);
  const valid = weightsValid(form.weights);
  const sources = Object.entries(data.sources);
  const activeMode = data.engine.mode;

  function setWeight(key: WeightKey, value: number) {
    setForm((f) => ({ ...f, weights: { ...f.weights, [key]: value } }));
  }

  function savePesos() {
    const body: ConfigPatchBody = {};
    for (const k of WEIGHT_KEYS) body[`score.${k}`] = form.weights[k];
    body["score.threshold_mar"] = form.threshold;
    patch.mutate(body);
  }

  function toggleSource(name: string, next: boolean) {
    patch.mutate({ [`source.${name}.enabled`]: next });
  }

  function setMode(mode: EngineMode) {
    if (mode === activeMode) return;
    patch.mutate({ "engine.mode": mode });
  }

  return (
    <div className="h-full overflow-y-auto px-[22px] pb-8 pt-5">
      <div className="flex max-w-[720px] flex-col gap-5">
        {/* Motor mode */}
        <Card title="Modo do motor" subtitle="Estado operacional da coleta contínua">
          <div
            className="inline-flex gap-0.5 rounded-[9px] bg-[var(--painel-chip)] p-[3px]"
            data-testid="config-mode"
          >
            {ENGINE_MODES.map((m) => {
              const isActive = m === activeMode;
              return (
                <button
                  key={m}
                  type="button"
                  data-testid={`config-mode-${m}`}
                  data-active={isActive ? "true" : "false"}
                  aria-pressed={isActive}
                  disabled={patch.isPending}
                  onClick={() => setMode(m)}
                  className={`flex h-8 items-center rounded-[7px] px-[13px] text-[12.5px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    isActive
                      ? "bg-[var(--card)] text-[var(--painel-navy)] shadow-sm"
                      : "text-[var(--painel-muted)]"
                  }`}
                >
                  {ENGINE_MODE_LABELS[m]}
                </button>
              );
            })}
          </div>
        </Card>

        {/* Fontes */}
        <Card title="Fontes" subtitle="Habilita/desabilita cada lane de coleta">
          {sources.length === 0 ? (
            <div className="text-[12.5px] text-[var(--painel-muted-2)]">
              Nenhuma fonte configurada.
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              {sources.map(([name, enabled]) => (
                <label
                  key={name}
                  data-testid={`config-source-${name}`}
                  className="flex items-center justify-between rounded-[8px] px-[10px] py-[9px] hover:bg-[var(--painel-chip)]"
                >
                  <span className="text-[13px] font-medium text-[var(--painel-text)]">
                    {name}
                  </span>
                  <input
                    type="checkbox"
                    data-testid={`config-source-toggle-${name}`}
                    checked={enabled}
                    disabled={patch.isPending}
                    onChange={(e) => toggleSource(name, e.target.checked)}
                    className="h-4 w-4 cursor-pointer accent-[var(--painel-navy)] disabled:cursor-not-allowed"
                  />
                </label>
              ))}
            </div>
          )}
        </Card>

        {/* Pesos §7.6 + threshold */}
        <Card
          title="Pesos §7.6 e limiar do Mar"
          subtitle="Os cinco pesos devem somar 100"
        >
          <div className="flex flex-col gap-[10px]">
            {WEIGHT_KEYS.map((k) => (
              <Field key={k} label={WEIGHT_LABELS[k]}>
                <NumberInput
                  testId={`config-weight-${k}`}
                  value={form.weights[k]}
                  onChange={(v) => setWeight(k, v)}
                  disabled={patch.isPending}
                />
              </Field>
            ))}

            <div className="mt-1 flex items-center gap-2 text-[12px]">
              <span className="text-[var(--painel-muted-2)]">Soma dos pesos:</span>
              <span
                data-testid="config-weight-sum"
                data-valid={valid ? "true" : "false"}
                className="font-mono font-semibold"
                style={{
                  color: valid ? "oklch(0.5 0.13 150)" : "oklch(0.5 0.18 27)",
                }}
              >
                {sum}
              </span>
              {!valid && (
                <span
                  data-testid="config-weight-warning"
                  className="text-[11.5px]"
                  style={{ color: "oklch(0.5 0.18 27)" }}
                >
                  precisa somar 100
                </span>
              )}
            </div>

            <div className="my-2 h-px bg-[var(--painel-border-inner)]" />

            <Field label="Limiar do Mar (threshold_mar)">
              <NumberInput
                testId="config-threshold-mar"
                value={form.threshold}
                onChange={(v) => setForm((f) => ({ ...f, threshold: v }))}
                disabled={patch.isPending}
              />
            </Field>

            <div className="mt-2 flex justify-end">
              <button
                type="button"
                data-testid="config-save-pesos"
                onClick={savePesos}
                disabled={!valid || patch.isPending}
                className="h-[32px] cursor-pointer rounded-[8px] bg-[var(--painel-navy)] px-[16px] text-[12.5px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
              >
                Salvar pesos
              </button>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

function Card({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-[13px] border border-[var(--painel-border-outer)] bg-[var(--card)] p-[18px]">
      <div className="mb-[14px]">
        <h2 className="text-[14px] font-semibold text-[var(--painel-text)]">
          {title}
        </h2>
        <p className="mt-[2px] text-[11.5px] text-[var(--painel-muted-2)]">
          {subtitle}
        </p>
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex items-center justify-between gap-4">
      <span className="text-[12.5px] text-[var(--painel-text)]">{label}</span>
      {children}
    </label>
  );
}

function NumberInput({
  testId,
  value,
  onChange,
  disabled,
}: {
  testId: string;
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  return (
    <input
      type="number"
      data-testid={testId}
      value={Number.isFinite(value) ? value : 0}
      min={0}
      max={100}
      disabled={disabled}
      onChange={(e) => onChange(Number(e.target.value))}
      className="h-8 w-[92px] rounded-[7px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-[9px] text-right font-mono text-[12.5px] text-[var(--painel-text)] disabled:cursor-not-allowed disabled:opacity-50"
    />
  );
}
