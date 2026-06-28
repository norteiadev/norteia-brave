"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import {
  type InjectTASessionBody,
  fetchTASessionStatus,
  injectTASession,
  taSessionKeys,
} from "@/lib/engine-api";

/**
 * PainelOrigem — the "Origem dos dados" source-pick modal (Painel light theme).
 *
 * The operator picks the collection source (mTur / TripAdvisor / Google Places).
 * For TripAdvisor the modal (re)establishes the scraper session: the operator
 * pastes the authenticated cURL captured in DevTools, which an in-modal parser
 * converts into the strict `SessionInjectBody` shape (cookies, query_ids,
 * user_agent, acquired_at) and submits through `injectTASession` over the BFF.
 *
 * The backend canary returns 422 `invalid_session` for a stale/malformed paste
 * and 503 `canary_unverified` for an infra fault — surfaced as DISTINCT toasts +
 * inline error states (RESEARCH §5 gotcha 1) so the operator never silently
 * accepts a bad session nor re-captures a scarce credential after a transient
 * fault. A TTL badge reads the real `expires_in` from the session status (warn at
 * 5 min). Pure scoped `--painel-*` token styling; the design's exact oklch/hex
 * literals are used only where no token exists (overlay, badges).
 */

export type OrigemSource = "mtur" | "tripadvisor" | "google_places";

/** Inline error kind surfaced after an inject attempt (distinct copy per status). */
type OrigemErrorKind = "422" | "503" | "other" | null;

/** TTL warn threshold (seconds) — mirrors the design's 5-min warn band. */
const TA_WARN_SECONDS = 5 * 60;

const SOURCE_ROWS: { key: OrigemSource; label: string; desc: string }[] = [
  {
    key: "mtur",
    label: "mTur",
    desc: "Cadastur · base oficial do Ministério do Turismo",
  },
  {
    key: "tripadvisor",
    label: "TripAdvisor",
    desc: "Scraper GraphQL · avaliações e popularidade",
  },
  {
    key: "google_places",
    label: "Google Places",
    desc: "Places API (New) · contatos e geolocalização",
  },
];

const SOURCE_LABEL: Record<OrigemSource, string> = {
  mtur: "mTur",
  tripadvisor: "TripAdvisor",
  google_places: "Google Places",
};

interface PainelOrigemProps {
  open: boolean;
  onClose: () => void;
  /** Source to preselect when the modal opens (from the live engine status). */
  initialSource?: OrigemSource;
}

/** Parsed subset of a cURL paste that maps onto the strict SessionInjectBody. */
export interface ParsedTACurl {
  cookies: Record<string, string>;
  query_ids: Record<string, string>;
  user_agent: string;
}

/**
 * Parse an operator's "Copy as cURL" paste into the cookies / query_ids /
 * user_agent triple the TA session endpoint requires. Cookie VALUES are only
 * ever passed straight to the BFF (never logged here). Resilient to single- or
 * double-quoted `-H`/`--header` flags and the `-b`/`--cookie` jar form.
 */
export function parseTACurl(curl: string): ParsedTACurl {
  const cookies: Record<string, string> = {};
  const query_ids: Record<string, string> = {};
  let user_agent = "";

  const splitCookieJar = (value: string) => {
    for (const part of value.split(";")) {
      const eq = part.indexOf("=");
      if (eq === -1) continue;
      const k = part.slice(0, eq).trim();
      const v = part.slice(eq + 1).trim();
      if (k) cookies[k] = v;
    }
  };

  // -H 'name: value' / --header "name: value"
  const headerRe = /(?:-H|--header)\s+(['"])([\s\S]*?)\1/gi;
  let m: RegExpExecArray | null;
  while ((m = headerRe.exec(curl)) !== null) {
    const header = m[2];
    const idx = header.indexOf(":");
    if (idx === -1) continue;
    const name = header.slice(0, idx).trim().toLowerCase();
    const value = header.slice(idx + 1).trim();
    if (name === "cookie") splitCookieJar(value);
    else if (name === "user-agent") user_agent = value;
  }

  // -b 'k=v; ...' / --cookie "k=v; ..."
  const cookieFlagRe = /(?:-b|--cookie)\s+(['"])([\s\S]*?)\1/gi;
  while ((m = cookieFlagRe.exec(curl)) !== null) splitCookieJar(m[2]);

  // GraphQL persisted-query ids carried in the request body. Key each by its
  // adjacent operationName when present, else positionally.
  const idRe =
    /preRegisteredQueryId\\?["']?\s*[:=]\s*\\?["']?([0-9a-fA-F]{8,})/g;
  let i = 0;
  while ((m = idRe.exec(curl)) !== null) {
    query_ids[`query_${i}`] = m[1];
    i += 1;
  }

  return { cookies, query_ids, user_agent };
}

/** Format a seconds count as m:ss, or "—" when absent. */
function fmtMMSS(seconds: number | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const t = Math.max(0, Math.floor(seconds));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

export function PainelOrigem({ open, onClose, initialSource }: PainelOrigemProps) {
  const qc = useQueryClient();
  const [source, setSource] = useState<OrigemSource>(initialSource ?? "mtur");
  const [curl, setCurl] = useState("");
  const [errorKind, setErrorKind] = useState<OrigemErrorKind>(null);

  // TTL badge reads the real session status; only polled while the modal is open.
  const { data: sessionStatus } = useQuery({
    queryKey: taSessionKeys.status,
    queryFn: fetchTASessionStatus,
    enabled: open,
    refetchOnWindowFocus: false,
  });

  const inject = useMutation({
    mutationFn: (body: InjectTASessionBody) => injectTASession(body),
    onSuccess: () => {
      setErrorKind(null);
      toast.success("Sessão TripAdvisor reconhecida");
      void qc.invalidateQueries({ queryKey: taSessionKeys.status });
      onClose();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 422) {
        setErrorKind("422");
        toast.error("Sessão inválida — cole um cURL atual do TripAdvisor.");
      } else if (err instanceof ApiError && err.status === 503) {
        setErrorKind("503");
        toast.error("Verificação indisponível — tente novamente em instantes.");
      } else {
        setErrorKind("other");
        toast.error(
          err instanceof ApiError ? err.message : "Falha ao injetar a sessão.",
        );
      }
    },
  });

  if (!open) return null;

  const taSelected = source === "tripadvisor";
  const curlText = curl.trim();
  const curlValid =
    /\bcurl\b/i.test(curlText) &&
    /tripadvisor|cookie|graphql/i.test(curlText) &&
    curlText.length > 40;
  const badge = !curlText
    ? { label: "Aguardando cURL", color: "#9ca3af", bg: "#f0eee9" }
    : curlValid
      ? {
          label: "✓ Sessão reconhecida",
          color: "oklch(0.5 0.13 150)",
          bg: "color-mix(in oklch, oklch(0.62 0.17 150) 16%, white)",
        }
      : {
          label: "Verifique o formato",
          color: "oklch(0.6 0.14 75)",
          bg: "color-mix(in oklch, oklch(0.72 0.15 75) 18%, white)",
        };

  const onSave = () => {
    if (source !== "tripadvisor") {
      toast.success(`Origem definida: ${SOURCE_LABEL[source]}`);
      onClose();
      return;
    }
    const parsed = parseTACurl(curl);
    inject.mutate({
      cookies: parsed.cookies,
      query_ids: parsed.query_ids,
      user_agent: parsed.user_agent,
      acquired_at: new Date().toISOString(),
    });
  };

  return (
    <div
      onClick={onClose}
      data-testid="painel-origem-overlay"
      className="fixed inset-0 z-[70] grid place-items-center p-6"
      style={{ background: "rgba(15,20,35,.4)" }}
    >
      <div
        role="dialog"
        aria-label="Origem dos dados"
        data-testid="painel-origem"
        onClick={(e) => e.stopPropagation()}
        className="w-[480px] max-w-full rounded-[15px] bg-[var(--card)] p-[22px]"
        style={{ boxShadow: "0 24px 60px rgba(15,23,42,.28)" }}
      >
        <div className="mb-1 text-[16px] font-bold tracking-[-0.2px] text-[var(--painel-text)]">
          Origem dos dados
        </div>
        <p className="m-0 mb-4 text-[12px] leading-[1.45] text-[var(--painel-muted)]">
          A camada <strong>data-mapper</strong> converte cada fonte para a
          estrutura canônica do Brave. A validação previne a inserção de
          atrativos/destinos já existentes na plataforma.
        </p>

        {/* Source radio rows */}
        <div className="flex flex-col gap-[9px]">
          {SOURCE_ROWS.map((row) => {
            const selected = source === row.key;
            return (
              <button
                key={row.key}
                type="button"
                role="radio"
                aria-checked={selected}
                data-testid={`origem-radio-${row.key}`}
                data-selected={selected ? "true" : undefined}
                onClick={() => {
                  setSource(row.key);
                  setErrorKind(null);
                }}
                className="flex items-center gap-[11px] rounded-[10px] border bg-[var(--card)] px-[13px] py-[12px] text-left transition-colors"
                style={{
                  borderColor: selected
                    ? "var(--painel-navy)"
                    : "var(--painel-border-outer)",
                  background: selected
                    ? "color-mix(in oklch, var(--painel-navy) 6%, white)"
                    : "var(--card)",
                }}
              >
                <span
                  className="h-[16px] w-[16px] flex-shrink-0 rounded-full border-2"
                  style={{
                    borderColor: selected ? "var(--painel-navy)" : "#cbd5e1",
                    background: selected
                      ? "radial-gradient(circle, var(--painel-navy) 0 4px, #fff 5px 16px)"
                      : "#fff",
                  }}
                  aria-hidden
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] font-semibold text-[var(--painel-text)]">
                    {row.label}
                  </span>
                  <span className="block text-[11.5px] leading-[1.35] text-[var(--painel-muted)]">
                    {row.desc}
                  </span>
                </span>
                {selected && (
                  <span
                    className="rounded-full px-[9px] py-[2px] text-[10px] font-semibold text-[var(--painel-navy)]"
                    style={{
                      background:
                        "color-mix(in oklch, var(--painel-navy) 10%, white)",
                    }}
                  >
                    Ativa
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* TripAdvisor cURL (re)inject */}
        {taSelected && (
          <div
            className="mt-[15px] border-t pt-[15px]"
            style={{ borderColor: "var(--painel-border-inner)" }}
          >
            <div className="mb-[7px] flex items-center justify-between gap-2">
              <label
                htmlFor="origem-curl"
                className="text-[11px] font-semibold uppercase tracking-[0.4px] text-[var(--painel-muted)]"
              >
                Comando cURL · sessão TripAdvisor
              </label>
              <span
                data-testid="origem-curl-badge"
                className="whitespace-nowrap rounded-[6px] px-[8px] py-[2px] text-[10px] font-semibold"
                style={{ color: badge.color, background: badge.bg }}
              >
                {badge.label}
              </span>
            </div>
            <textarea
              id="origem-curl"
              data-testid="origem-curl"
              value={curl}
              spellCheck={false}
              onChange={(e) => setCurl(e.target.value)}
              placeholder="curl 'https://www.tripadvisor.com/data/graphql/ids' -H 'cookie: ...' -H 'user-agent: ...' --data-raw '...'"
              className="min-h-[104px] w-full resize-y rounded-[9px] border px-[12px] py-[10px] font-mono text-[11.5px] leading-[1.55] text-[var(--painel-text)]"
              style={{
                borderColor: "var(--painel-border-outer)",
                background: "var(--painel-chip)",
              }}
            />
            <div className="mt-[7px] flex items-center justify-between gap-2">
              <p className="m-0 text-[11px] leading-[1.45] text-[var(--painel-muted-2)]">
                Cole o cURL da requisição GraphQL autenticada (DevTools → Network
                → Copiar como cURL). O token dura ~30 min.
              </p>
              {sessionStatus?.present && (
                <span
                  data-testid="origem-ttl"
                  className="whitespace-nowrap rounded-[6px] px-[8px] py-[2px] font-mono text-[10.5px] font-semibold"
                  style={{
                    color:
                      sessionStatus.expires_in != null &&
                      sessionStatus.expires_in <= TA_WARN_SECONDS
                        ? "oklch(0.58 0.14 75)"
                        : "oklch(0.5 0.13 150)",
                    background: "var(--painel-chip)",
                  }}
                >
                  TTL {fmtMMSS(sessionStatus.expires_in)}
                </span>
              )}
            </div>

            {/* Distinct inline error states (422 stale vs 503 infra) */}
            {errorKind === "422" && (
              <p
                data-testid="origem-error-422"
                className="m-0 mt-[10px] rounded-[8px] px-[11px] py-[8px] text-[11.5px] font-medium"
                style={{
                  color: "oklch(0.5 0.18 27)",
                  background:
                    "color-mix(in oklch, oklch(0.55 0.20 27) 10%, white)",
                }}
              >
                Sessão inválida — o cURL está expirado ou malformado. Capture e
                cole um cURL atual do TripAdvisor.
              </p>
            )}
            {errorKind === "503" && (
              <p
                data-testid="origem-error-503"
                className="m-0 mt-[10px] rounded-[8px] px-[11px] py-[8px] text-[11.5px] font-medium"
                style={{
                  color: "oklch(0.55 0.13 75)",
                  background:
                    "color-mix(in oklch, oklch(0.72 0.15 75) 14%, white)",
                }}
              >
                Verificação indisponível — a sessão não pôde ser confirmada agora
                (falha de infraestrutura). Tente novamente em instantes.
              </p>
            )}
          </div>
        )}

        {/* Footer actions */}
        <div className="mt-[18px] flex justify-end gap-[9px]">
          <button
            type="button"
            data-testid="origem-cancel"
            onClick={onClose}
            className="h-[36px] rounded-[8px] border bg-[var(--card)] px-[15px] text-[12.5px] font-semibold text-[var(--painel-text)]"
            style={{ borderColor: "var(--painel-border-outer)" }}
          >
            Cancelar
          </button>
          <button
            type="button"
            data-testid="origem-submit"
            disabled={inject.isPending}
            onClick={onSave}
            className="h-[36px] rounded-[8px] border-none bg-[var(--painel-navy)] px-[17px] text-[12.5px] font-semibold text-white disabled:opacity-50"
          >
            Salvar origem
          </button>
        </div>
      </div>
    </div>
  );
}
