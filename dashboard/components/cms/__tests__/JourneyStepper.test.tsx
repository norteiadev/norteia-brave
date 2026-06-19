import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  JourneyStepper,
  type AuditLogRow,
} from "@/components/cms/JourneyStepper";

/**
 * D-06 — JourneyStepper primitive (dedicated unit coverage).
 *
 * Behavioral surface per the component contract:
 *   - destination journey = 4 steps (Nascente → Rio/Score → DLQ → Mar)
 *   - attraction journey  = 7 steps (FSM: discovered → … → terminal)
 *   - per-step status (completed / current / pending) derived from
 *     routing + subState + auditLog rows
 *   - audit-row mapping surfaces actor + timestamp on completed steps
 *   - descarte is a terminal step on the atrativo path
 *
 * Step status is exposed via aria-label `${label}: ${status}`, which we read
 * to make assertions deterministic. Pure rendering — no network.
 */

/** Read the status word out of a step's aria-label `${label}: ${status}`. */
function stepStatus(label: string): string {
  // Non-compact renders TWO elements with this label (circle + nothing else);
  // the circle span carries the aria-label. Use the first match.
  const el = screen.getAllByLabelText(new RegExp(`^${label}: `))[0];
  const aria = el.getAttribute("aria-label") ?? "";
  return aria.split(": ")[1] ?? "";
}

describe("D-06 JourneyStepper — destino (4-step)", () => {
  it("renders exactly four labeled steps in pipeline order", () => {
    render(
      <JourneyStepper
        entityType="destination"
        routing="dlq"
        score={null}
        auditLog={[]}
      />,
    );
    expect(screen.getByText("Nascente")).toBeInTheDocument();
    expect(screen.getByText("Rio / Score")).toBeInTheDocument();
    expect(screen.getByText("DLQ")).toBeInTheDocument();
    expect(screen.getByText("Mar")).toBeInTheDocument();
    // 4 list items in the (non-compact) journey.
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
  });

  it("treats Nascente as always completed and Rio/Score as pending until scored", () => {
    render(
      <JourneyStepper
        entityType="destination"
        routing="dlq"
        score={null}
        auditLog={[]}
      />,
    );
    expect(stepStatus("Nascente")).toBe("completed");
    expect(stepStatus("Rio / Score")).toBe("pending");
  });

  it("marks Rio/Score completed and shows the score once populated", () => {
    render(
      <JourneyStepper
        entityType="destination"
        routing="dlq"
        score={88.5}
        auditLog={[]}
      />,
    );
    expect(stepStatus("Rio / Score")).toBe("completed");
    expect(screen.getByText("Score 88.5")).toBeInTheDocument();
  });

  it("marks DLQ as current when routing=dlq and no DLQ audit action exists", () => {
    render(
      <JourneyStepper
        entityType="destination"
        routing="dlq"
        score={88.5}
        auditLog={[]}
      />,
    );
    expect(stepStatus("DLQ")).toBe("current");
    expect(stepStatus("Mar")).toBe("pending");
  });

  it("marks DLQ completed and surfaces the audit row when a DLQ action is logged", () => {
    const auditLog: AuditLogRow[] = [
      {
        action: "dlq_validated",
        actor: "operador@norteia",
        after_state: null,
        created_at: "2026-06-10T14:30:00Z",
      },
    ];
    render(
      <JourneyStepper
        entityType="destination"
        routing="mar"
        score={90}
        auditLog={auditLog}
      />,
    );
    expect(stepStatus("DLQ")).toBe("completed");
    expect(stepStatus("Mar")).toBe("completed");
    // Audit actor must surface on the completed DLQ step.
    expect(screen.getByText(/operador@norteia/)).toBeInTheDocument();
  });
});

describe("D-06 JourneyStepper — atrativo (7-step)", () => {
  it("renders exactly seven labeled FSM steps", () => {
    render(
      <JourneyStepper
        entityType="attraction"
        routing="in_progress"
        subState="discovered"
        auditLog={[]}
      />,
    );
    for (const label of [
      "Descoberto",
      "Contatos",
      "Sinais",
      "Score",
      "Gate WhatsApp",
      "Outreach",
      "Mar / DLQ",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getAllByRole("listitem")).toHaveLength(7);
  });

  it("marks the current FSM step from sub_state and leaves later steps pending", () => {
    render(
      <JourneyStepper
        entityType="attraction"
        routing="in_progress"
        subState="signals_gathered"
        auditLog={[]}
      />,
    );
    // signals_gathered → index 2 (Sinais) is current.
    expect(stepStatus("Sinais")).toBe("current");
    expect(stepStatus("Outreach")).toBe("pending");
    expect(stepStatus("Mar / DLQ")).toBe("pending");
  });

  it("maps sub_state_advanced audit rows to completed FSM steps", () => {
    const auditLog: AuditLogRow[] = [
      {
        action: "atrativo_discovered",
        actor: null,
        after_state: null,
        created_at: "2026-06-01T09:00:00Z",
      },
      {
        action: "sub_state_advanced",
        actor: "sistema",
        after_state: { sub_state: "contacts_found" },
        created_at: "2026-06-02T09:00:00Z",
      },
    ];
    render(
      <JourneyStepper
        entityType="attraction"
        routing="in_progress"
        subState="signals_gathered"
        auditLog={auditLog}
      />,
    );
    expect(stepStatus("Descoberto")).toBe("completed");
    expect(stepStatus("Contatos")).toBe("completed");
    expect(stepStatus("Sinais")).toBe("current");
  });

  it("completes the terminal step when routing=mar (reached the sea)", () => {
    render(
      <JourneyStepper
        entityType="attraction"
        routing="mar"
        subState={null}
        auditLog={[]}
      />,
    );
    expect(stepStatus("Mar / DLQ")).toBe("completed");
  });

  it("treats descarte as a completed terminal step (rejected path)", () => {
    const auditLog: AuditLogRow[] = [
      {
        action: "hard_descarte",
        actor: "operador@norteia",
        after_state: null,
        created_at: "2026-06-03T11:00:00Z",
      },
    ];
    render(
      <JourneyStepper
        entityType="attraction"
        routing="descarte"
        subState={null}
        auditLog={auditLog}
      />,
    );
    // Terminal step is completed even though it is the rejected (descarte) outcome.
    expect(stepStatus("Mar / DLQ")).toBe("completed");
    expect(screen.getByText(/operador@norteia/)).toBeInTheDocument();
  });
});

describe("D-06 JourneyStepper — compact mode", () => {
  it("renders a compact circle bar (no descriptions) with one circle per step", () => {
    const { container } = render(
      <JourneyStepper
        entityType="destination"
        routing="mar"
        score={90}
        auditLog={[]}
        compact
      />,
    );
    // Compact omits the textual labels/descriptions.
    expect(screen.queryByText("Nascente")).not.toBeInTheDocument();
    // One <li> per step (4 for destino).
    const items = within(container).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    // Each circle carries an aria-label of the form "<label>: <status>".
    const circles = container.querySelectorAll('[aria-label*=": "]');
    expect(circles.length).toBe(4);
  });
});
