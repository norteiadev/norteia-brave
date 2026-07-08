"use client";

import { useEffect, useRef, useState } from "react";

import { BR_UFS, type TypeFilter } from "@/lib/painel-data";

const TYPE_OPTIONS: { key: TypeFilter; label: string }[] = [
  { key: "all", label: "Tudo" },
  { key: "destino", label: "Destinos" },
  { key: "atrativo", label: "Atrativos" },
];

export interface PainelFiltersProps {
  type: TypeFilter;
  onTypeChange: (t: TypeFilter) => void;
  uf: string | null;
  onUfChange: (uf: string | null) => void;
  counts?: { all: number; destino: number; atrativo: number };
}

/**
 * The Painel header's filter controls — a presentational, fully controlled pair:
 *   - a type segmented control (Tudo / Destinos / Atrativos) that reports the
 *     selected `TypeFilter` to the parent and marks the active button, and
 *   - a UF-scope dropdown that SINGLE-selects one of the 27 BR UFs (click to
 *     pick, click again or "Todas" to clear to null) — one state at a time so
 *     the whole board + counts scope to that UF ("acompanhar o processo por UF").
 *
 * State and data live in the container (plan 17-05); only the popover open/close
 * is local. Tokens are the scoped painel CSS vars only — no hardcoded hex.
 */
export function PainelFilters({
  type,
  onTypeChange,
  uf,
  onUfChange,
  counts,
}: PainelFiltersProps) {
  const [open, setOpen] = useState(false);
  const ufLabel = uf ?? "Todas";
  const ufRef = useRef<HTMLDivElement>(null);

  // Close the UF popover on any click outside it (anywhere on the page).
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ufRef.current && !ufRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2.5">
        <span className="text-[11px] font-semibold uppercase tracking-[0.4px] text-[var(--painel-muted)]">
          Mostrar
        </span>
        <div className="inline-flex gap-0.5 rounded-[9px] bg-[var(--painel-chip)] p-[3px]">
          {TYPE_OPTIONS.map((opt) => {
            const active = type === opt.key;
            return (
              <button
                key={opt.key}
                type="button"
                data-testid={`filter-type-${opt.key}`}
                data-active={active ? "true" : "false"}
                aria-pressed={active}
                onClick={() => onTypeChange(opt.key)}
                className={`flex items-center gap-1.5 rounded-[7px] px-3 py-1 text-[12.5px] font-medium transition-colors ${
                  active
                    ? "bg-[var(--card)] text-[var(--painel-text)] shadow-sm"
                    : "text-[var(--painel-muted)]"
                }`}
              >
                {opt.label}
                {counts ? (
                  <span className="font-mono text-[11px] text-[var(--painel-muted-2)]">
                    {counts[opt.key]}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>

      <div className="relative" ref={ufRef}>
        <button
          type="button"
          data-testid="filter-uf-trigger"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="flex h-8 items-center gap-2 rounded-lg border border-[var(--painel-border-outer)] bg-[var(--card)] px-3 text-[12.5px] font-medium text-[var(--painel-text)]"
        >
          <span className="text-[var(--painel-muted)]">Escopo UF</span>
          <strong className="font-semibold">{ufLabel}</strong>
          <span className="text-[10px] text-[var(--painel-muted-2)]">▾</span>
        </button>

        {open ? (
          <div className="absolute left-0 top-10 z-40 w-[248px] rounded-[11px] border border-[var(--painel-border-outer)] bg-[var(--card)] p-3 shadow-lg">
            <div className="mb-2.5 flex items-center justify-between">
              <span className="text-[11px] font-semibold uppercase tracking-[0.4px] text-[var(--painel-muted)]">
                Acompanhar por UF
              </span>
              <button
                type="button"
                data-testid="filter-uf-clear"
                onClick={() => onUfChange(null)}
                className="text-[11.5px] font-semibold text-[var(--painel-navy)]"
              >
                Todas
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {BR_UFS.map((code) => {
                const selected = uf === code;
                return (
                  <button
                    key={code}
                    type="button"
                    data-testid={`filter-uf-${code}`}
                    aria-pressed={selected}
                    onClick={() => onUfChange(selected ? null : code)}
                    className={`rounded border px-1.5 py-0.5 font-mono text-[11px] transition-colors ${
                      selected
                        ? "border-[var(--painel-navy)] bg-[var(--painel-chip)] font-medium text-[var(--painel-text)]"
                        : "border-[var(--painel-border-outer)] text-[var(--painel-muted)]"
                    }`}
                  >
                    {code}
                  </button>
                );
              })}
            </div>
            <p className="mt-[11px] text-[11px] leading-[1.4] text-[var(--painel-muted-2)]">
              Escopa o quadro e as contagens a uma UF para acompanhar o processo.
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
