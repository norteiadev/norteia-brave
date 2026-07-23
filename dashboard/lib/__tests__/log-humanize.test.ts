import { describe, it, expect } from "vitest";

import { humanizeLogEvent, formatLogTime } from "@/lib/log-humanize";
import type { LogLine } from "@/lib/logs-api";

const line = (over: Partial<LogLine> & { event: string }): LogLine => ({
  id: 1,
  ts: "2026-06-30T12:00:00Z",
  level: "info",
  ...over,
});

describe("humanizeLogEvent", () => {
  it("maps a curated slug to its pt-BR sentence with a field interpolated", () => {
    expect(humanizeLogEvent(line({ event: "engine_mode_set", mode: "auto" }))).toBe(
      "Motor: modo alterado para auto",
    );
    expect(humanizeLogEvent(line({ event: "engine_run_complete" }))).toBe(
      "Varredura concluída",
    );
  });

  it("null-guards a curated template when the field is absent", () => {
    expect(humanizeLogEvent(line({ event: "engine_mode_set" }))).toBe(
      "Motor: modo alterado para —",
    );
  });

  it("prettifies an unknown slug and appends a present field", () => {
    const out = humanizeLogEvent(
      line({ event: "ta_parse_skip_malformed_card", uf: "RJ" }),
    );
    expect(out).toBe("Ta parse skip malformed card · uf=RJ");
  });

  it("prettifies an unknown slug with no useful fields", () => {
    expect(humanizeLogEvent(line({ event: "some_other_event" }))).toBe(
      "Some other event",
    );
  });
});

describe("formatLogTime", () => {
  it("returns HH:mm:ss", () => {
    const d = new Date("2026-06-30T09:07:05Z");
    const pad = (n: number) => String(n).padStart(2, "0");
    const expected = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    expect(formatLogTime("2026-06-30T09:07:05Z")).toBe(expected);
    expect(formatLogTime("2026-06-30T09:07:05Z")).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });

  it("returns '' for empty or invalid input", () => {
    expect(formatLogTime("")).toBe("");
    expect(formatLogTime("not-a-date")).toBe("");
  });
});
