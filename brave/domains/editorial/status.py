"""The editorial lane's funnel (``brave editorial-status [UF]``): posts → claims → matches →
editorial fields written → destination outbox → cost. Read-only SQL."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from brave.core.models import EditorialClaim, EditorialDestination, EditorialDocument, LLMGeneration
from brave.domains.editorial.extract_batch import LANE, pending_posts

_FIELDS_WRITTEN = text(
    """
    SELECT count(DISTINCT r.id), count(k.key)
      FROM rio_records r
      JOIN LATERAL jsonb_object_keys(CAST(r.normalized AS jsonb) -> 'editorial') AS k(key) ON true
     WHERE r.entity_type = 'attraction'
       AND jsonb_typeof(CAST(r.normalized AS jsonb) -> 'editorial') = 'object'
       AND (CAST(:uf AS text) IS NULL OR r.uf = :uf)
    """
)

# The attraction push of the editorial write: Rio attractions carrying normalized.editorial,
# and where their ACTIVE Mar row stands. A push at or after processed_at carried the current
# editorial (publish re-derives the snapshot before it POSTs); an older push or none is the
# Mar outbox still to drain; push_error is a rejection that waits for a change.
_ATTRACTION_PUSH = text(
    """
    SELECT count(*),
           count(*) FILTER (WHERE r.routing = 'mar' AND m.id IS NOT NULL),
           count(*) FILTER (WHERE r.routing = 'mar' AND m.push_error IS NULL
                              AND m.pushed_at >= coalesce(r.processed_at, '-infinity')),
           count(*) FILTER (WHERE r.routing = 'mar' AND m.id IS NOT NULL AND m.push_error IS NULL
                              AND (m.pushed_at IS NULL
                                   OR m.pushed_at < coalesce(r.processed_at, '-infinity'))),
           count(*) FILTER (WHERE r.routing = 'mar' AND m.push_error IS NOT NULL)
      FROM rio_records r
      LEFT JOIN mar_records m ON m.rio_id = r.id AND m.superseded_by_id IS NULL
     WHERE r.entity_type = 'attraction'
       AND jsonb_typeof(CAST(r.normalized AS jsonb) -> 'editorial') = 'object'
       AND (CAST(:uf AS text) IS NULL OR r.uf = :uf)
    """
)


def funnel(session: Session, uf: str | None, *, est_usd_per_post: float) -> dict[str, Any]:
    """``est_usd_per_post`` is settings.editorial_est_usd_per_post (the single source)."""
    uf = uf.upper() if uf else None
    docs = EditorialDocument
    in_uf = [docs.uf == uf] if uf else []

    def doc_count(*where: Any) -> int:
        return session.scalar(select(func.count()).select_from(docs).where(*in_uf, *where)) or 0

    claim_rows = session.execute(
        select(EditorialClaim.status, func.count())
        .join(docs, docs.url == EditorialClaim.document_url)
        .where(*in_uf)
        .group_by(EditorialClaim.status)
    ).all()
    matched_attractions = session.scalar(
        select(func.count(func.distinct(EditorialClaim.canonical_key)))
        .join(docs, docs.url == EditorialClaim.document_url)
        .where(*in_uf, EditorialClaim.status == "matched", EditorialClaim.entity_kind == "atrativo")
    )
    written_records, written_fields = session.execute(_FIELDS_WRITTEN, {"uf": uf}).one()
    with_editorial, in_mar, pushed, push_pending, push_error = session.execute(
        _ATTRACTION_PUSH, {"uf": uf}
    ).one()

    dest = EditorialDestination
    dest_uf = [dest.uf == uf] if uf else []

    def dest_count(*where: Any) -> int:
        return session.scalar(select(func.count()).select_from(dest).where(*dest_uf, *where)) or 0

    cost = session.scalar(
        select(func.coalesce(func.sum(LLMGeneration.usd_cost), 0)).where(LLMGeneration.lane == LANE)
    )
    pending = pending_posts(session, uf)
    return {
        "uf": uf,
        "documents": {
            "fetched": doc_count(),
            "extracted": doc_count(docs.extracted_at.isnot(None)),
            "error": doc_count(docs.error.isnot(None)),
            "pending": pending,
            "in_flight": doc_count(docs.batch_id.isnot(None)),
        },
        "claims": {status: n for status, n in claim_rows},
        "attractions_matched": matched_attractions or 0,
        "editorial": {"attractions": written_records or 0, "fields": written_fields or 0},
        "destinations": {
            "pending": dest_count(dest.pushed_at.is_(None), dest.push_error.is_(None)),
            "pushed": dest_count(dest.pushed_at.isnot(None)),
            "error": dest_count(dest.push_error.isnot(None)),
        },
        "attraction_push": {
            "with_editorial": with_editorial,
            "in_mar": in_mar,
            "pushed": pushed,
            "pending": push_pending,
            "error": push_error,
            "not_in_mar": with_editorial - in_mar,
        },
        "llm_cost_usd": float(cost or 0),
        "pending_estimate_usd": round(pending * est_usd_per_post, 4),
        "est_usd_per_post": est_usd_per_post,
    }


def format_funnel(f: dict[str, Any]) -> str:
    d, c, e, dst = f["documents"], f["claims"], f["editorial"], f["destinations"]
    ap = f["attraction_push"]
    claims = ", ".join(f"{k} {v}" for k, v in sorted(c.items())) or "none"
    return "\n".join(
        [
            f"editorial-status {f['uf'] or 'ALL'}",
            f"  posts        fetched {d['fetched']} | extracted {d['extracted']} | error {d['error']}"
            f" | pending {d['pending']} | in flight {d['in_flight']}",
            f"  to extract   {d['pending']} post(s): estimated ${f['pending_estimate_usd']:.2f}"
            f" (${f['est_usd_per_post']:.2f}/post, batch rates) — the cost gate before --submit-only",
            f"  claims       {claims}",
            f"  attractions  matched {f['attractions_matched']}"
            f" | with editorial {e['attractions']} ({e['fields']} fields written)",
            f"  attr. push   with editorial {ap['with_editorial']} | in Mar {ap['in_mar']}"
            f" | pushed with editorial {ap['pushed']} | pending {ap['pending']}"
            f" | push_error {ap['error']} | not in Mar {ap['not_in_mar']} (DLQ: waits for Promoção)",
            f"  destinations pending {dst['pending']} | pushed {dst['pushed']} | error {dst['error']}",
            f"  LLM cost     ${f['llm_cost_usd']:.4f} (editorial lane, all UFs)",
        ]
    )
