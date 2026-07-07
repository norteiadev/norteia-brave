import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { PainelRevisao } from "@/components/painel/PainelRevisao";
import {
  dlqListEmpty,
  dlqListSuccess,
  dlqValidateSuccess,
  sampleListItems,
} from "@/mocks/handlers/dlq";
import {
  gateApproveSuccess,
  gateListEmpty,
  gateListSuccess,
  sampleGateItems,
} from "@/mocks/handlers/gate";
import { server } from "@/mocks/server";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";

const requests: { method: string; url: string }[] = [];

beforeEach(() => {
  requests.length = 0;
  server.events.on("request:start", ({ request }) => {
    requests.push({ method: request.method, url: request.url });
  });
});

afterEach(() => {
  server.events.removeAllListeners();
});

describe("PainelRevisao", () => {
  it("renders the DLQ queue and the WhatsApp gate queue", async () => {
    server.use(dlqListSuccess(), gateListSuccess());

    const { findAllByTestId } = renderWithClient(<PainelRevisao />);

    const dlqRows = await findAllByTestId("revisao-dlq-row");
    expect(dlqRows).toHaveLength(sampleListItems.length);

    const gateRows = await findAllByTestId("revisao-gate-row");
    expect(gateRows).toHaveLength(sampleGateItems.length);

    // LGPD: gate rows surface the pre-masked phone, never a raw E.164.
    expect(gateRows[0]).toHaveTextContent("9••••");
  });

  it("clicking Aprovar fires the gate approve PATCH for that atrativo", async () => {
    server.use(dlqListSuccess(), gateListSuccess(), gateApproveSuccess());

    const { findAllByTestId } = renderWithClient(<PainelRevisao />);

    const buttons = await findAllByTestId("revisao-gate-aprovar");
    fireEvent.click(buttons[0]);

    await waitFor(() =>
      expect(
        requests.some(
          (r) =>
            r.method === "PATCH" &&
            r.url.includes("/api/api/v1/atrativos/gate/") &&
            r.url.includes("/approve"),
        ),
      ).toBe(true),
    );
  });

  it("clicking Validar fires the DLQ validate PATCH for that record", async () => {
    server.use(dlqListSuccess(), gateListSuccess(), dlqValidateSuccess());

    const { findAllByTestId } = renderWithClient(<PainelRevisao />);

    const buttons = await findAllByTestId("revisao-dlq-validar");
    fireEvent.click(buttons[0]);

    await waitFor(() =>
      expect(
        requests.some(
          (r) =>
            r.method === "PATCH" &&
            r.url.includes("/api/api/v1/dlq/") &&
            r.url.includes("/validate"),
        ),
      ).toBe(true),
    );
  });

  it("renders both empty states when the queues are empty", async () => {
    server.use(dlqListEmpty(), gateListEmpty());

    const { findByTestId, queryAllByTestId } = renderWithClient(
      <PainelRevisao />,
    );

    await findByTestId("revisao-dlq-empty");
    await findByTestId("revisao-gate-empty");
    expect(queryAllByTestId("revisao-dlq-row")).toHaveLength(0);
    expect(queryAllByTestId("revisao-gate-row")).toHaveLength(0);
  });
});
