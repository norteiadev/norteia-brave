"use client";

import type { ReactNode } from "react";

import { ScoreBreakdownPanel } from "@/components/dlq/ScoreBreakdownPanel";
import { StatusBadge } from "@/components/dlq/StatusBadge";
import { RampContext } from "@/components/gate/RampContext";
import { Separator } from "@/components/ui/separator";
import { maskedPhoneFrom, type GateQueueItem } from "@/lib/gate-api";

/**
 * GateReviewPanel — the WhatsApp-gate master-detail RIGHT pane (DASH-03, D-06).
 *
 * Reuses the DLQ master-detail scaffold pattern: like the DLQ `ReviewPanel`, it
 * renders the §7.6 `ScoreBreakdownPanel` + the `StatusBadge` + the Rio normalized
 * payload + an injected action bar. The gate GET already returns the full row, so
 * (unlike DLQ) there is no secondary detail fetch — the selected `item` drives
 * the panel directly. The RampContext panel rides along so the operator sees the
 * volume-ramp cap and the WhatsApp quality-rating state BEFORE approving outreach.
 *
 * LGPD (T-04-18): any phone shown is the ALREADY-MASKED value from the backend
 * ("telefone (minimizado)"); the raw `phone_e164` is never read or reconstructed.
 */
export function GateReviewPanel({
  item,
  actions,
}: {
  item: GateQueueItem | null;
  /** Injected gate action bar (Aprovar contato / Rejeitar atrativo). */
  actions?: (item: GateQueueItem) => ReactNode;
}) {
  if (item == null) {
    return (
      <div className="flex h-full items-center justify-center p-12 text-center text-[14px] text-muted-foreground">
        Selecione um atrativo na fila para revisar antes de aprovar o contato.
      </div>
    );
  }

  const maskedPhone = maskedPhoneFrom(item.normalized);

  return (
    <div className="flex h-full flex-col gap-6 overflow-auto p-6">
      <header className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h2 className="text-[20px] font-semibold leading-tight">Gate WhatsApp</h2>
          <span className="font-mono text-[12px] text-muted-foreground tabular-nums">
            {item.canonical_key ?? item.rio_id}
          </span>
        </div>
        <StatusBadge routing={item.routing} subState={item.sub_state ?? undefined} />
      </header>

      <RampContext />

      <ScoreBreakdownPanel
        breakdown={
          (item.normalized?.["score_breakdown"] as Record<string, unknown>) ?? {}
        }
        score={item.score}
      />

      <Separator />

      {/* LGPD: masked phone only — labeled "telefone (minimizado)", never raw e164. */}
      <section className="flex flex-col gap-1" data-testid="masked-phone">
        <h3 className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
          telefone (minimizado)
        </h3>
        <p className="font-mono text-[14px] tabular-nums">
          {maskedPhone ?? "—"}
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-2">
        <h3 className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
          Rio normalizado
        </h3>
        <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 font-mono text-[12px] leading-relaxed">
          {JSON.stringify(redactPhone(item.normalized), null, 2)}
        </pre>
      </section>

      {actions ? (
        <>
          <Separator />
          <div className="flex flex-wrap items-center gap-2">{actions(item)}</div>
        </>
      ) : null}
    </div>
  );
}

/**
 * Defensive LGPD belt-and-suspenders: even though the backend is the source of
 * truth for masking, never let a raw `phone_e164` (or obvious raw-phone keys)
 * reach the DOM via the normalized JSON dump. Masked fields are preserved.
 */
function redactPhone(
  normalized: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  if (!normalized) return {};
  const RAW_KEYS = new Set([
    "phone_e164",
    "phone",
    "telefone",
    "phone_number",
    "whatsapp",
    "whatsapp_e164",
  ]);
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(normalized)) {
    if (RAW_KEYS.has(k)) {
      out[k] = "[minimizado]";
    } else {
      out[k] = v;
    }
  }
  return out;
}
