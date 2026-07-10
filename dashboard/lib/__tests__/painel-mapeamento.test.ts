import { describe, expect, it } from "vitest";

import {
  buildMapRows,
  buildPreview,
  cloneDefaultMaps,
  DEFAULT_MAPS,
  RAW,
  SOURCE_LABELS,
} from "@/lib/painel-mapeamento";

describe("buildMapRows", () => {
  it("returns one row per default mapping entry with resolved raw value", () => {
    const maps = cloneDefaultMaps();
    const rows = buildMapRows(maps, "tripadvisor");
    expect(rows).toHaveLength(DEFAULT_MAPS.tripadvisor.length);
    expect(rows[0]).toMatchObject({
      index: 0,
      src: "name",
      value: RAW.tripadvisor.name,
      canonical: "name",
      dimmed: false,
    });
  });

  it("flags rows mapped to '—' as dimmed", () => {
    const maps = cloneDefaultMaps();
    const rows = buildMapRows(maps, "tripadvisor");
    const ignored = rows.find((r) => r.src === "locationId");
    expect(ignored?.canonical).toBe("—");
    expect(ignored?.dimmed).toBe(true);
  });
});

describe("buildPreview", () => {
  it("ends with a 'source' row carrying the source label", () => {
    const maps = cloneDefaultMaps();
    const rows = buildPreview(maps, "tripadvisor");
    const last = rows[rows.length - 1];
    expect(last).toEqual({ key: "source", value: SOURCE_LABELS.tripadvisor });
  });

  it("reflects a changed mapping in the preview", () => {
    const maps = cloneDefaultMaps();
    // Re-route the rating field to 'review_count'.
    const ratingEntry = maps.tripadvisor.find((m) => m.src === "rating")!;
    ratingEntry.canonical = "review_count";
    // And drop the original review_count mapping so it does not shadow.
    const reviewsEntry = maps.tripadvisor.find((m) => m.src === "numReviews")!;
    reviewsEntry.canonical = "—";

    const rows = buildPreview(maps, "tripadvisor");
    const reviewCountRow = rows.find((r) => r.key === "review_count");
    expect(reviewCountRow?.value).toBe(RAW.tripadvisor.rating);
  });

  it("omits canonical fields with no mapped source", () => {
    const maps = cloneDefaultMaps();
    maps.tripadvisor.forEach((m) => {
      if (m.canonical === "name") m.canonical = "—";
    });
    const rows = buildPreview(maps, "tripadvisor");
    expect(rows.find((r) => r.key === "name")).toBeUndefined();
  });
});

describe("cloneDefaultMaps", () => {
  it("is a deep copy — mutating the result does not touch DEFAULT_MAPS", () => {
    const clone = cloneDefaultMaps();
    clone.tripadvisor[0].canonical = "MUTATED";
    expect(DEFAULT_MAPS.tripadvisor[0].canonical).toBe("name");
    expect(clone.tripadvisor[0]).not.toBe(DEFAULT_MAPS.tripadvisor[0]);
  });
});
