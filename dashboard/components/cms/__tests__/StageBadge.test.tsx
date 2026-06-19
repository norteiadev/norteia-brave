import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StageBadge } from "@/components/cms/StageBadge";

/**
 * D-02 — StageBadge primitive (dedicated unit coverage).
 *
 * Behavioral surface per the component contract:
 *   - routing → human label (mar→MAR, dlq→DLQ, descarte→Descarte,
 *     in_progress→Em andamento); unknown routing renders the raw value.
 *   - subState → title-cased FSM label.
 *   - score → banded chip text (one decimal) + band color class
 *     (≥85 mar/green, 40–84.9 dlq/amber, <40 descarte/red).
 *   - source → friendly chip (mtur→Mtur, etc.); unknown source = raw.
 *   - validationPending → "Aguardando" flag chip.
 *   - all colors are CSS-var references, never hardcoded hex.
 *
 * Pure rendering — no network, deterministic.
 */

describe("D-02 StageBadge", () => {
  it("renders routing labels for each known routing state", () => {
    const cases: Array<[string, string]> = [
      ["mar", "MAR"],
      ["dlq", "DLQ"],
      ["descarte", "Descarte"],
      ["in_progress", "Em andamento"],
    ];
    for (const [routing, label] of cases) {
      const { unmount } = render(<StageBadge routing={routing} />);
      expect(screen.getByText(label)).toBeInTheDocument();
      unmount();
    }
  });

  it("is case-insensitive for routing and falls back to raw value when unknown", () => {
    const { unmount } = render(<StageBadge routing="MAR" />);
    expect(screen.getByText("MAR")).toBeInTheDocument();
    unmount();

    render(<StageBadge routing="weird_state" />);
    // Unknown routing renders the raw string rather than dropping the badge.
    expect(screen.getByText("weird_state")).toBeInTheDocument();
  });

  it("title-cases each atrativo FSM sub_state", () => {
    const cases: Array<[string, string]> = [
      ["discovered", "Discovered"],
      ["contacts_found", "Contacts Found"],
      ["signals_gathered", "Signals Gathered"],
      ["aguardando_consulta_whatsapp", "Aguardando Consulta Whatsapp"],
      ["whatsapp_in_progress", "Whatsapp In Progress"],
    ];
    for (const [sub, label] of cases) {
      const { unmount } = render(<StageBadge subState={sub} />);
      expect(screen.getByText(label)).toBeInTheDocument();
      unmount();
    }
  });

  it("renders score with one decimal and the correct band color per threshold", () => {
    // ≥85 → mar (green) band
    const { container: high, unmount: u1 } = render(<StageBadge score={92} />);
    expect(screen.getByText("92.0")).toBeInTheDocument();
    expect(high.querySelector(".text-\\[var\\(--status-mar\\)\\]")).not.toBeNull();
    u1();

    // exactly 85 → still mar band (inclusive lower bound)
    const { container: edge85, unmount: u2 } = render(<StageBadge score={85} />);
    expect(screen.getByText("85.0")).toBeInTheDocument();
    expect(edge85.querySelector(".text-\\[var\\(--status-mar\\)\\]")).not.toBeNull();
    u2();

    // 40–84.9 → dlq (amber) band
    const { container: mid, unmount: u3 } = render(<StageBadge score={60.5} />);
    expect(screen.getByText("60.5")).toBeInTheDocument();
    expect(mid.querySelector(".text-\\[var\\(--status-dlq\\)\\]")).not.toBeNull();
    u3();

    // <40 → descarte (red) band
    const { container: low, unmount: u4 } = render(<StageBadge score={12.3} />);
    expect(screen.getByText("12.3")).toBeInTheDocument();
    expect(low.querySelector(".text-\\[var\\(--status-descarte\\)\\]")).not.toBeNull();
    u4();
  });

  it("renders a score of 0 (does not treat 0 as absent)", () => {
    render(<StageBadge score={0} />);
    expect(screen.getByText("0.0")).toBeInTheDocument();
  });

  it("renders friendly source labels and falls back to the raw source", () => {
    const { unmount } = render(<StageBadge source="mtur" />);
    expect(screen.getByText("Mtur")).toBeInTheDocument();
    unmount();

    render(<StageBadge source="some_other_source" />);
    expect(screen.getByText("some_other_source")).toBeInTheDocument();
  });

  it("renders the 'Aguardando' validation-pending flag only when set", () => {
    const { unmount } = render(<StageBadge validationPending />);
    expect(screen.getByText("Aguardando")).toBeInTheDocument();
    unmount();

    render(<StageBadge validationPending={false} routing="mar" />);
    expect(screen.queryByText("Aguardando")).not.toBeInTheDocument();
  });

  it("renders nothing visible when all props are absent", () => {
    const { container } = render(<StageBadge />);
    // The wrapper span exists but contains no badge children.
    const wrapper = container.querySelector("span");
    expect(wrapper).not.toBeNull();
    expect(wrapper!.querySelectorAll("span").length).toBe(0);
  });

  it("uses CSS-var color tokens, never hardcoded hex colors", () => {
    const { container } = render(
      <StageBadge routing="mar" subState="discovered" score={90} source="mtur" validationPending />,
    );
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
    expect(container.innerHTML).toContain("var(--status-mar)");
    expect(container.innerHTML).toContain("var(--color-primary)");
  });

  it("renders all five badge facets simultaneously when every prop is supplied", () => {
    render(
      <StageBadge
        routing="dlq"
        subState="contacts_found"
        score={50}
        source="notebooklm"
        validationPending
      />,
    );
    expect(screen.getByText("DLQ")).toBeInTheDocument();
    expect(screen.getByText("Contacts Found")).toBeInTheDocument();
    expect(screen.getByText("50.0")).toBeInTheDocument();
    expect(screen.getByText("NotebookLM")).toBeInTheDocument();
    expect(screen.getByText("Aguardando")).toBeInTheDocument();
  });
});
