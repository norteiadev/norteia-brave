import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import {
  CurrencyProvider,
  CurrencySwitch,
  useCurrency,
} from "@/components/painel/currency";

// Intl pt-BR puts a NBSP between symbol and amount; normalize for matching.
const text = (el: HTMLElement) => el.textContent?.replace(/ /g, " ");

function Costs() {
  const { format } = useCurrency();
  return (
    <ul>
      <li data-testid="today">{format(29.44)}</li>
      <li data-testid="cap">{format(50, { digits: 0 })}</li>
    </ul>
  );
}

function Screen() {
  return (
    <CurrencyProvider>
      <CurrencySwitch />
      <Costs />
    </CurrencyProvider>
  );
}

beforeEach(() => window.localStorage.clear());

describe("currency switch", () => {
  it("re-renders every money value when switching USD <-> BRL, and remembers the choice", async () => {
    const user = userEvent.setup();
    const first = render(<Screen />);

    expect(text(screen.getByTestId("today"))).toBe("US$ 29,44");
    expect(text(screen.getByTestId("cap"))).toBe("US$ 50");
    expect(screen.getByRole("button", { name: "Dólar" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // The fixed rate is shown with its date.
    expect(screen.getByRole("group", { name: "Moeda" })).toHaveAttribute(
      "title",
      expect.stringMatching(/R\$\s5,42.*\d{2}\/\d{2}\/\d{4}/),
    );

    await user.click(screen.getByRole("button", { name: "Real" }));

    expect(text(screen.getByTestId("today"))).toBe("R$ 159,56");
    expect(text(screen.getByTestId("cap"))).toBe("R$ 271");
    // In reais the fixed rate and its date are on screen, not only in a tooltip.
    expect(screen.getByTestId("currency-fx")).toBeVisible();
    expect(text(screen.getByTestId("currency-fx"))).toBe("câmbio fixo R$ 5,42 · 26/10/2026");
    expect(screen.getByRole("button", { name: "Real" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    first.unmount();
    render(<Screen />);
    expect(text(screen.getByTestId("today"))).toBe("R$ 159,56");

    await user.click(screen.getByRole("button", { name: "Dólar" }));
    expect(text(screen.getByTestId("today"))).toBe("US$ 29,44");
  });
});
