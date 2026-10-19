"""Two persist_normalized calls on one row with no flush between: the 2nd must not drop the 1st."""

from sqlalchemy import JSON, Integer, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from brave.core.rio.persist import persist_normalized


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "row"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    normalized: Mapped[dict | None] = mapped_column(JSON)


def test_second_persist_keeps_first_unflushed_write() -> None:
    engine = create_engine("sqlite://")
    _Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(_Row(id=1, normalized={"name": "x"}))
        s.commit()
        row = s.get(_Row, 1)
        before = dict(row.normalized)
        persist_normalized(s, row, before, {**before, "ta_backfilled_at": "t"})  # TA stamp
        before = dict(row.normalized)  # Places agent snapshot (stamp included)
        persist_normalized(s, row, before, {**before, "google_enriched": True})  # agent write
        s.commit()
        s.expire_all()
        assert s.get(_Row, 1).normalized == {"name": "x", "ta_backfilled_at": "t", "google_enriched": True}
