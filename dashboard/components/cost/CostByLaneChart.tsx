"use client";

import type { CostWindowHours } from "@/lib/cost-api";

import { CostBarChart } from "./CostBarChart";

/**
 * CostByLaneChart (DASH-04, UI-SPEC §5).
 *
 * Spend-per-lane: a Recharts bar of summed USD cost grouped by collection lane
 * (`GET /api/v1/cost?group_by=lane`), fetched through the BFF via `useQuery`
 * (useCost). Total USD headline in Geist Mono tabular-nums. Loading / empty
 * ("Sem dados no período") / 401 / error states are handled by `CostBarChart`.
 */
export function CostByLaneChart({
  windowHours = 24 * 7,
}: {
  windowHours?: CostWindowHours;
}) {
  return (
    <CostBarChart
      groupBy="lane"
      windowHours={windowHours}
      testId="cost-by-lane"
      emptyKeyLabel="lane"
    />
  );
}
