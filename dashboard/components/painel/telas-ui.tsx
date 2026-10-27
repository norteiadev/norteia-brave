"use client";

import type { ReactNode } from "react";

import {
  type DeferredCommit,
  useDeferredCommit,
} from "@/components/painel/useDeferredCommit";

/**
 * POC building blocks (docs/poc/painel-redesign: .panel, .pill, .btn, .seg,
 * .kpi, .switch) shared by Varreduras, Duplicados and Configuração. Colors come
 * only from the .painel-light tokens, so both themes follow.
 */

export function Panel({
  title,
  hint,
  right,
  children,
  testId,
}: {
  title: string;
  hint?: string;
  right?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section
      data-testid={testId}
      className="min-w-0 rounded-[12px] border border-[var(--line)] bg-[var(--surface)] p-4"
    >
      <div className="mb-3 flex flex-wrap items-baseline gap-[10px]">
        <h2 className="text-[13.5px] font-semibold tracking-[-0.1px] text-[var(--ink)]">
          {title}
        </h2>
        {hint && <span className="text-[12px] text-[var(--ink-3)]">{hint}</span>}
        {right && <div className="ml-auto">{right}</div>}
      </div>
      {children}
    </section>
  );
}

export type Tone = "ok" | "run" | "warn" | "bad" | "neutral";

const PILL_TONE: Record<Tone, string> = {
  ok: "bg-[var(--mar-soft)] text-[var(--mar-ink)]",
  run: "bg-[var(--navy-soft)] text-[var(--navy)]",
  warn: "bg-[var(--dlq-soft)] text-[var(--dlq-ink)]",
  bad: "bg-[var(--falha-soft)] text-[var(--falha-ink)]",
  neutral: "bg-[var(--surface-2)] text-[var(--ink-2)]",
};

export function Pill({
  tone = "neutral",
  children,
  ...rest
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
  "data-testid"?: string;
  "data-status"?: string;
}) {
  return (
    <span
      {...rest}
      className={`inline-flex items-center gap-[5px] whitespace-nowrap rounded-full px-2 py-[2px] text-[11.5px] font-semibold ${PILL_TONE[tone]}`}
    >
      {children}
    </span>
  );
}

const BTN_VARIANT = {
  default:
    "border-[var(--line)] bg-[var(--surface)] text-[var(--ink)] hover:enabled:bg-[var(--surface-3)]",
  primary:
    "border-[var(--navy)] bg-[var(--navy)] text-[var(--on-primary)] hover:enabled:opacity-90",
  danger:
    "border-[var(--line)] bg-[var(--surface)] text-[var(--falha-ink)] hover:enabled:bg-[var(--surface-3)]",
} as const;

export function Btn({
  variant = "default",
  small,
  className = "",
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof BTN_VARIANT;
  small?: boolean;
}) {
  return (
    <button
      type="button"
      {...rest}
      className={`inline-flex cursor-pointer items-center justify-center gap-[6px] whitespace-nowrap rounded-[8px] border font-semibold disabled:cursor-not-allowed disabled:opacity-45 ${
        small ? "min-h-[26px] px-[9px] text-[12px]" : "min-h-[30px] px-3 text-[12.5px]"
      } ${BTN_VARIANT[variant]} ${className}`}
    />
  );
}

/** Segmented control: one pressed button at a time (POC .seg). */
export function Seg<T extends string>({
  label,
  options,
  value,
  onChange,
  testIdPrefix,
}: {
  label: string;
  options: readonly { key: T; label: string }[];
  value: T;
  onChange: (key: T) => void;
  testIdPrefix?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex flex-wrap gap-[2px] rounded-[8px] bg-[var(--surface-2)] p-[2px]"
    >
      {options.map((o) => {
        const on = o.key === value;
        return (
          <button
            key={o.key || "all"}
            type="button"
            aria-pressed={on}
            data-active={on ? "true" : "false"}
            data-testid={testIdPrefix ? `${testIdPrefix}-${o.key || "all"}` : undefined}
            onClick={() => onChange(o.key)}
            className={`h-7 cursor-pointer whitespace-nowrap rounded-[6px] px-[10px] text-[12.5px] font-semibold ${
              on
                ? "bg-[var(--surface)] text-[var(--ink)] shadow-[var(--shadow)]"
                : "text-[var(--ink-3)]"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function Kpi({
  label,
  value,
  sub,
  testId,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  testId?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-[12px] border border-[var(--line)] bg-[var(--surface)] p-[14px]">
      <span className="text-[12px] font-semibold text-[var(--ink-2)]">{label}</span>
      <span
        data-testid={testId}
        className="font-mono text-[22px] font-semibold leading-[1.15] tracking-[-0.8px] text-[var(--ink)]"
      >
        {value}
      </span>
      {sub && <span className="text-[12px] text-[var(--ink-3)]">{sub}</span>}
    </div>
  );
}

/** On/off switch (POC .switch): a button with role="switch". */
export function Switch({
  checked,
  onChange,
  labelledBy,
  testId,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  labelledBy: string;
  testId?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      data-testid={testId}
      onClick={() => onChange(!checked)}
      className={`relative h-[22px] w-[38px] flex-none cursor-pointer rounded-full border transition-colors ${
        checked
          ? "border-[var(--mar)] bg-[var(--mar)]"
          : "border-[var(--line)] bg-[var(--surface-2)]"
      }`}
    >
      <span
        aria-hidden
        className={`absolute left-[2px] top-[2px] h-4 w-4 rounded-full transition-transform ${
          checked ? "translate-x-4 bg-[var(--surface)]" : "bg-[var(--ink-3)]"
        }`}
      />
    </button>
  );
}

export function UfBadge({ uf }: { uf: string }) {
  return (
    <span className="rounded-[5px] bg-[var(--surface-2)] px-[6px] py-[2px] font-mono text-[11px] font-semibold text-[var(--ink-2)]">
      {uf}
    </span>
  );
}

/**
 * useDeferredCommit plus an undo signal. `onUndo` runs when the operator
 * clicked "Desfazer" (nothing was sent); `onError` when the request failed.
 */
export function useUndoableCommit() {
  const commit = useDeferredCommit();
  return ({
    run,
    onUndo,
    onError,
    delayMs = 6000,
    ...rest
  }: DeferredCommit & { onUndo: () => void; onError?: () => void }) => {
    let ran = false;
    commit({
      ...rest,
      delayMs,
      run: () => {
        ran = true;
        return run().catch((err: unknown) => {
          onError?.();
          throw err; // the shared hook turns it into the explained toast
        });
      },
    });
    // ponytail: useDeferredCommit has no undo callback, so undo is inferred:
    // its timer was scheduled first with the same delay, so if run() has not
    // started when ours fires, the operator undid it. The restore lands at the
    // end of the window, not on the click; swap for an onUndo option there.
    setTimeout(() => {
      if (!ran) onUndo();
    }, delayMs);
  };
}
