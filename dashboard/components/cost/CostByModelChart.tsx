"use client";

import type { CostWindowHours } from "@/lib/cost-api";

import { CostBarChart } from "./CostBarChart";

/**
 * CostByModelChart (DASH-04, UI-SPEC §5).
 *
 * Spend-per-model: a Recharts bar of summed USD cost grouped by `model_slug`
 * (`GET /api/v1/cost?group_by=model`), fetched through the BFF via `useQuery`
 * (useCost). Total USD headline in Geist Mono tabular-nums. Loading / empty
 * ("Sem dados no período") / 401 / error states are handled by `CostBarChart`.
 */
export function CostByModelChart({
  windowHours = 24 * 7,
}: {
  windowHours?: CostWindowHours;
}) {
  return (
    <CostBarChart
      groupBy="model"
      windowHours={windowHours}
      testId="cost-by-model"
      emptyKeyLabel="modelo"
    />
  );
}
