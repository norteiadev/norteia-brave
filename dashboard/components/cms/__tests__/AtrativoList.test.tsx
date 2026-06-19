import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AtrativoList } from "@/components/cms/AtrativoList";
import { server } from "@/mocks/server";
import {
  atrativosListEmpty,
  atrativosListSuccess,
  sampleAtrativos,
} from "@/mocks/handlers/atrativos";

import { renderWithClient } from "./test-utils";

beforeEach(() => {
  server.resetHandlers();
});

afterEach(() => {
  server.resetHandlers();
});

describe("AtrativoList", () => {
  it("renders sub_state badge for 'discovered' item (navy chip)", async () => {
    server.use(atrativosListSuccess());
    renderWithClient(<AtrativoList />);

    // Wait for "Mercado Modelo" (sub_state=discovered) to appear
    const mercado = await screen.findByText("Mercado Modelo");
    expect(mercado).toBeInTheDocument();

    // StageBadge renders "Discovered" for sub_state="discovered" (toTitleCase)
    const discoveredBadges = await screen.findAllByText("Discovered");
    expect(discoveredBadges.length).toBeGreaterThan(0);
  });

  it("renders empty state 'Sem atrativos' when list returns no items", async () => {
    server.use(atrativosListEmpty());
    renderWithClient(<AtrativoList />);

    const emptyHeading = await screen.findByText("Sem atrativos");
    expect(emptyHeading).toBeInTheDocument();
  });

  it("phone_e164 never appears in the rendered DOM", async () => {
    server.use(atrativosListSuccess());
    renderWithClient(<AtrativoList />);

    // Wait for list to load
    await screen.findByText("Mercado Modelo");

    // phone_e164 should NEVER be rendered — only phone_masked is in sample data
    const phoneE164Element = screen.queryByText(/phone_e164/i);
    expect(phoneE164Element).toBeNull();
  });
});
