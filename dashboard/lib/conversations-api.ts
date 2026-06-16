/**
 * Conversations data layer (DASH-05, D-01 · R2 Option B · R3 LGPD).
 *
 * Query keys + typed fetchers for the WhatsApp-transcript slice. Both calls go
 * through the BFF via `apiFetch` (relative `/api/...`, operator Bearer attached)
 * — never to FastAPI directly. The endpoints are trivial read-only SELECTs over
 * the append-only `conversation_message` log (decoupled from LangGraph
 * checkpoints).
 *
 * Backing endpoints (brave/api/routers/dashboard.py):
 *   GET /api/v1/conversations          → list: one entry per rio_id with the
 *     masked phone, message_count, and last_message.
 *   GET /api/v1/conversations/{rio_id} → transcript: messages ordered oldest→
 *     newest (direction inbound/outbound, role, content, extracted), masked
 *     phone; 404 on unknown rio_id.
 *
 * LGPD (R3, T-04-28): the backend emits ONLY the masked phone (`phone_masked`).
 * This layer never holds nor reconstructs a raw E.164 number — there is no field
 * for it in either response type.
 */

import { apiFetch } from "@/lib/api-client";

export type MessageDirection = "inbound" | "outbound";

/** A single transcript turn. `content` is the rendered bubble text. */
export interface ConversationMessage {
  id: string;
  direction: MessageDirection;
  role: string;
  content: string;
  /** Structured extraction snapshot attached at a message boundary (or null). */
  extracted: Record<string, unknown> | null;
  created_at: string | null;
}

/** The last message preview shown in the master list. */
export interface ConversationLastMessage {
  direction: MessageDirection;
  content: string;
  created_at: string | null;
}

/** One conversation in the master list (per rio_id). */
export interface ConversationListItem {
  rio_id: string;
  /** Masked phone only — never the raw E.164 (R3, T-04-28). */
  phone_masked: string;
  message_count: number;
  last_message: ConversationLastMessage | null;
}

export interface ConversationListData {
  conversations: ConversationListItem[];
}

/** A full transcript for one rio_id. */
export interface ConversationDetail {
  rio_id: string;
  /** The conversation's masked phone (from the log — never raw PII, R3). */
  phone_masked: string;
  messages: ConversationMessage[];
}

export const conversationKeys = {
  all: ["conversations"] as const,
  list: ["conversations", "list"] as const,
  detail: (rioId: string) => ["conversations", "detail", rioId] as const,
};

export function fetchConversations(): Promise<ConversationListData> {
  return apiFetch<ConversationListData>("api/v1/conversations");
}

export function fetchConversationDetail(
  rioId: string,
): Promise<ConversationDetail> {
  return apiFetch<ConversationDetail>(`api/v1/conversations/${rioId}`);
}
