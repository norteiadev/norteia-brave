/**
 * One money formatter for the Painel. The API reports every cost in USD; BRL
 * is a display conversion.
 */

export type Currency = "USD" | "BRL";

// ponytail: fixed client rate, set on `date`, until the fx.usd_brl config key
// lands (Phase 1); the UI always shows it next to its date.
export const FX_USD_BRL = { rate: 5.42, date: "2026-10-26" } as const;

/** "2026-10-26" -> "26/10/2026". */
export function formatFxDate(iso: string = FX_USD_BRL.date): string {
  return iso.split("-").reverse().join("/");
}

/** pt-BR currency string for a USD amount ("US$ 29,44" / "R$ 159,56"). */
export function formatMoney(
  usd: number,
  currency: Currency,
  { digits = 2 }: { digits?: number } = {},
): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(currency === "BRL" ? usd * FX_USD_BRL.rate : usd);
}
