/**
 * log-humanize — turn raw structlog event slugs into pt-BR human sentences.
 *
 * The sync-process log buffer emits technical snake_case events
 * ("places_enrich_kept_floor", "engine_run_complete"). This module maps the
 * meaningful sweep events to concise pt-BR wordings, with a smart fallback that
 * prettifies any unmapped slug and appends the most useful present fields.
 *
 * Pure — no React. Consumed by components/painel/PainelLogs.tsx.
 */
import type { LogLine } from "@/lib/logs-api";

/** Curated pt-BR templates for the meaningful sweep events. */
const CURATED: Record<string, (l: LogLine) => string> = {
  engine_run_complete: () => "Varredura concluída",
  engine_mode_set: (l) => `Motor: modo alterado para ${l.mode ?? "—"}`,
  engine_source_set: (l) => `Fonte de coleta alterada para ${l.source ?? "—"}`,
  engine_stop_requested: () => "Parada do motor solicitada",
  engine_stop_drain: () => "Motor parando — drenando fila",
  engine_mode_pause_drain: () => "Motor pausado — drenando fila",
  ta_keepalive_ok: () => "Sessão TripAdvisor renovada",
  ta_keepalive_skipped_no_session: () =>
    "Keep-alive TripAdvisor ignorado — sem sessão",
  ta_keepalive_skipped_offline: () =>
    "Keep-alive TripAdvisor ignorado — motor offline",
  places_enrich_kept_floor: () => "Atrativo enriquecido no Places — mantido no piso",
  places_enrich_failed_kept_floor: () =>
    "Enriquecimento Places falhou — mantido no piso",
  places_enrich_hard_descarte: () =>
    "Atrativo descartado após enriquecimento Places",
  places_text_search_ok: () => "Places: busca por texto OK",
  places_place_details_ok: () => "Places: detalhes do local OK",
  push_mar_permanent_failure: () => "Falha permanente ao publicar no Mar",
  copywriter_failed_kept_floor: () => "Copywriter falhou — mantido no piso",
  llm_slug_unavailable: () => "Modelo LLM indisponível",
  ramp_incremented: () => "Ramp de envio incrementado",
};

/** Fields worth surfacing in the smart-fallback suffix, in priority order. */
const SUFFIX_FIELDS = ["uf", "name", "source", "score", "count"] as const;
const MAX_LEN = 160;

/** Prettify a raw slug: split on "_"/".", capitalize first word, join with spaces. */
function prettifySlug(event: string): string {
  const words = event.split(/[_.]+/).filter(Boolean);
  if (words.length === 0) return event;
  const [first, ...rest] = words;
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(" ");
}

/** Compact " · key=value" suffix of the most useful present fields. */
function fieldSuffix(line: LogLine): string {
  const parts: string[] = [];
  for (const key of SUFFIX_FIELDS) {
    const v = line[key];
    if (v != null) parts.push(`${key}=${v}`);
  }
  return parts.length ? ` · ${parts.join(" ")}` : "";
}

/** Return a pt-BR human sentence for a log line. */
export function humanizeLogEvent(line: LogLine): string {
  const curated = CURATED[line.event];
  if (curated) return curated(line);
  const out = prettifySlug(line.event) + fieldSuffix(line);
  return out.length > MAX_LEN ? out.slice(0, MAX_LEN - 1) + "…" : out;
}

/** Format an ISO timestamp as "HH:mm:ss"; invalid/empty → "". */
export function formatLogTime(ts: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
