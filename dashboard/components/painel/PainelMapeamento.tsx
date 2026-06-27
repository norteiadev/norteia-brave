"use client";

import { useState } from "react";

import {
  buildMapRows,
  buildPreview,
  CANON,
  cloneDefaultMaps,
  type MapEntry,
  type MapSourceKey,
  SOURCE_KEYS,
  SOURCE_LABELS,
} from "@/lib/painel-mapeamento";

/**
 * PainelMapeamento — the "Mapeamento da Origem" view.
 *
 * A REAL, client-side config editor for the data-mapper: it converts each
 * source's raw payload field into the Brave canonical structure. This mapping is
 * inherently LOCAL config (no backend) per product decision, so all state lives
 * here: the selected `source` and the editable `maps`. Changing a row's <select>
 * updates the live canonical preview on the right.
 *
 * Pure logic (RAW / CANON / DEFAULT_MAPS / buildMapRows / buildPreview) lives in
 * lib/painel-mapeamento.ts; this file is presentational + local-state only.
 *
 * Tokens: scoped painel CSS vars where available (--card, --painel-border-outer,
 * --painel-navy, --painel-chip, --painel-muted-2, etc.). The navy preview panel
 * uses the exact design literals it shows (#9fb3d4 subtext, green status dot).
 */
export function PainelMapeamento() {
  const [source, setSource] = useState<MapSourceKey>("mtur");
  const [maps, setMaps] =
    useState<Record<MapSourceKey, MapEntry[]>>(cloneDefaultMaps);

  const rows = buildMapRows(maps, source);
  const previewRows = buildPreview(maps, source);

  function onMapChange(index: number, canonical: string) {
    setMaps((prev) => {
      const nextSource = prev[source].map((m, i) =>
        i === index ? { ...m, canonical } : m,
      );
      return { ...prev, [source]: nextSource };
    });
  }

  return (
    <div className="h-full overflow-y-auto px-[22px] pb-7 pt-5">
      {/* intro + segmented source control */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="m-0 max-w-[560px] text-[12px] leading-[1.5] text-[var(--painel-muted)]">
          A camada <strong>data-mapper</strong> converte o registro bruto de
          cada fonte para a estrutura canônica do Brave. Ajuste o destino de cada
          campo; a pré-visualização à direita atualiza ao vivo.
        </p>
        <div className="inline-flex gap-[2px] rounded-[9px] bg-[var(--painel-chip)] p-[3px]">
          {SOURCE_KEYS.map((key) => {
            const on = source === key;
            return (
              <button
                key={key}
                type="button"
                data-testid={`map-source-${key}`}
                onClick={() => setSource(key)}
                className={`inline-flex h-7 items-center rounded-[7px] border-none px-[11px] text-[12.5px] font-semibold transition-all ${
                  on
                    ? "bg-[var(--card)] text-[var(--painel-navy)] shadow-[0_1px_2px_rgba(20,30,55,0.12)]"
                    : "bg-transparent text-[var(--painel-muted)]"
                }`}
              >
                {SOURCE_LABELS[key]}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex flex-wrap items-start gap-4">
        {/* left card: source fields → canonical */}
        <div className="min-w-[340px] flex-1 rounded-[13px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-[18px] py-4">
          <div className="mb-[3px] text-[13px] font-semibold">
            Campos da origem → canônico
          </div>
          <div className="mb-[14px] text-[11px] text-[var(--painel-muted-2)]">
            {SOURCE_LABELS[source]} · {rows.length} campos no payload bruto
          </div>
          <div className="flex flex-col gap-2">
            {rows.map((r) => (
              <div
                key={r.index}
                data-testid="map-row"
                className="flex items-center gap-[10px]"
                style={r.dimmed ? { opacity: 0.55 } : undefined}
              >
                <div className="min-w-0 flex-1">
                  <div className="overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[11.5px] font-semibold text-[var(--painel-navy)]">
                    {r.src}
                  </div>
                  <div className="overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[10.5px] text-[var(--painel-muted-2)]">
                    {r.value}
                  </div>
                </div>
                <span className="flex-shrink-0 text-[var(--painel-hint)]">→</span>
                <select
                  data-testid="map-select"
                  data-index={r.index}
                  value={r.canonical}
                  onChange={(e) => onMapChange(r.index, e.target.value)}
                  className="h-[34px] w-[150px] flex-shrink-0 cursor-pointer rounded-[8px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-[10px] text-[12px] text-[var(--painel-text)]"
                >
                  {CANON.map((o) => (
                    <option key={o.key} value={o.key}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        </div>

        {/* right panel: canonical preview */}
        <div className="sticky top-0 min-w-[300px] flex-1 rounded-[13px] bg-[var(--painel-navy)] px-[18px] py-4 text-white">
          <div className="mb-[3px] flex items-center gap-2 text-[13px] font-semibold">
            Registro canônico · Brave
          </div>
          <div className="mb-[14px] text-[11px] text-[#9fb3d4]">
            Resultado do mapeamento — entra no Nascente
          </div>
          <div className="flex flex-col gap-px overflow-hidden rounded-[9px] bg-white/10">
            {previewRows.map((pr) => (
              <div
                key={pr.key}
                data-testid="map-preview-row"
                className="flex items-center justify-between gap-3 bg-white/[0.03] px-[13px] py-[9px]"
              >
                <span className="font-mono text-[11px] text-[#9fb3d4]">
                  {pr.key}
                </span>
                <span className="max-w-[170px] overflow-hidden text-ellipsis whitespace-nowrap text-right font-mono text-[11.5px] font-semibold text-white">
                  {pr.value}
                </span>
              </div>
            ))}
          </div>
          <div className="mt-[13px] flex items-center gap-2 text-[11px] text-[#9fb3d4]">
            <span
              className="h-[6px] w-[6px] rounded-full"
              style={{ background: "oklch(0.7 0.17 150)" }}
            />
            Validado pelo schema canônico · {previewRows.length} campos
          </div>
        </div>
      </div>
    </div>
  );
}
