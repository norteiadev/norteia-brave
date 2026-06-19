import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { DestinoList } from "@/components/cms/DestinoList";
import { server } from "@/mocks/server";
import {
  destinosListEmpty,
  destinosListError,
  destinosListSuccess,
  sampleDestinos,
} from "@/mocks/handlers/destinos";

import { renderWithClient } from "./test-utils";

beforeEach(() => {
  server.resetHandlers();
});

describe("DestinoList", () => {
  it("renders table rows with StageBadge — DLQ row shows amber badge, Mar row shows green badge", async () => {
    server.use(destinosListSuccess());
    renderWithClient(<DestinoList />);

    // Wait for the DLQ destino row (Pelourinho) to appear
    const pelourinho = await screen.findByText("Pelourinho");
    expect(pelourinho).toBeInTheDocument();

    // The DLQ row's routing badge should have "DLQ" text
    const dlqBadges = screen.getAllByText("DLQ");
    expect(dlqBadges.length).toBeGreaterThan(0);

    // The Mar row (Copacabana) should show "MAR" badge
    const copacabana = await screen.findByText("Copacabana");
    expect(copacabana).toBeInTheDocument();
    const marBadges = screen.getAllByText("MAR");
    expect(marBadges.length).toBeGreaterThan(0);
  });

  it("renders empty state 'Sem destinos' when list returns no items", async () => {
    server.use(destinosListEmpty());
    renderWithClient(<DestinoList />);

    const emptyHeading = await screen.findByText("Sem destinos");
    expect(emptyHeading).toBeInTheDocument();
  });

  it("renders 401 session-expired message when API returns 401", async () => {
    server.use(destinosListError(401));
    renderWithClient(<DestinoList />);

    const sessionExpired = await screen.findByText(
      "Sessão expirada ou token inválido",
    );
    expect(sessionExpired).toBeInTheDocument();
  });

  it("calls onSelect with the row id when a row is clicked", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    const onSelect = vi.fn();

    server.use(destinosListSuccess());
    renderWithClient(<DestinoList onSelect={onSelect} />);

    const pelourinho = await screen.findByText("Pelourinho");
    await user.click(pelourinho);

    expect(onSelect).toHaveBeenCalledWith(sampleDestinos[0].id);
  });
});
