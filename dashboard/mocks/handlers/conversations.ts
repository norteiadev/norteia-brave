import { http, HttpResponse } from "msw";

import type {
  ConversationDetail,
  ConversationListData,
} from "@/lib/conversations-api";

/**
 * MSW handlers for the conversations slice (DASH-05, D-07 offline harness).
 *
 * Same double-prefix rule as the other handlers: the browser client (`apiFetch`)
 * hits the BFF at a relative `/api/...` URL which jsdom resolves against
 * `http://localhost:3000`, and the BFF maps `/api/<rest>` → FastAPI `/<rest>`. So
 * to reach FastAPI's `/api/v1/conversations` the browser requests
 * `/api/api/v1/conversations`; MSW matches that absolute form.
 *
 * LGPD (R3, T-04-28): every sample carries ONLY a masked phone — there is no raw
 * E.164 string anywhere in these fixtures, so the transcript-panel "no raw e164
 * in the DOM" assertion holds against real-shaped data.
 *
 * Each factory returns handlers; suites pick the view-state variant
 * (success / empty / error / 401 / 404) via `server.use(...)`.
 */

const LIST = "http://localhost:3000/api/api/v1/conversations";
const DETAIL = "http://localhost:3000/api/api/v1/conversations/:rioId";

export const SAMPLE_RIO_ID = "11111111-1111-1111-1111-111111111111";

/** The masked phone the backend emits — never the raw E.164 (R3). */
export const SAMPLE_MASKED_PHONE = "+55 11 9••••-••42";

export const sampleConversationList: ConversationListData = {
  conversations: [
    {
      rio_id: SAMPLE_RIO_ID,
      phone_masked: SAMPLE_MASKED_PHONE,
      message_count: 3,
      last_message: {
        direction: "inbound",
        content: "Funciona de terça a domingo, das 9h às 17h.",
        created_at: "2026-06-15T14:32:00+00:00",
      },
    },
    {
      rio_id: "22222222-2222-2222-2222-222222222222",
      phone_masked: "+55 71 9••••-••07",
      message_count: 1,
      last_message: {
        direction: "outbound",
        content: "Olá! Somos da Norteia e gostaríamos de confirmar os horários.",
        created_at: "2026-06-15T11:05:00+00:00",
      },
    },
  ],
};

export const sampleConversationDetail: ConversationDetail = {
  rio_id: SAMPLE_RIO_ID,
  phone_masked: SAMPLE_MASKED_PHONE,
  messages: [
    {
      id: "msg-1",
      direction: "outbound",
      role: "assistant",
      content:
        "Olá! Somos da Norteia e gostaríamos de confirmar os horários de funcionamento.",
      extracted: null,
      created_at: "2026-06-15T14:30:00+00:00",
    },
    {
      id: "msg-2",
      direction: "inbound",
      role: "user",
      content: "Funciona de terça a domingo, das 9h às 17h.",
      extracted: null,
      created_at: "2026-06-15T14:31:00+00:00",
    },
    {
      id: "msg-3",
      direction: "outbound",
      role: "assistant",
      content: "Perfeito, muito obrigado pela confirmação!",
      extracted: {
        opening_hours: "ter-dom 09:00-17:00",
        confidence: 0.91,
      },
      created_at: "2026-06-15T14:32:00+00:00",
    },
  ],
};

export function conversationsListSuccess(
  data: ConversationListData = sampleConversationList,
) {
  return http.get(LIST, () => HttpResponse.json(data));
}

export function conversationsListEmpty() {
  return http.get(LIST, () =>
    HttpResponse.json({ conversations: [] } satisfies ConversationListData),
  );
}

export function conversationsListError(status = 500) {
  return http.get(LIST, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

export function conversationsListUnauthorized() {
  return http.get(LIST, () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 }),
  );
}

export function conversationDetailSuccess(
  data: ConversationDetail = sampleConversationDetail,
) {
  return http.get(DETAIL, () => HttpResponse.json(data));
}

export function conversationDetailEmpty() {
  return http.get(DETAIL, () =>
    HttpResponse.json({
      rio_id: SAMPLE_RIO_ID,
      phone_masked: SAMPLE_MASKED_PHONE,
      messages: [],
    } satisfies ConversationDetail),
  );
}

export function conversationDetailNotFound() {
  return http.get(DETAIL, () =>
    HttpResponse.json({ detail: "No conversation found for rio_id" }, {
      status: 404,
    }),
  );
}

export function conversationDetailError(status = 500) {
  return http.get(DETAIL, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

export function conversationDetailUnauthorized() {
  return http.get(DETAIL, () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 }),
  );
}

/** Default barrel: list + detail success (suites override per state). */
export const conversationsHandlers = [
  conversationsListSuccess(),
  conversationDetailSuccess(),
];
