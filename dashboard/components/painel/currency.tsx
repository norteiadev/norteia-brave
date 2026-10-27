"use client";

import { createContext, useContext, useId, useMemo, type ReactNode } from "react";

import { FX_USD_BRL, formatFxDate, formatMoney, type Currency } from "@/lib/money";

import { useStoredState } from "./useStoredState";

interface CurrencyContextValue {
  currency: Currency;
  setCurrency: (currency: Currency) => void;
  /** Format a USD amount in the operator's currency. */
  format: (usd: number, opts?: { digits?: number }) => string;
  fx: typeof FX_USD_BRL;
}

// Outside a provider (isolated component tests) money renders in USD.
const CurrencyContext = createContext<CurrencyContextValue>({
  currency: "USD",
  setCurrency: () => {},
  format: (usd, opts) => formatMoney(usd, "USD", opts),
  fx: FX_USD_BRL,
});

const CURRENCIES = ["USD", "BRL"] as const;

export function CurrencyProvider({ children }: { children: ReactNode }) {
  const [currency, setCurrency] = useStoredState<Currency>("brave.currency", CURRENCIES);
  const value = useMemo<CurrencyContextValue>(
    () => ({
      currency,
      setCurrency,
      format: (usd, opts) => formatMoney(usd, currency, opts),
      fx: FX_USD_BRL,
    }),
    [currency, setCurrency],
  );
  return <CurrencyContext.Provider value={value}>{children}</CurrencyContext.Provider>;
}

export function useCurrency(): CurrencyContextValue {
  return useContext(CurrencyContext);
}

const OPTIONS: { value: Currency; symbol: string; label: string }[] = [
  { value: "USD", symbol: "US$", label: "Dólar" },
  { value: "BRL", symbol: "R$", label: "Real" },
];

/**
 * US$ / R$ segmented switch. In reais, the fixed rate and its date are shown
 * next to it (not only in a hover title: touch and keyboard users see it too).
 */
export function CurrencySwitch() {
  const { currency, setCurrency, fx } = useCurrency();
  const fxId = useId();
  const rate = fx.rate.toLocaleString("pt-BR", { minimumFractionDigits: 2 });
  const brl = currency === "BRL";
  return (
    <span className="inline-flex items-center gap-[6px]">
      <div
        role="group"
        aria-label="Moeda"
        aria-describedby={brl ? fxId : undefined}
        title={`Câmbio fixo: R$ ${rate} por US$ 1 (definido em ${formatFxDate(fx.date)})`}
        className="inline-flex overflow-hidden rounded-[7px] border border-[var(--line)] text-[11.5px] font-semibold"
      >
        {OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-label={o.label}
            aria-pressed={currency === o.value}
            onClick={() => setCurrency(o.value)}
            className={`px-[8px] py-[3px] ${
              currency === o.value
                ? "bg-[var(--surface-2)] text-[var(--ink)]"
                : "text-[var(--ink-3)] hover:bg-[var(--surface-3)]"
            }`}
          >
            {o.symbol}
          </button>
        ))}
      </div>
      {brl ? (
        <span id={fxId} data-testid="currency-fx" className="whitespace-nowrap font-mono text-[11px] text-[var(--ink-3)]">
          câmbio fixo R$ {rate} · {formatFxDate(fx.date)}
        </span>
      ) : null}
    </span>
  );
}
