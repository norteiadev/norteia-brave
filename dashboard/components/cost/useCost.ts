"use client";

import { useQuery } from "@tanstack/react-query";

import {
  type CostData,
  type CostGroupBy,
  type CostWindowHours,
  costKeys,
  fetchCost,
} from "@/lib/cost-api";

/**
 * Shared cost query hook (DASH-04, D-04).
 *
 * Each cost surface (by-lane chart / by-model chart / summary) reads through this
 * hook keyed by `(groupBy, windowHours)`, so observers of the same dimension +
 * window dedupe onto one network call (TanStack cache). No polling — cost is a
 * historical aggregate, refetched on the group-by / window controls only.
 */
export function useCost(groupBy: CostGroupBy, windowHours: CostWindowHours) {
  return useQuery<CostData>({
    queryKey: costKeys.data(groupBy, windowHours),
    queryFn: () => fetchCost(groupBy, windowHours),
    refetchOnWindowFocus: false,
  });
}
