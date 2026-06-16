import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScoreBreakdownPanel } from "@/components/dlq/ScoreBreakdownPanel";

const BREAKDOWN = {
  origem: 90,
  completude: 60,
  corroboracao: 50,
  atualidade: 80,
  validacao_humana: 0,
};

describe("ScoreBreakdownPanel", () => {
  it("renders all five §7.6 criteria labels", () => {
    render(<ScoreBreakdownPanel breakdown={BREAKDOWN} score={72.4} />);
    expect(screen.getByText("origem")).toBeInTheDocument();
    expect(screen.getByText("completude")).toBeInTheDocument();
    expect(screen.getByText("corroboração")).toBeInTheDocument();
    expect(screen.getByText("atualidade")).toBeInTheDocument();
    expect(screen.getByText("validação-humana")).toBeInTheDocument();
  });

  it("renders the total score readout (Display) from the score prop", () => {
    render(<ScoreBreakdownPanel breakdown={BREAKDOWN} score={72.4} />);
    expect(screen.getByTestId("score-total")).toHaveTextContent("72.4");
  });

  it("renders five score bars (one per criterion)", () => {
    render(<ScoreBreakdownPanel breakdown={BREAKDOWN} score={72.4} />);
    expect(screen.getAllByRole("progressbar")).toHaveLength(5);
  });

  it("computes the total from weighted criteria when score is absent", () => {
    // 90*.3 + 60*.2 + 50*.2 + 80*.15 + 0*.15 = 27+12+10+12+0 = 61.0
    render(<ScoreBreakdownPanel breakdown={BREAKDOWN} />);
    expect(screen.getByTestId("score-total")).toHaveTextContent("61.0");
  });

  it("still renders all five rows on an empty/partial breakdown", () => {
    render(<ScoreBreakdownPanel breakdown={{}} score={0} />);
    expect(screen.getAllByRole("progressbar")).toHaveLength(5);
    expect(screen.getByText("origem")).toBeInTheDocument();
  });
});
