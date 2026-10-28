import { act, fireEvent, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { Toaster } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithClient } from "@/components/cms/__tests__/test-utils";
import type { RegistrosTab } from "@/components/painel/nav";
import { PainelRegistros } from "@/components/painel/PainelRegistros";
import { PainelShell } from "@/components/painel/PainelShell";
import type { AtrativoDetail, AtrativoListItem } from "@/lib/atrativos-api";
import {
  atrativoDetailSuccess,
  atrativosByRouting,
  failureCardLogSuccess,
  failureCardsSuccess,
  sampleFailureCards,
  promoteBulkDryRunSuccess,
  promoteBulkRunSuccess,
  promoteBulkZeroCandidates,
} from "@/mocks/handlers/atrativos";
import { configGetSuccess } from "@/mocks/handlers/config";
import { engineModeSuccess, engineStatus, marRepushSuccess } from "@/mocks/handlers/engine";
import { server } from "@/mocks/server";

const API = "http://localhost:3000/api/api/v1";
const LOCKED_COPY = "Motor ligado — pause o motor para promover, descartar ou editar registros.";
const RAW_PHONE = "+55 71 99999-0000";
const RAW_CONTACT_PHONE = "+5571988887777";
const RAW_EMAIL = "contato@mercado.example";

function row(over: Partial<AtrativoListItem>): AtrativoListItem {
  return {
    id: "x",
    entity_type: "attraction",
    uf: "PE",
    routing: "dlq",
    sub_state: null,
    score: 70,
    name: "x",
    source: "tripadvisor",
    municipio: "Recife",
    validation_pending: false,
    mar_id: null,
    parent_mar_id: null,
    contacts_summary: null,
    ...over,
  };
}

const MERCADO = row({
  id: "rio-1",
  uf: "BA",
  municipio: "Salvador",
  name: "Mercado Modelo",
  score: 61,
  description_pending: true,
  temporarily_closed: true,
  contacts_summary: { phone_masked: "**1234", website: null },
});
const DLQ_ROWS = [
  MERCADO,
  ...Array.from({ length: 29 }, (_, i) =>
    row({ id: `dlq-${i + 2}`, name: `Atrativo ${i + 2}` }),
  ),
];
const MAR_ROWS = [
  row({ id: "mar-1", routing: "mar", name: "Farol da Barra", score: 92 }),
  row({ id: "mar-2", routing: "mar", name: "Pelourinho", score: 88 }),
];

const DETAIL: AtrativoDetail = {
  ...MERCADO,
  score_breakdown: {
    origem: 16,
    completude: 15,
    corroboracao: 10,
    atualidade: 20,
    validacao_humana: 0,
  },
  normalized: {
    name: "Mercado Modelo",
    phone: RAW_PHONE,
    contacts: { phone_e164: RAW_CONTACT_PHONE, email: RAW_EMAIL },
    address: "Praça Visconde de Cayru, 250",
    weekday_text: ["segunda-feira: 09:00–18:00", "terça-feira: 09:00–18:00"],
    website: "https://mercadomodelo.ba.gov.br",
    most_recent_review_at: "2026-01-10T15:00:00Z",
    review_count: 1234,
    rating: 4.5,
  },
  audit_log: [],
  parent_destino: null,
  events: [
    { stage: "scored", status: "ok", message: null, data: null, created_at: "2026-06-10T09:00:05Z" },
    { stage: "routed", status: "ok", message: "no_recent_reviews", data: null, created_at: "2026-06-10T09:00:06Z" },
  ],
  dlq_reason: "no_recent_reviews",
  source: "tripadvisor",
  processed_at: "2026-06-10T09:00:06Z",
  score_version: "v1",
};

/** Every request that left the app, as "METHOD path?query" + its JSON body. */
let sent: { key: string; body: unknown }[] = [];
const count = (prefix: string) => sent.filter((r) => r.key.startsWith(prefix)).length;
const bodies = (prefix: string) =>
  sent.filter((r) => r.key.startsWith(prefix)).map((r) => r.body);

beforeEach(() => {
  sent = [];
  server.events.on("request:start", async ({ request }) => {
    const url = new URL(request.url);
    const body =
      request.method === "GET" ? null : await request.clone().json().catch(() => null);
    sent.push({ key: `${request.method} ${url.pathname.replace("/api/api/v1", "")}${url.search}`, body });
  });
  server.use(
    engineStatus(),
    configGetSuccess(),
    atrativosByRouting([...DLQ_ROWS, ...MAR_ROWS]),
    failureCardsSuccess(),
    atrativoDetailSuccess(DETAIL),
    failureCardLogSuccess(),
    engineModeSuccess(),
  );
});

afterEach(() => {
  server.events.removeAllListeners();
  vi.useRealTimers();
});

// The page owns tab, UF and the open record (they live in the URL); same here.
function Harness({ initial = "revisao", id = null }: { initial?: RegistrosTab; id?: string | null }) {
  const [tab, setTab] = useState<RegistrosTab>(initial);
  const [uf, setUf] = useState("");
  const [selected, setSelected] = useState<string | null>(id);
  return (
    <PainelRegistros
      tab={tab}
      uf={uf}
      selected={selected}
      onTabChange={(t) => {
        setTab(t);
        setSelected(null);
      }}
      onUfChange={(u) => {
        setUf(u);
        setSelected(null);
      }}
      onSelect={setSelected}
    />
  );
}

function mount(initial?: RegistrosTab, id?: string) {
  return renderWithClient(
    <>
      <Toaster />
      <Harness initial={initial} id={id} />
    </>,
  );
}

async function openMercado(ui: ReturnType<typeof mount>) {
  fireEvent.click(await ui.findByText("Mercado Modelo"));
  return within(ui.getByRole("complementary", { name: "Detalhe do registro" }));
}

/** Switch to fake timers only around the undo window (Query/MSW need real ones to load). */
async function expireWindow() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000);
    // Let the request settle on real I/O, then flush the (fake) timers its
    // outcome scheduled, e.g. the error toast, before going back to real timers.
    for (let i = 0; i < 5; i++) {
      await new Promise((r) => setImmediate(r));
      await vi.advanceTimersByTimeAsync(50);
    }
  });
  vi.useRealTimers();
}
/** Click the toast's "Desfazer" (the toast mounts on the next timer tick). */
async function undo(ui: ReturnType<typeof mount>) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100);
  });
  fireEvent.click(ui.getByRole("button", { name: "Desfazer" }));
}
function fakeTimers() {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
}

describe("Registros — tabs and list", () => {
  it("Revisão lists the DLQ from the current endpoint, with every tab's total", async () => {
    const ui = mount();

    expect(await ui.findByText("Mercado Modelo")).toBeInTheDocument();
    expect(ui.getByRole("tab", { name: /Revisão/ })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(ui.getByTestId("registros-tab-count-revisao")).toHaveTextContent("30"));
    expect(ui.getByTestId("registros-tab-count-mar")).toHaveTextContent("2");
    expect(ui.getByTestId("registros-tab-count-falha")).toHaveTextContent("1");

    expect(ui.getAllByTestId("registros-row")).toHaveLength(25);
    expect(ui.getByText("Mostrando 25 de 30")).toBeInTheDocument();
    expect(count("GET /atrativos?routing=dlq&limit=25")).toBeGreaterThan(0);

    const first = within(ui.getAllByTestId("registros-row")[0]);
    expect(first.getByText("Salvador · BA")).toBeInTheDocument();
    expect(first.getByText("Sem descrição")).toBeInTheDocument();
    expect(first.getByText("Fechado temporariamente")).toBeInTheDocument();
    expect(first.getByText("61")).toBeInTheDocument();
    // The dedup endpoint pairs only one page of candidates: no partial
    // "Possível duplicado" flag until Phase 2 (its absence would read as "not one").
    expect(ui.queryByText("Possível duplicado")).toBeNull();
    expect(count("GET /dedup")).toBe(0);
  });

  it("'Mostrar mais' grows the page by what is left and shows N de TOTAL", async () => {
    const ui = mount();
    fireEvent.click(await ui.findByRole("button", { name: "Mostrar mais 5" }));

    await waitFor(() => expect(ui.getAllByTestId("registros-row")).toHaveLength(30));
    expect(count("GET /atrativos?routing=dlq&limit=75")).toBe(1);
    expect(ui.getByText("Mostrando 30 de 30")).toBeInTheDocument();
    expect(ui.queryByRole("button", { name: /Mostrar mais/ })).toBeNull();
  });

  it("a failed load says so with a retry, instead of an empty queue", async () => {
    server.use(http.get(`${API}/atrativos`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const ui = mount();

    expect(await ui.findByRole("alert")).toHaveTextContent("Não foi possível carregar os registros.");
    expect(ui.queryByText("Nenhum registro nesta etapa.")).toBeNull();

    server.use(atrativosByRouting([...DLQ_ROWS, ...MAR_ROWS]));
    fireEvent.click(ui.getByRole("button", { name: "Tentar de novo" }));
    expect(await ui.findByText("Mercado Modelo")).toBeInTheDocument();
  });

  it("filters by UF on the server", async () => {
    const ui = mount();
    await ui.findByText("Mercado Modelo");
    fireEvent.change(ui.getByLabelText("UF"), { target: { value: "BA" } });

    await waitFor(() => expect(ui.getAllByTestId("registros-row")).toHaveLength(1));
    expect(count("GET /atrativos?uf=BA&routing=dlq&limit=25")).toBe(1);
    expect(ui.getByText("Mostrando 1 de 1")).toBeInTheDocument();
  });

  it("Mar lists the Mar rows and re-sends the pending pushes", async () => {
    server.use(engineStatus({ norteia_api: { up: true, pending: 4, rejected: 3 } }), marRepushSuccess(4));
    const ui = mount("mar");

    expect(await ui.findByText("Farol da Barra")).toBeInTheDocument();
    expect(ui.getAllByTestId("registros-row")).toHaveLength(2);
    expect(await ui.findByTestId("api-sync-label")).toHaveTextContent(
      "norteia-api online · 4 pendentes de envio",
    );
    expect(ui.getByTestId("api-sync-rejected")).toHaveTextContent("Pushes rejeitados: 3");

    fireEvent.click(ui.getByRole("button", { name: "Reenviar pendentes" }));
    expect(await ui.findByText("4 reenviados para a norteia-api.")).toBeInTheDocument();
    expect(count("POST /mar/repush")).toBe(1);
  });

  it("Mar hides 'Reenviar pendentes' when nothing is pending, and explains a down API", async () => {
    server.use(engineStatus({ norteia_api: { up: false, reason: "ingest:401", pending: 0 } }));
    const ui = mount("mar");

    expect(await ui.findByTestId("api-sync-label")).toHaveTextContent(
      "norteia-api recusou o token de ingestão (expirado?)",
    );
    expect(ui.queryByRole("button", { name: "Reenviar pendentes" })).toBeNull();
  });

  it("Mar disables 'Reenviar pendentes' while the API is down, and hides the sync when externals are off", async () => {
    server.use(engineStatus({ norteia_api: { up: false, pending: 1 } }));
    const down = mount("mar");
    expect(await down.findByTestId("api-sync-label")).toHaveTextContent(
      "norteia-api fora do ar · 1 pendente de envio",
    );
    expect(down.getByRole("button", { name: "Reenviar pendentes" })).toBeDisabled();
    down.unmount();

    server.use(engineStatus({ norteia_api: { up: null, pending: 9 } }));
    const off = mount("mar");
    await off.findByText("Farol da Barra");
    expect(off.queryByTestId("api-sync-label")).toBeNull();
  });

  it("Falha leaves destino quarantines out of the list and its count", async () => {
    server.use(
      failureCardsSuccess([
        ...sampleFailureCards,
        { ...sampleFailureCards[0], source_ref: "tripadvisor:destination:7", name: "Destino X", entity_type: "destination" },
      ]),
    );
    const ui = mount("falha");

    expect(await ui.findByText("Praia Do Bosque")).toBeInTheDocument();
    expect(ui.queryByText("Destino X")).toBeNull();
    expect(ui.getByTestId("registros-tab-count-falha")).toHaveTextContent("1");
  });

  it("Falha lists the failure cards and opens the record's log", async () => {
    const ui = mount("falha");

    fireEvent.click(await ui.findByText("Praia Do Bosque"));
    const detail = within(ui.getByRole("complementary", { name: "Detalhe do registro" }));

    expect(await detail.findByText("Quarentena (falha)")).toBeInTheDocument();
    expect(detail.getAllByText("ibge_unmatched: 'Praia Do Bosque'").length).toBeGreaterThan(0);
    expect(count("GET /failures/cards/log?source_ref=tripadvisor%3Aattraction%3A99999")).toBe(1);
    expect(detail.queryByRole("button", { name: /Promover/ })).toBeNull();
  });

  it("switching tabs calls onTabChange", async () => {
    const ui = mount();
    await ui.findByText("Mercado Modelo");
    fireEvent.click(ui.getByRole("tab", { name: /Mar/ }));

    expect(await ui.findByText("Farol da Barra")).toBeInTheDocument();
    expect(ui.getByRole("tab", { name: /Mar/ })).toHaveAttribute("aria-selected", "true");
  });

  it("a new tab never shows the previous tab's rows while it loads", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.get(`${API}/atrativos`, async ({ request }) => {
        const sp = new URL(request.url).searchParams;
        const routing = sp.get("routing");
        const rows = [...DLQ_ROWS, ...MAR_ROWS].filter((r) => r.routing === routing);
        if (routing === "mar" && sp.get("limit") !== "1") await gate;
        return HttpResponse.json({ items: rows.slice(0, Number(sp.get("limit"))), total: rows.length, offset: 0, limit: 25 });
      }),
    );
    const ui = mount();
    await ui.findByText("Mercado Modelo");
    fireEvent.click(ui.getByRole("tab", { name: /Mar/ }));

    expect(await ui.findByText("Carregando…")).toBeInTheDocument();
    expect(ui.queryByText("Mercado Modelo")).toBeNull();
    expect(ui.queryByText(/Mostrando/)).toBeNull();

    release();
    expect(await ui.findByText("Farol da Barra")).toBeInTheDocument();
  });
});

describe("Registros — detail", () => {
  it("shows motivo + next step, the weighted breakdown, the fields and the timeline", async () => {
    const ui = mount();
    const detail = await openMercado(ui);

    const motivo = within(await detail.findByTestId("registros-motivo"));
    expect(motivo.getByText("Sem review recente")).toBeInTheDocument();
    expect(motivo.getByText(/Próximo passo/)).toBeInTheDocument();
    expect(motivo.getByText("dlq_reason = no_recent_reviews")).toBeInTheDocument();

    const bd = within(detail.getByTestId("registros-breakdown"));
    expect(bd.getByTestId("registros-bd-origem")).toHaveTextContent("Origem");
    expect(bd.getByTestId("registros-bd-origem")).toHaveTextContent("peso 20");
    expect(bd.getByTestId("registros-bd-origem")).toHaveTextContent("16,0");
    expect(bd.getByTestId("registros-bd-total")).toHaveTextContent("61,0");
    // 61 + 20 (validação humana) = 81 < limiar 85.
    expect(bd.getByTestId("registros-bd-total")).toHaveTextContent("faltam 4,0");

    expect(detail.getByTestId("registros-field-address")).toHaveTextContent("Praça Visconde de Cayru, 250");
    expect(detail.getByTestId("registros-field-hours")).toHaveTextContent("segunda-feira: 09:00–18:00");
    expect(detail.getByTestId("registros-field-site")).toHaveTextContent("https://mercadomodelo.ba.gov.br");
    expect(detail.getByTestId("registros-field-phone")).toHaveTextContent("**1234");
    expect(detail.getByTestId("registros-field-reviews")).toHaveTextContent("1.234");
    expect(detail.getByTestId("registros-field-rating")).toHaveTextContent("4,5");
    expect(detail.getByTestId("registros-field-last-review")).toHaveTextContent("10/01/2026");
    expect(detail.getByTestId("registros-field-description")).toHaveAttribute("data-missing", "true");
    expect(detail.getByTestId("registros-field-address")).not.toHaveAttribute("data-missing");

    // LGPD: the raw normalized phone and contacts never reach the screen.
    expect(ui.queryByText(RAW_PHONE, { exact: false })).toBeNull();
    for (const raw of [RAW_PHONE, RAW_CONTACT_PHONE, RAW_EMAIL]) {
      expect(document.body.textContent).not.toContain(raw);
    }

    const tl = within(detail.getByRole("list", { name: "Linha do tempo" }));
    expect(tl.getByText("Pontuado (confiabilidade)")).toBeInTheDocument();
    expect(tl.getByText("Roteado")).toBeInTheDocument();
  });

  it("a record without a masked contact phone is not marked as missing a phone", async () => {
    const ui = mount();
    fireEvent.click(await ui.findByText("Atrativo 2"));
    const detail = within(ui.getByRole("complementary", { name: "Detalhe do registro" }));

    const phone = await detail.findByTestId("registros-field-phone");
    expect(phone).not.toHaveAttribute("data-missing");
    expect(phone).toHaveTextContent("não exibido (mascaramento pendente)");
    expect(document.body.textContent).not.toContain(RAW_PHONE);
  });

  it("explains the score-gate free text with the score embedded", async () => {
    server.use(
      atrativoDetailSuccess({ ...DETAIL, dlq_reason: "score=61.00 below threshold_mar=85.0" }),
    );
    const ui = mount();
    const detail = await openMercado(ui);

    const motivo = within(await detail.findByTestId("registros-motivo"));
    expect(motivo.getByText("Score abaixo do limiar")).toBeInTheDocument();
    expect(motivo.getByText(/61,0/)).toBeInTheDocument();
  });

  it("shows an unknown motivo as its raw code", async () => {
    server.use(atrativoDetailSuccess({ ...DETAIL, dlq_reason: "algo_novo" }));
    const ui = mount();
    const detail = await openMercado(ui);

    expect(await detail.findByText("dlq_reason = algo_novo")).toBeInTheDocument();
  });
});

describe("Registros — linked record", () => {
  it("opens a linked record that is past the loaded page", async () => {
    const far = { ...DETAIL, id: "dlq-30", name: "Atrativo 30" };
    server.use(http.get(`${API}/atrativos/:id`, () => HttpResponse.json(far)));
    const ui = mount("revisao", "dlq-30");

    const aside = within(ui.getByRole("complementary", { name: "Detalhe do registro" }));
    expect(await aside.findByRole("heading", { name: "Atrativo 30" })).toBeInTheDocument();
    expect(ui.getAllByTestId("registros-row")).toHaveLength(25);
  });

  it("an unknown id shows the list with a short note, not an empty panel", async () => {
    server.use(
      http.get(`${API}/atrativos/:id`, () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
    );
    const ui = mount("revisao", "sumiu");

    const aside = within(ui.getByRole("complementary", { name: "Detalhe do registro" }));
    expect(await aside.findByText("Registro não encontrado nesta etapa.")).toBeInTheDocument();
    expect(ui.getAllByTestId("registros-row")).toHaveLength(25);
  });

  it("a record that left this stage counts as not found here", async () => {
    server.use(
      http.get(`${API}/atrativos/:id`, () =>
        HttpResponse.json({ ...DETAIL, id: "promovido", routing: "mar" }),
      ),
    );
    const ui = mount("revisao", "promovido");

    const aside = within(ui.getByRole("complementary", { name: "Detalhe do registro" }));
    expect(await aside.findByText("Registro não encontrado nesta etapa.")).toBeInTheDocument();
  });

  it("an unknown failure card shows the note too", async () => {
    const ui = mount("falha", "tripadvisor:attraction:nope");

    const aside = within(ui.getByRole("complementary", { name: "Detalhe do registro" }));
    expect(await aside.findByText("Registro não encontrado nesta etapa.")).toBeInTheDocument();
  });
});

describe("Registros — single actions with undo", () => {
  it("Promover sends nothing when undone", async () => {
    const ui = mount();
    const detail = await openMercado(ui);
    await detail.findByTestId("registros-motivo");

    fakeTimers();
    fireEvent.click(detail.getByRole("button", { name: "Promover ao Mar" }));
    await undo(ui);
    await expireWindow();

    expect(count("PATCH /atrativos/rio-1/transition")).toBe(0);
  });

  it("Promover sends exactly one transition dlq → mar when the window expires", async () => {
    const ui = mount();
    const detail = await openMercado(ui);
    await detail.findByTestId("registros-motivo");
    server.use(http.patch(`${API}/atrativos/:id/transition`, () => HttpResponse.json({ status: "ok" })));

    fakeTimers();
    fireEvent.click(detail.getByRole("button", { name: "Promover ao Mar" }));
    await expireWindow();

    await waitFor(() => expect(count("PATCH /atrativos/rio-1/transition")).toBe(1));
    expect(bodies("PATCH /atrativos/rio-1/transition")).toEqual([{ to: "mar", expected: "dlq" }]);
  });

  it("one pending action per record: a second click inside the window sends nothing more", async () => {
    const ui = mount();
    const detail = await openMercado(ui);
    await detail.findByTestId("registros-motivo");
    server.use(http.patch(`${API}/atrativos/:id/transition`, () => HttpResponse.json({ status: "ok" })));

    fakeTimers();
    fireEvent.click(detail.getByRole("button", { name: "Promover ao Mar" }));
    for (const name of ["Promover ao Mar", "Editar", "Descartar"]) {
      expect(detail.getByRole("button", { name })).toBeDisabled();
    }
    expect(detail.getByTestId("registros-pending")).toHaveTextContent("Desfazer");
    fireEvent.click(detail.getByRole("button", { name: "Promover ao Mar" }));
    fireEvent.click(detail.getByRole("button", { name: "Descartar" }));
    await expireWindow();

    await waitFor(() => expect(count("PATCH /atrativos/rio-1/transition")).toBe(1));
    expect(bodies("PATCH /atrativos/rio-1/transition")).toEqual([{ to: "mar", expected: "dlq" }]);
  });

  it("Descartar sends exactly one transition dlq → descarte when the window expires", async () => {
    const ui = mount();
    const detail = await openMercado(ui);
    await detail.findByTestId("registros-motivo");
    server.use(http.patch(`${API}/atrativos/:id/transition`, () => HttpResponse.json({ status: "ok" })));

    fakeTimers();
    fireEvent.click(detail.getByRole("button", { name: "Descartar" }));
    await expireWindow();

    await waitFor(() => expect(count("PATCH /atrativos/rio-1/transition")).toBe(1));
    expect(bodies("PATCH /atrativos/rio-1/transition")).toEqual([{ to: "descarte", expected: "dlq" }]);
  });

  it("Editar sends only the changed canonical fields, once, and nothing when undone", async () => {
    const ui = mount();
    const detail = await openMercado(ui);
    await detail.findByTestId("registros-motivo");
    server.use(http.patch(`${API}/atrativos/:id/edit`, () => HttpResponse.json({ status: "ok" })));

    fireEvent.click(detail.getByRole("button", { name: "Editar" }));
    // The form never offers the phone.
    expect(detail.queryByLabelText(/Telefone/)).toBeNull();
    expect(detail.getByLabelText("Endereço")).toHaveValue("Praça Visconde de Cayru, 250");
    fireEvent.change(detail.getByLabelText("Descrição editorial"), {
      target: { value: "Casarão de artesanato baiano." },
    });

    fakeTimers();
    fireEvent.click(detail.getByRole("button", { name: "Salvar" }));
    await undo(ui);
    await expireWindow();
    expect(count("PATCH /atrativos/rio-1/edit")).toBe(0);

    fireEvent.click(detail.getByRole("button", { name: "Editar" }));
    fireEvent.change(detail.getByLabelText("Descrição editorial"), {
      target: { value: "Casarão de artesanato baiano." },
    });
    fakeTimers();
    fireEvent.click(detail.getByRole("button", { name: "Salvar" }));
    await expireWindow();

    await waitFor(() => expect(count("PATCH /atrativos/rio-1/edit")).toBe(1));
    expect(bodies("PATCH /atrativos/rio-1/edit")).toEqual([
      { fields: { descricao_editorial: "Casarão de artesanato baiano." } },
    ]);
  });

  it("explains a 423 from the server in a toast", async () => {
    const ui = mount();
    const detail = await openMercado(ui);
    await detail.findByTestId("registros-motivo");
    server.use(
      http.patch(`${API}/atrativos/:id/transition`, () =>
        HttpResponse.json({ detail: "locked" }, { status: 423 }),
      ),
    );

    fakeTimers();
    fireEvent.click(detail.getByRole("button", { name: "Promover ao Mar" }));
    await expireWindow();

    expect(await ui.findByText(LOCKED_COPY)).toBeInTheDocument();
  });

  it("explains a 409 (record held by the reliability gate) with the server's reason", async () => {
    const held = "registro não cruzou o gate de confiabilidade e permanece no Rio (motivo: no_recent_reviews)";
    const ui = mount();
    const detail = await openMercado(ui);
    await detail.findByTestId("registros-motivo");
    server.use(
      http.patch(`${API}/atrativos/:id/transition`, () =>
        HttpResponse.json({ detail: held }, { status: 409 }),
      ),
    );

    fakeTimers();
    fireEvent.click(detail.getByRole("button", { name: "Promover ao Mar" }));
    await expireWindow();

    expect(await ui.findByText(held)).toBeInTheDocument();
  });

  it("locks every action while the motor is LIGADO, with a 'Pausar motor' shortcut", async () => {
    server.use(engineStatus({ mode: "LIGADO", editing_unlocked: false }));
    const ui = mount();
    const detail = await openMercado(ui);
    await detail.findByTestId("registros-motivo");

    const lock = await ui.findByRole("status");
    expect(lock).toHaveTextContent("Motor ligado.");
    for (const name of ["Promover ao Mar", "Editar", "Descartar"]) {
      expect(detail.getByRole("button", { name })).toBeDisabled();
    }
    expect(ui.getByTestId("promover-lote-btn")).toBeDisabled();

    fireEvent.click(within(lock).getByRole("button", { name: "Pausar motor" }));
    await waitFor(() => expect(bodies("POST /engine/mode")).toEqual([{ mode: "PAUSADO" }]));
  });

  it("Mar detail is read-only", async () => {
    const ui = mount("mar");
    fireEvent.click(await ui.findByText("Farol da Barra"));
    const detail = within(ui.getByRole("complementary", { name: "Detalhe do registro" }));

    await detail.findByTestId("registros-breakdown");
    expect(detail.queryByRole("button", { name: "Promover ao Mar" })).toBeNull();
    expect(detail.queryByRole("button", { name: "Editar" })).toBeNull();
    expect(detail.queryByRole("button", { name: "Descartar" })).toBeNull();
  });
});

describe("Registros — keyboard", () => {
  it("J/K move the active row and open it, Esc closes the detail", async () => {
    const ui = mount();
    await ui.findByText("Mercado Modelo");
    const rows = () => ui.getAllByTestId("registros-row");
    const aside = ui.getByRole("complementary", { name: "Detalhe do registro" });

    fireEvent.keyDown(document.body, { key: "j" });
    expect(rows()[0]).toHaveAttribute("aria-selected", "true");
    expect(within(aside).getByRole("heading", { name: "Mercado Modelo" })).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: "j" });
    expect(rows()[1]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(document.body, { key: "k" });
    expect(rows()[0]).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(rows()[0]).toHaveAttribute("aria-selected", "false");
    expect(within(aside).getByText(/Escolha um registro/)).toBeInTheDocument();
  });

  it("leaves J/K and Esc to an open dialog", async () => {
    server.use(promoteBulkDryRunSuccess());
    const ui = mount();
    const detail = await openMercado(ui);
    fireEvent.click(ui.getByTestId("promover-lote-btn"));
    const dialog = await ui.findByTestId("promover-lote-dialog");

    fireEvent.keyDown(dialog, { key: "j" });
    expect(ui.getAllByTestId("registros-row")[1]).toHaveAttribute("aria-selected", "false");

    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(ui.queryByTestId("promover-lote-dialog")).toBeNull());
    // Esc closed the dialog only, not the record behind it.
    expect(detail.getByRole("heading", { name: "Mercado Modelo" })).toBeInTheDocument();
  });

  it("ignores J/K while typing in a field", async () => {
    const ui = mount();
    await ui.findByText("Mercado Modelo");

    fireEvent.keyDown(ui.getByLabelText("UF"), { key: "j" });
    expect(ui.getAllByTestId("registros-row")[0]).toHaveAttribute("aria-selected", "false");
  });
});

describe("Registros — Promover em lote", () => {
  it("dry-run → confirm → real run, scoped to the UF filter", async () => {
    server.use(promoteBulkDryRunSuccess());
    const ui = mount();
    await ui.findByText("Mercado Modelo");
    fireEvent.change(ui.getByLabelText("UF"), { target: { value: "BA" } });

    fireEvent.click(ui.getByTestId("promover-lote-btn"));
    const dialog = await ui.findByTestId("promover-lote-dialog");
    expect(dialog).toHaveTextContent("Promover 12 atrativos");
    expect(dialog).toHaveTextContent("12 elegíveis em BA");
    expect(dialog).toHaveTextContent("3 por score");
    expect(dialog).toHaveTextContent("2 sem descrição");
    expect(dialog).toHaveTextContent("1 sem review recente");

    server.use(promoteBulkRunSuccess({ promoted: 10, remaining: 2 }));
    fireEvent.click(ui.getByTestId("promover-lote-confirm"));

    expect(
      await ui.findByText("10 promovidos, 0 retidos, 0 falharam. Restam 2."),
    ).toBeInTheDocument();
    expect(bodies("POST /atrativos/promote-bulk")).toEqual([
      { uf: "BA", dry_run: true },
      { uf: "BA", dry_run: false },
    ]);
  });

  it("the confirm dialog follows the dark theme outside the shell subtree", async () => {
    window.localStorage.setItem("brave.painelTheme", "dark");
    server.use(promoteBulkDryRunSuccess(), atrativosByRouting([...DLQ_ROWS, ...MAR_ROWS]));
    const ui = renderWithClient(
      <PainelShell active="registros" registrosTab="revisao" onSelect={() => {}} topbar={null}>
        <Harness />
      </PainelShell>,
    );
    fireEvent.click(await ui.findByTestId("promover-lote-btn"));

    expect(await ui.findByTestId("promover-lote-dialog")).toHaveAttribute("data-painel-theme", "dark");
    window.localStorage.clear();
  });

  it("cancelling the confirm makes no second request", async () => {
    server.use(promoteBulkDryRunSuccess());
    const ui = mount();
    fireEvent.click(await ui.findByTestId("promover-lote-btn"));

    fireEvent.click(await ui.findByTestId("promover-lote-cancel"));
    await waitFor(() => expect(ui.queryByTestId("promover-lote-dialog")).toBeNull());
    expect(bodies("POST /atrativos/promote-bulk")).toEqual([{ uf: null, dry_run: true }]);
  });

  it("zero candidates never opens the confirm", async () => {
    server.use(promoteBulkZeroCandidates());
    const ui = mount();
    fireEvent.click(await ui.findByTestId("promover-lote-btn"));

    expect(
      await ui.findByText("Nenhum atrativo elegível para promoção em lote com os filtros atuais."),
    ).toBeInTheDocument();
    expect(ui.queryByTestId("promover-lote-dialog")).toBeNull();
  });

  it("surfaces the edit-lock copy on a 423", async () => {
    server.use(
      http.post(`${API}/atrativos/promote-bulk`, () =>
        HttpResponse.json({ detail: "locked" }, { status: 423 }),
      ),
    );
    const ui = mount();
    fireEvent.click(await ui.findByTestId("promover-lote-btn"));

    expect(await ui.findByText(LOCKED_COPY)).toBeInTheDocument();
    expect(ui.queryByTestId("promover-lote-dialog")).toBeNull();
  });
});
