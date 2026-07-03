import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getOperatorToken } from "@/lib/api-client";

// Mock the App Router navigation hooks. `searchParams` is swapped per test to
// drive the expired/normal branches.
const push = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => searchParams,
}));

import LoginPage from "../page";

beforeEach(() => {
  push.mockReset();
  searchParams = new URLSearchParams();
  window.localStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("Login gate (DASH-06, UI-SPEC)", () => {
  it("renders the 'Entrar' primary CTA", () => {
    render(<LoginPage />);
    expect(
      screen.getByRole("button", { name: "Entrar" }),
    ).toBeInTheDocument();
  });

  it("shows the UI-SPEC 401 copy when redirected with reason=expired", () => {
    searchParams = new URLSearchParams("reason=expired");
    render(<LoginPage />);
    expect(
      screen.getByText("Sessão expirada ou token inválido"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Faça login novamente para continuar."),
    ).toBeInTheDocument();
  });

  it("does NOT show the 401 copy on a normal load", () => {
    render(<LoginPage />);
    expect(
      screen.queryByText("Sessão expirada ou token inválido"),
    ).not.toBeInTheDocument();
  });

  it("persists the operator token and navigates to the painel on submit", async () => {
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(
      screen.getByPlaceholderText("Bearer token"),
      "my-operator-token",
    );
    await user.click(screen.getByRole("button", { name: "Entrar" }));

    expect(getOperatorToken()).toBe("my-operator-token");
    expect(push).toHaveBeenCalledWith("/painel");
  });
});
