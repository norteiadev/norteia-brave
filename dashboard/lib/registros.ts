/**
 * Registros (#88): operator copy for the record list + detail — pipeline stage
 * labels, the DLQ reason (motivo) explanations, and the score potential.
 *
 * The motivo map mirrors the `dlq_reason` values the collector writes today
 * (brave/core/rio/routing.py, brave/domains/places/*, brave/api/routers/*).
 * Phase 2 moves the reason-bucket normalization server-side; until then an
 * unknown code is shown raw, never guessed.
 */

/** PT-BR label per Brave pipeline stage (RecordEvent.stage). */
export const STAGE_LABELS: Record<string, string> = {
  tripadvisor_synced: "Sincronizado do TripAdvisor",
  review_enriched: "Avaliações enriquecidas",
  municipio_resolved: "Município resolvido",
  geo_enriched: "Geolocalização complementada",
  parent_destino_linked: "Destino-pai vinculado",
  validated: "Validado",
  ingested: "Ingerido (Nascente)",
  deduped: "Duplicado",
  scored: "Pontuado (confiabilidade)",
  routed: "Roteado",
  description_enriched: "Descrição editorial gerada",
  places_enriched: "Enriquecido (Google Places)",
  places_descarte: "Descartado pelo Google Places",
  places_match_reverted: "Match do Google Places desfeito",
  quarantined: "Quarentena (falha)",
};

export type ReasonTone = "ok" | "warn" | "bad";

export interface ReasonInfo {
  label: string;
  why: string;
  next: string;
  tone: ReasonTone;
}

const GATE = "DLQ & Gate";

const REASONS: Record<string, ReasonInfo> = {
  awaiting_promotion: {
    label: "Pronto para promover",
    why: "O score passa do limiar com a validação humana e nada mais bloqueia.",
    next: "Conferir e promover.",
    tone: "ok",
  },
  no_recent_reviews: {
    label: "Sem review recente",
    why: "A última review é antiga demais para o gate de atualidade; promover agora devolve 409.",
    next: "Aguardar uma review nova ou descartar.",
    tone: "warn",
  },
  municipio_nao_confirmado: {
    label: "Município não confirmado",
    why: "A busca da descrição não menciona o município do registro (possível homônimo).",
    next: "Confirmar o município e corrigir o registro, ou descartar.",
    tone: "warn",
  },
  descricao_nao_fundamentada: {
    label: "Descrição sem fundamento",
    why: "O texto gerado afirma coisas que a busca não confirma; o rascunho ficou fora do Mar.",
    next: "Escrever a descrição editorial (Editar) ou descartar.",
    tone: "warn",
  },
  closed_temporarily: {
    label: "Fechado temporariamente",
    why: "O Google Places marca o lugar como fechado por ora (CLOSED_TEMPORARILY).",
    next: "Promover mesmo assim ou aguardar a reabertura.",
    tone: "warn",
  },
  closed_place: {
    label: "Fechado permanentemente",
    why: "O Google Places marca o lugar como fechado de vez.",
    next: "Nada a fazer: o registro sai do pipeline.",
    tone: "bad",
  },
  no_contact_found: {
    label: "Sem contato",
    why: "Nenhum telefone foi encontrado para a consulta por WhatsApp.",
    next: "Decidir com os dados atuais: promover ou descartar.",
    tone: "warn",
  },
  no_contact_phone: {
    label: "Sem telefone",
    why: "O registro não tem telefone para a consulta por WhatsApp.",
    next: "Decidir com os dados atuais: promover ou descartar.",
    tone: "warn",
  },
  owner_opted_out: {
    label: "Responsável pediu para sair",
    why: "O responsável pediu para não ser contatado pelo WhatsApp (opt-out).",
    next: "Não contatar de novo; decidir com os dados atuais.",
    tone: "warn",
  },
  owner_no_answer: {
    label: "Responsável não respondeu",
    why: "A consulta por WhatsApp ficou sem resposta.",
    next: "Decidir com os dados atuais: promover ou descartar.",
    tone: "warn",
  },
  steward_rejected: {
    label: "Descartado pelo steward",
    why: "Um steward descartou o registro.",
    next: "Nada a fazer.",
    tone: "bad",
  },
  steward_rejected_gate: {
    label: "Recusado no gate WhatsApp",
    why: "Um steward recusou a consulta por WhatsApp deste registro.",
    next: "Decidir com os dados atuais: promover ou descartar.",
    tone: "warn",
  },
  steward_sent_to_review: {
    label: "Enviado para revisão",
    why: "Um steward mandou o registro para revisão manual.",
    next: "Conferir os campos e promover ou descartar.",
    tone: "warn",
  },
  community_error_report: {
    label: "Erro reportado pela comunidade",
    why: "Um usuário reportou erro neste registro publicado; ele voltou para revisão.",
    next: "Conferir e corrigir os campos, depois promover de novo.",
    tone: "warn",
  },
  google_tipo_negocio: {
    label: "Google: tipo de negócio",
    why: "O Google Places associou um negócio (loja, restaurante) a este atrativo do TripAdvisor.",
    next: `Aceitar o lugar ou liberar em ${GATE}, ou descartar.`,
    tone: "warn",
  },
  google_fechado_revisao: {
    label: "Google: fechado, revisar",
    why: "O Google marca o lugar como fechado de vez, mas o tipo não fecha (praia, rio): homônimo ou listagem errada.",
    next: `Conferir e liberar em ${GATE}, ou descartar.`,
    tone: "warn",
  },
  categoria_revisao: {
    label: "Categoria não confirmada",
    why: "Nem a categoria do TripAdvisor nem os tipos do Google indicam um lugar para visitar.",
    next: `Liberar como atrativo em ${GATE}, ou descartar.`,
    tone: "warn",
  },
  nao_atrativo: {
    label: "Não é atrativo",
    why: "O Google Places identificou um negócio, não um lugar para visitar.",
    next: "Nada a fazer: o registro sai do pipeline.",
    tone: "bad",
  },
  dedup_discarded: {
    label: "Descartado como duplicado",
    why: "Um steward descartou o registro como duplicado de outro no Mar.",
    next: "Nada a fazer.",
    tone: "bad",
  },
};

// The score gate writes free text with the score embedded (routing.py).
const SCORE_GATE = /^score=([\d.]+) below threshold_mar=([\d.]+)$/;

/** One-decimal pt-BR number ("61,0"). */
export const fmt1 = (n: number) =>
  n.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** Score with the human validation added (what a promote would reach). */
export function scorePotential(score: number, weightValidacao: number): number {
  return Math.min(100, score + weightValidacao);
}

/**
 * Explain a `dlq_reason`. `weightValidacao` lets the score-gate text say
 * whether a promote alone would cross the threshold. Unknown → null (the caller
 * shows the raw code).
 */
export function explainReason(
  code: string | null,
  weightValidacao = 0,
): ReasonInfo | null {
  if (!code) return null;
  const known = REASONS[code];
  if (known) return known;
  const gate = SCORE_GATE.exec(code);
  if (!gate) return null;
  const score = Number(gate[1]);
  const threshold = Number(gate[2]);
  const passes = scorePotential(score, weightValidacao) >= threshold;
  return {
    label: "Score abaixo do limiar",
    why: `Score ${fmt1(score)} abaixo do limiar ${fmt1(threshold)} do Mar.`,
    next: passes
      ? "A validação humana leva o score ao limiar: conferir e promover."
      : "Nem com a validação humana chega ao limiar: completar horário, endereço ou site, ou descartar.",
    tone: passes ? "ok" : "warn",
  };
}

