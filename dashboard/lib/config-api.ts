/**
 * Runtime-config data layer (Phase H Config view).
 *
 * Typed fetchers for the operator-tunable config surface. Every call goes
 * through the BFF via `apiFetch` (relative `/api/...`, operator Bearer attached)
 * — never to FastAPI directly.
 *
 * Backing endpoints (brave/api/routers/config.py):
 *   GET   /api/v1/config   — effective AppConfig snapshot (secrets redacted to '***')
 *   PATCH /api/v1/config   — upsert a FLAT dotted-key → value map
 *
 * Only three families of keys are settable (anything else → 422 server-side):
 *   - `source.<name>.enabled`  = boolean
 *   - the five `score.weight_*` (MUST sum to 100 whenever any is touched)
 *     and `score.threshold_mar` — numbers ∈ [0, 100]
 *   - `engine.mode` ∈ {LIGADO, PAUSADO, DESLIGADO}
 *
 * The weight-sum-100 reliability invariant is enforced BOTH client-side (so an invalid
 * edit never leaves the form) and server-side (the authoritative 422 backstop).
 */

import { apiFetch } from "@/lib/api-client";

/** Operator engine mode — the settable `engine.mode` values. */
export type EngineMode = "LIGADO" | "PAUSADO" | "DESLIGADO";

export const ENGINE_MODES: readonly EngineMode[] = [
  "LIGADO",
  "PAUSADO",
  "DESLIGADO",
] as const;

export const ENGINE_MODE_LABELS: Record<EngineMode, string> = {
  LIGADO: "Ligado",
  PAUSADO: "Pausado",
  DESLIGADO: "Desligado",
};

/** The five reliability score-weight attribute names (under the `score.` block). */
export const WEIGHT_KEYS = [
  "weight_origem",
  "weight_completude",
  "weight_corroboracao",
  "weight_atualidade",
  "weight_validacao_humana",
] as const;

export type WeightKey = (typeof WEIGHT_KEYS)[number];

export const WEIGHT_LABELS: Record<WeightKey, string> = {
  weight_origem: "Origem",
  weight_completude: "Completude",
  weight_corroboracao: "Corroboração",
  weight_atualidade: "Atualidade",
  weight_validacao_humana: "Validação humana",
};

export interface ConfigScore {
  weight_origem: number;
  weight_completude: number;
  weight_corroboracao: number;
  weight_atualidade: number;
  weight_validacao_humana: number;
  threshold_mar: number;
  [key: string]: unknown;
}

export interface ConfigEngine {
  mode: EngineMode;
  [key: string]: unknown;
}

/** The effective config snapshot (env defaults + config_settings overlay). */
export interface AppConfigSnapshot {
  score: ConfigScore;
  engine: ConfigEngine;
  /** Collection sources keyed by name → enabled flag. */
  sources: Record<string, boolean>;
  /** Gates the LLM description-enrichment lane (off → local sweeps run without cost). */
  description_enrichment_enabled: boolean;
  [key: string]: unknown;
}

export interface ConfigPatchResult {
  updated: string[];
  config: AppConfigSnapshot;
}

/** A PATCH body value: number (weights/threshold), string (engine.mode) or bool (source enabled). */
export type ConfigPatchBody = Record<string, number | string | boolean>;

export const configKeys = {
  snapshot: ["config"] as const,
};

export function fetchConfig(): Promise<AppConfigSnapshot> {
  return apiFetch<AppConfigSnapshot>("api/v1/config");
}

export function updateConfig(body: ConfigPatchBody): Promise<ConfigPatchResult> {
  return apiFetch<ConfigPatchResult>("api/v1/config", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Sum the five reliability weights (client twin of the server invariant). */
export function weightsSum(weights: Record<WeightKey, number>): number {
  return WEIGHT_KEYS.reduce((sum, k) => sum + (Number(weights[k]) || 0), 0);
}

/** True when the five weights sum to 100 (±0.01) — mirrors the server guard. */
export function weightsValid(weights: Record<WeightKey, number>): boolean {
  return Math.abs(weightsSum(weights) - 100) <= 0.01;
}
