"use client";

import { useState } from "react";

import { PainelLogs } from "@/components/painel/PainelLogs";
import { SOURCE_LABELS, type EngineSource } from "@/lib/engine-api";

/**
 * PainelLogsView — the "Logs" painel view (phase H).
 *
 * A thin inline wrapper around {@link PainelLogs}: a per-source segmented control
 * on top, and the always-polling inline log tail filling the rest. PainelLogs
 * itself keeps its slide-over behavior for the PainelTopbar terminal icon; here we
 * pass `inline` so it drops the overlay/fixed chrome and lives inside the shell.
 *
 * The log ring buffer is keyed by collection source (engine lane); we offer the
 * two lanes this milestone ships (Padrão / TripAdvisor).
 */

const SOURCE_OPTIONS: EngineSource[] = ["default", "tripadvisor"];

export function PainelLogsView() {
  const [source, setSource] = useState<EngineSource>("tripadvisor");

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-shrink-0 items-center gap-3 px-[22px] pb-3 pt-5">
        <div className="inline-flex gap-0.5 rounded-[9px] bg-[var(--painel-chip)] p-[3px]">
          {SOURCE_OPTIONS.map((s) => (
            <button
              key={s}
              type="button"
              data-testid={`logs-source-${s}`}
              data-active={source === s ? "true" : "false"}
              aria-pressed={source === s}
              onClick={() => setSource(s)}
              className={`flex h-7 items-center rounded-[7px] px-[11px] text-[12.5px] font-semibold transition-colors ${
                source === s
                  ? "bg-[var(--card)] text-[var(--painel-navy)] shadow-sm"
                  : "text-[var(--painel-muted)]"
              }`}
            >
              {SOURCE_LABELS[s]}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 px-[22px] pb-6">
        <div className="h-full overflow-hidden rounded-[13px] border border-[var(--painel-border-outer)]">
          <PainelLogs inline open source={source} onClose={() => {}} />
        </div>
      </div>
    </div>
  );
}
