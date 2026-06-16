"use client";

import { useQuery } from "@tanstack/react-query";

import {
  MONITOR_REFETCH_INTERVAL_MS,
  fetchMonitor,
  monitorKeys,
  type MonitorData,
} from "@/lib/monitor-api";

/**
 * Shared monitor query hook (DASH-02, D-04).
 *
 * Every monitor surface (tiles / chart / alerts) reads through this single hook,
 * so they share the `monitorKeys.data(sinceHours)` cache entry — one network poll
 * feeds all three components (TanStack dedupes concurrent observers of the same
 * key). Liveness comes from `refetchInterval` (no WebSocket this milestone).
 */
export function useMonitor(sinceHours = 24) {
  return useQuery<MonitorData>({
    queryKey: monitorKeys.data(sinceHours),
    queryFn: () => fetchMonitor(sinceHours),
    refetchInterval: MONITOR_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });
}
