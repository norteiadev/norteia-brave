"""DescriptionEnrichmentAgent — enriches an atrativo with a Norteia-voice description.

Sub-state transition: signals_gathered → description_enriched.

Post-Signal FSM step (mirrors SignalAgent structurally): for an atrativo, fuzzy-match
the Guia Melhores Destinos ``-l.html`` editorial page, scrape its description, rewrite
it in the Norteia voice via the LLM, and persist it to the schemaless canonical field
``descricao_editorial`` — which then flows Rio→Mar→push→drawer (see the Rio plumbing in
brave/core/rio/routing.py). The synthetic ``posicionamento`` stays as the floor: when
there is no MD page (or no scraped text), the record keeps its current completude and
description (graceful degradation — POC §6 gotcha D).

Two cases in scope (POC §6; case 3 "web research" is descoped):
  1. MD page matched + scraped → descricao_editorial = scraped text.
  2. LLM rewrite succeeds → descricao_editorial = Norteia-voice version.
     LLM failure/exception → keep the scraped text (never propagate the error).

completude degrau (new): when the record was at the discovery ceiling (75.0 = all five
fields) and a description is written, completude_value is bumped to 90.0 (see
_compute_completude in discovery.py). A record below the ceiling keeps its value.

After enrichment the agent re-scores via route_by_score (a *_value changed), so a
borderline record can move mar↔dlq. On dlq it clears sub_state (bounce to DLQ), matching
the SignalAgent post-score convention.

LGPD/legal (POC §4): the scraped MD text is TRANSIENT LLM context — only the rewrite
(or, on rewrite failure, the scraped text) is persisted, with source provenance. Prompt
content is NEVER logged (T-02-04).

D-18 boundary: no imports from brave.lanes.destinos or brave.tasks.
"""

from __future__ import annotations

import unicodedata
import uuid
from typing import TYPE_CHECKING

import structlog
from rapidfuzz import fuzz
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from brave.config.settings import ScoreConfig
from brave.core.rio.routing import route_by_score
from brave.observability.audit import write_audit
from brave.observability.record_events import record_event
from brave.shared.ibge_distritos import resolve_distrito_place

if TYPE_CHECKING:
    from brave.clients.base import LLMClientProtocol, MelhoresDestinosClientProtocol
    from brave.config.settings import MelhoresDestinosConfig
    from brave.core.models import RioRecord
    from brave.shared.ibge_distritos import IbgeDistrito

logger = structlog.get_logger(__name__)

# token_set_ratio cutoff for the matched page's breadcrumb <Place> vs the atrativo
# município name (seat/município-level page). Below it we fall back to the distrito
# check before deciding the page belongs to a DIFFERENT place.
_MUNI_NAME_THRESHOLD: int = 85


def _fold_lower(s: str) -> str:
    """NFKD accent-fold + lowercase (município/place name comparison)."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s or "") if unicodedata.category(c) != "Mn"
    ).strip().lower()


# ---------------------------------------------------------------------------
# Phase D — Norteia voice prompt
# ---------------------------------------------------------------------------
#
# Compiled from the Norteia "Guia Mestre de Branding & Tom de Voz – vFinal"
# (2025-09-02, atualizado Abr/2026). Distilled to the parts relevant to writing a
# short attraction description: brand persona (bússola confiável; arquétipos
# Explorador + Cuidador), tom de voz, palavras-chave, and the "evitar" list. The
# fact-preservation / no-invention rules (POC §4 legal posture) are kept HARD so the
# voice never licenses fabrication.

NORTEIA_VOICE_PROMPT = """Você escreve para a Norteia — uma bússola confiável que
orienta jornadas pelo Brasil real, com presença e propósito. Arquétipos: Explorador
(curioso, aventureiro) e Cuidador (acolhedor, atento). Personalidade: inspiradora,
humana, curiosa, prática e acolhedora.

Tarefa: reescreva o texto-fonte abaixo como a descrição editorial do atrativo
"{nome}" ({municipio}/{uf}), em português do Brasil, na voz da Norteia.

Tom de voz:
- Inspirador, humano, consciente, autêntico, direto e claro.
- Valorize a brasilidade com orgulho e a conexão entre pessoas e territórios; convide
  o leitor a viver o lugar, sem soar publicitário.
- Incorpore com naturalidade (nunca como lista ou jargão forçado) o espírito de:
  jornada, propósito, conexão, brasilidade, pertencimento, curadoria.

Evite:
- Superlativos vagos ("o melhor de todos os tempos", "imperdível", "único no mundo").
- Jargões técnicos sem explicação e estereótipos culturais ou regionais.

Regras de fidelidade (OBRIGATÓRIAS — a voz nunca autoriza inventar):
- PRESERVE TODOS os fatos do texto-fonte (datas, nomes, história, motivos, tradições,
  detalhes). Não omita fatos relevantes.
- NÃO adicione nenhum fato que não esteja no texto-fonte — não invente datas, números,
  história ou significados ausentes. Apenas reescreva o que está lá, na voz da Norteia.
- Reescreva com PALAVRAS PRÓPRIAS — não copie frases literais do original.
- Prosa corrida, sem títulos, sem emojis, sem listas. Extensão proporcional ao
  conteúdo do texto-fonte (não infle nem resuma além do necessário).
- Retorne APENAS a descrição final, sem comentários.

Texto-fonte:
{descricao}
"""


# ---------------------------------------------------------------------------
# DescriptionEnrichmentAgent
# ---------------------------------------------------------------------------


class DescriptionEnrichmentAgent:
    """Enriches an atrativo with a Norteia-voice editorial description.

    Advances sub_state from "signals_gathered" to "description_enriched".
    Idempotency guard: returns immediately if sub_state != "signals_gathered".

    Args:
        md_client:  MelhoresDestinosClientProtocol implementation (real/null/fake).
        llm_client: LLMClientProtocol implementation (real/null/fake) for the rewrite.
        session:    SQLAlchemy synchronous Session.
        config:     ScoreConfig with reliability weights (for the re-score).
        md_config:  MelhoresDestinosConfig (for the voice model slug). Optional.
        distritos:  IBGE DTB distrito reference table for the breadcrumb-<Place> →
                    IBGE distrito relation (loaded once, mirrors DiscoveryAgent). None
                    → distrito enrichment is a no-op and the distrito_* keys stay absent.
    """

    def __init__(
        self,
        md_client: MelhoresDestinosClientProtocol,
        llm_client: LLMClientProtocol,
        session: Session,
        config: ScoreConfig | None = None,
        md_config: MelhoresDestinosConfig | None = None,
        distritos: list[IbgeDistrito] | None = None,
    ) -> None:
        self._md_client = md_client
        self._llm_client = llm_client
        self._session = session
        self._config = config or ScoreConfig()
        self._md_config = md_config
        self._distritos = distritos or []

    def _breadcrumb_municipio_ok(
        self, place: str | None, municipio: str, municipio_id: str | None
    ) -> bool:
        """True if the matched MD page belongs to the atrativo's município.

        The client validates the breadcrumb STATE (UF), but a nationwide WRatio match can
        still land on a same-state DIFFERENT place — most likely for generic names. This
        crosses the page's breadcrumb <Place> against the atrativo município: accept when
        <Place> matches the município name (seat/município-level page) OR resolves as a
        distrito of the parent município (rio.municipio_id). No extra GET — <Place> is the
        breadcrumb already fetched in run(). Falls back to accept (client state guard only)
        when the page has no <Place>, or no distrito reference is loaded to disambiguate a
        non-name-matching <Place>.
        """
        if not place:
            return True
        if (
            municipio
            and fuzz.token_set_ratio(_fold_lower(municipio), _fold_lower(place))
            >= _MUNI_NAME_THRESHOLD
        ):
            return True
        if self._distritos and municipio_id:
            return resolve_distrito_place(place, municipio_id, self._distritos) is not None
        return True

    async def run(self, rio: RioRecord) -> None:
        """Enrich one atrativo and advance to description_enriched.

        Pipeline:
          1. Idempotency guard: sub_state must be "signals_gathered".
          2. find_attraction_url — miss → keep floor (no write), advance + re-score.
          3. hit: fetch_description — no text → keep floor.
          4. LLM rewrite (Norteia voice) — success overwrites; failure keeps scraped.
          5. Write descricao_editorial + bump completude (75 → 90) on the ceiling.
          6. flag_modified, advance sub_state, re-score (route_by_score), bounce on dlq.
        """
        # Step 1: idempotency guard. Accept the Places-chain entry ("signals_gathered")
        # AND the TA-lane direct entry (sub_state is None): TA atrativos never pass
        # through find_contacts/gather_signals, so sweep_tripadvisor dispatches this
        # agent straight on the freshly-Rio'd record. Any other state
        # ("description_enriched", etc.) is terminal here → idempotent no-op.
        if rio.sub_state not in ("signals_gathered", None):
            return
        prior_sub_state = rio.sub_state

        normalized = rio.normalized or {}
        nome: str = normalized.get("name") or ""
        municipio: str = normalized.get("municipio") or ""
        uf: str = rio.uf or normalized.get("uf") or ""

        new_normalized = dict(normalized)
        description_written = False

        # Step 2+3: match the MD page and scrape its description. ANY external failure
        # (no page, page removed → 404/403/410, network, parse) degrades to the floor —
        # the whole MD interaction is guarded so a defect in the scraper can never strand
        # the record short of description_enriched (the client contract is "never raises",
        # but this consumer-side guard is the belt-and-suspenders that preserves the FSM).
        scraped: str | None = None
        url: str | None = None
        place: str | None = None
        try:
            url = await self._md_client.find_attraction_url(nome, municipio, uf) if nome else None
            if url:
                # Fetch the breadcrumb ONCE (cached by the client's UF guard) and reuse it
                # for BOTH the município guard here and the distrito step below.
                place = await self._md_client.fetch_breadcrumb_place(url)
                # Município guard: the client validates STATE, but WRatio can still match a
                # same-state DIFFERENT place (generic names like "Igreja Matriz"). Accept
                # only when the page's breadcrumb <Place> matches the atrativo município OR
                # resolves as a distrito of it; else keep the floor — never write a
                # wrong-place description onto a canonical record.
                if not self._breadcrumb_municipio_ok(place, municipio, rio.municipio_id):
                    logger.info(
                        "md_municipio_mismatch_kept_floor",
                        rio_id=str(rio.id),
                        municipio=municipio,
                    )
                    url = None
                else:
                    scraped = await self._md_client.fetch_description(url)
        except Exception:  # noqa: BLE001 — MD failure keeps the floor, never blocks the FSM
            logger.warning("md_scrape_failed_kept_floor", rio_id=str(rio.id))
            scraped = None

        if scraped and scraped.strip():
            descricao = scraped.strip()

            # Step 4: rewrite in the Norteia voice (case 2). Failure keeps the
            # scraped text — NEVER propagate the error (graceful degradation).
            model_slug = (
                self._md_config.voice_model_slug
                if self._md_config is not None
                else "claude-haiku-4-5"
            )
            try:
                rewritten = await self._llm_client.generate(
                    [
                        {
                            "role": "user",
                            "content": NORTEIA_VOICE_PROMPT.format(
                                nome=nome,
                                municipio=municipio,
                                uf=uf,
                                descricao=descricao,
                            ),
                        }
                    ],
                    model=model_slug,
                )
                if rewritten and rewritten.strip():
                    descricao = rewritten.strip()
                else:
                    logger.info("md_rewrite_empty_kept_scraped", rio_id=str(rio.id))
            except Exception:  # noqa: BLE001 — rewrite failure keeps the scraped text
                logger.warning("md_rewrite_failed_kept_scraped", rio_id=str(rio.id))

            # Step 5: persist descricao_editorial + bump completude on the ceiling.
            new_normalized["descricao_editorial"] = descricao
            if float(new_normalized.get("completude_value", 0.0)) == 75.0:
                # New degrau: 5 discovery fields (75.0 ceiling) + curated description → 90.
                new_normalized["completude_value"] = 90.0
            description_written = True
        else:
            # No page matched, no scraped text, or an MD failure above → keep the floor.
            logger.info("md_kept_floor", rio_id=str(rio.id), uf=uf)

        # Step 5b: distrito relation — cross the MD breadcrumb <Place> against the IBGE
        # DTB scoped to the parent município (rio.municipio_id). Best-effort, decoupled
        # from the description step: any miss (no matched url, no <Place>, no distrito
        # match) leaves every distrito_* key ABSENT (floor preserved), and any external
        # failure is swallowed — mirrors the descricao_editorial graceful-degradation
        # path so a scraper/parse defect can never strand the record short of
        # description_enriched. Writes distrito_municipio_ibge (the parent município
        # relation) alongside the distrito_* keys DiscoveryAgent also emits.
        if url and self._distritos and rio.municipio_id:
            match: IbgeDistrito | None = None
            try:
                # Reuse the breadcrumb <Place> already fetched above (one GET per page).
                match = (
                    resolve_distrito_place(place, rio.municipio_id, self._distritos)
                    if place
                    else None
                )
            except Exception:  # noqa: BLE001 — breadcrumb/distrito failure keeps the floor
                logger.warning("md_breadcrumb_failed_kept_floor", rio_id=str(rio.id))
                match = None
            if match:
                new_normalized["distrito_name"] = match.nome
                new_normalized["distrito_code"] = match.distrito_code
                new_normalized["distrito_municipio_ibge"] = match.ibge_code
                new_normalized["distrito_source"] = "md_breadcrumb"
                new_normalized["subdistrito_name"] = None
                new_normalized["subdistrito_code"] = None
                logger.info(
                    "md_distrito_resolved",
                    rio_id=str(rio.id),
                    distrito_code=match.distrito_code,
                )

        # Step 6: mutate normalized + advance sub_state.
        rio.normalized = new_normalized
        flag_modified(rio, "normalized")
        rio.sub_state = "description_enriched"

        write_audit(
            session=self._session,
            action="sub_state_advanced",
            entity_type="attraction",
            record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
            before_state={"sub_state": prior_sub_state},
            after_state={
                "sub_state": "description_enriched",
                "descricao_editorial_set": description_written,
                "completude_value": new_normalized.get("completude_value"),
            },
            actor="description_enrichment_agent",
        )
        self._session.flush()

        # Re-score: a *_value changed (completude) → borderline record can move mar↔dlq.
        route_by_score(self._session, rio, self._config)
        self._session.flush()

        # dlq bounce — mirror the SignalAgent post-score convention (sub_state cleared).
        if rio.routing == "dlq":
            rio.sub_state = None
            write_audit(
                session=self._session,
                action="sub_state_advanced",
                entity_type="attraction",
                record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
                before_state={"sub_state": "description_enriched"},
                after_state={"sub_state": None, "routing": "dlq"},
                actor="description_enrichment_agent",
            )
            self._session.flush()

        # Append-only Log-tab timeline event — mirrors the ingested/scored/routed
        # emissions in routing.py so the drawer Log tab shows the enrichment step.
        # Keyed by canonical_key (the universal drawer key). LGPD: public-geo /
        # engineering fields only — never the descricao_editorial text itself.
        canonical_key = rio.canonical_key or ""
        record_event(
            session=self._session,
            source=canonical_key.split(":", 1)[0] if canonical_key else "unknown",
            source_ref=canonical_key,
            stage="description_enriched",
            status="ok" if description_written else "skip",
            entity_type="attraction",
            uf=rio.uf,
            rio_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
            data={
                "description_written": description_written,
                "completude_value": new_normalized.get("completude_value"),
                "routing": rio.routing,
            },
        )
        self._session.flush()

        logger.info(
            "description_enriched",
            rio_id=str(rio.id),
            routing=rio.routing,
            sub_state=rio.sub_state,
            description_written=description_written,
        )
