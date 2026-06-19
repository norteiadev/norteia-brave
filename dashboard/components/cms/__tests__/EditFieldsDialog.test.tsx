import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { EditFieldsDialog } from "@/components/cms/EditFieldsDialog";
import { destinoKeys, editDestino } from "@/lib/destinos-api";
import { server } from "@/mocks/server";

import { renderWithClient } from "./test-utils";

const EDIT_URL = "http://localhost:3000/api/api/v1/destinos/:id/edit";

const NORMALIZED = {
  name: "Pelourinho",
  municipality: "Salvador",
  origem_value: 100, // derived §7.6 input — must NOT render as editable
  phone_e164: "+5571999999999", // PII — must NOT render
};

function renderDialog() {
  return renderWithClient(
    <EditFieldsDialog
      normalized={NORMALIZED}
      editFn={(fields) => editDestino("11111111-1111-1111-1111-111111111111", fields)}
      invalidateKey={destinoKeys.all}
    />,
  );
}

beforeEach(() => {
  server.resetHandlers();
});

describe("EditFieldsDialog", () => {
  it("submits only changed fields to PATCH /edit and closes on success", async () => {
    const user = userEvent.setup();
    let captured: unknown = null;
    server.use(
      http.patch(EDIT_URL, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ status: "ok" });
      }),
    );

    renderDialog();

    await user.click(screen.getByRole("button", { name: "Editar campos" }));

    // Edit only `name`; leave `municipality` untouched.
    const nameInput = await screen.findByLabelText("name");
    await user.clear(nameInput);
    await user.type(nameInput, "Pelourinho Histórico");

    await user.click(screen.getByRole("button", { name: "Salvar" }));

    await waitFor(() =>
      expect(captured).toEqual({ fields: { name: "Pelourinho Histórico" } }),
    );
    // Dialog closes on success.
    await waitFor(() =>
      expect(
        screen.queryByText("Editar campos canônicos"),
      ).not.toBeInTheDocument(),
    );
  });

  it("never renders PII or derived score fields as editable", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: "Editar campos" }));
    await screen.findByLabelText("name");

    expect(screen.getByLabelText("municipality")).toBeInTheDocument();
    expect(screen.queryByLabelText("phone_e164")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("origem_value")).not.toBeInTheDocument();
  });

  it("no-ops (closes without a request) when nothing changed", async () => {
    const user = userEvent.setup();
    let calls = 0;
    server.use(
      http.patch(EDIT_URL, () => {
        calls += 1;
        return HttpResponse.json({ status: "ok" });
      }),
    );

    renderDialog();
    await user.click(screen.getByRole("button", { name: "Editar campos" }));
    await screen.findByLabelText("name");
    await user.click(screen.getByRole("button", { name: "Salvar" }));

    await waitFor(() =>
      expect(
        screen.queryByText("Editar campos canônicos"),
      ).not.toBeInTheDocument(),
    );
    expect(calls).toBe(0);
  });
});
