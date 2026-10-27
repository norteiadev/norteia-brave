import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * D-01 — Norteia brand token swap in globals.css.
 *
 * The shadcn neutral base was swapped for the Norteia palette:
 *   - --primary  : navy blue accent   (oklch hue ~253)
 *   - --accent   : terracota          (oklch hue ~30)
 *   - --background: off-white          (warm, near-white, hue ~90)
 * and the Tailwind v4 `@theme inline` token bridge must remain intact so the
 * CSS vars are exposed as utility colors (e.g. --color-primary).
 *
 * This is a content assertion against the real source file — no build needed.
 */

const CSS_PATH = resolve(__dirname, "..", "globals.css");
const css = readFileSync(CSS_PATH, "utf8");

/** Extract the value of a `--token:` declaration scoped to the :root block. */
function rootTokenValue(token: string): string | null {
  const rootMatch = css.match(/:root\s*\{([\s\S]*?)\}/);
  if (!rootMatch) return null;
  const body = rootMatch[1];
  const re = new RegExp(`--${token}\\s*:\\s*([^;]+);`);
  const m = body.match(re);
  return m ? m[1].trim() : null;
}

/** Parse the three numeric channels out of `oklch(L C H ...)`. */
function parseOklch(value: string): { l: number; c: number; h: number } | null {
  const m = value.match(
    /oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)/i,
  );
  if (!m) return null;
  return { l: Number(m[1]), c: Number(m[2]), h: Number(m[3]) };
}

describe("D-01 globals.css Norteia brand tokens", () => {
  it("defines --primary as a navy blue (oklch blue hue, chromatic)", () => {
    const value = rootTokenValue("primary");
    expect(value, "--primary must be declared in :root").not.toBeNull();

    const oklch = parseOklch(value as string);
    expect(oklch, `--primary must be an oklch color, got: ${value}`).not.toBeNull();

    // Navy = dark-ish, chromatic, blue hue band (~230–270).
    expect(oklch!.l).toBeLessThan(0.5); // dark
    expect(oklch!.c).toBeGreaterThan(0.05); // not greyscale
    expect(oklch!.h).toBeGreaterThanOrEqual(220);
    expect(oklch!.h).toBeLessThanOrEqual(280);
  });

  it("defines --accent as terracota (oklch warm orange/red hue, chromatic)", () => {
    const value = rootTokenValue("accent");
    expect(value, "--accent must be declared in :root").not.toBeNull();

    const oklch = parseOklch(value as string);
    expect(oklch, `--accent must be an oklch color, got: ${value}`).not.toBeNull();

    // Terracota = warm, chromatic, orange/red hue band (~15–55).
    expect(oklch!.c).toBeGreaterThan(0.05); // not greyscale
    expect(oklch!.h).toBeGreaterThanOrEqual(10);
    expect(oklch!.h).toBeLessThanOrEqual(55);
  });

  it("defines --background as an off-white (very light, warm, near-neutral)", () => {
    const value = rootTokenValue("background");
    expect(value, "--background must be declared in :root").not.toBeNull();

    const oklch = parseOklch(value as string);
    expect(
      oklch,
      `--background must be an oklch color, got: ${value}`,
    ).not.toBeNull();

    // Off-white = very light but NOT pure white (#fff would be L=1 C=0).
    expect(oklch!.l).toBeGreaterThan(0.9);
    expect(oklch!.l).toBeLessThan(1.0);
    // Warm tint => some chroma present (distinguishes it from neutral white).
    expect(oklch!.c).toBeGreaterThan(0);
  });

  it("keeps the @theme inline block intact and bridges brand tokens to utilities", () => {
    const themeMatch = css.match(/@theme\s+inline\s*\{([\s\S]*?)\}/);
    expect(themeMatch, "@theme inline block must be present").not.toBeNull();

    const themeBody = themeMatch![1];
    // The brand tokens must be re-exported as Tailwind color utilities.
    expect(themeBody).toContain("--color-primary: var(--primary)");
    expect(themeBody).toContain("--color-accent: var(--accent)");
    expect(themeBody).toContain("--color-background: var(--background)");
  });

  it("exposes the pipeline status tokens used by StageBadge/JourneyStepper", () => {
    // StageBadge and JourneyStepper reference these CSS vars directly.
    for (const token of ["status-mar", "status-dlq", "status-descarte"]) {
      const value = rootTokenValue(token);
      expect(value, `--${token} must be declared in :root`).not.toBeNull();
      expect(parseOklch(value as string), `--${token} must be oklch`).not.toBeNull();
    }
  });
});

/**
 * Painel redesign (#83) — POC tokens scoped to `.painel-light` (light) and
 * `.painel-light[data-painel-theme="dark"]` (dark). Both themes must keep WCAG
 * AA contrast (4.5:1) for text and for the status inks on their soft chips.
 */
function blockTokens(selector: string): Record<string, string> {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = css.match(new RegExp(`(?:^|\\n)${esc}\\s*\\{([\\s\\S]*?)\\}`));
  if (!m) return {};
  return Object.fromEntries(
    [...m[1].matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)].map((t) => [t[1], t[2].trim()]),
  );
}

function luminance(hex: string): number {
  const n = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => {
    const c = parseInt(n.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const POC_TOKENS = [
  "surface", "surface-2", "surface-3", "line", "line-2",
  "ink", "ink-2", "ink-3", "ink-4", "navy", "navy-soft", "on-primary",
  "mar", "mar-ink", "mar-soft", "dlq", "dlq-ink", "dlq-soft",
  "falha", "falha-ink", "falha-soft", "rio", "descarte", "focus", "shadow",
];

describe("Painel light/dark theme tokens", () => {
  const themes = {
    light: blockTokens(".painel-light"),
    dark: blockTokens('.painel-light[data-painel-theme="dark"]'),
  };

  for (const [name, t] of Object.entries(themes)) {
    it(`${name}: declares every POC token`, () => {
      for (const token of POC_TOKENS) expect(t[token], `--${token}`).toBeTruthy();
    });

    it(`${name}: text and status inks pass AA contrast`, () => {
      const pairs: [string, string][] = [
        ["ink", "surface"], ["ink-2", "surface"], ["ink-3", "surface"],
        ["ink", "surface-2"], ["ink-3", "surface-2"], ["navy", "surface"],
        ["on-primary", "navy"],
        ["mar-ink", "mar-soft"], ["dlq-ink", "dlq-soft"], ["falha-ink", "falha-soft"],
        ["mar-ink", "surface"], ["dlq-ink", "surface"], ["falha-ink", "surface"],
      ];
      for (const [fg, bg] of pairs) {
        expect(contrast(t[fg], t[bg]), `--${fg} on --${bg}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }

  it("remaps the legacy --painel-* and shadcn vars onto the POC tokens", () => {
    const t = themes.light;
    expect(t["painel-text"]).toBe("var(--ink)");
    expect(t["card"]).toBe("var(--surface)");
    expect(t["primary"]).toBe("var(--navy)");
  });
});
