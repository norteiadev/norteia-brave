"""E2E of the image lane, Brave side: 5 destinos + their top-4 atrativos (by reviews).

Runs inside the backend container (it needs the container env: DB, R2, Wikimedia UA):
    docker compose exec -T worker /app/.venv/bin/python -m scripts.e2e_images <cmd>

    keys              the 25 test canonical_keys, comma-separated (for the API side)
    run               image lane over the 25 entities, then the photo outbox until it drains
    status            per entity: lookup outcome, picks, pushed / pending / errors
    cleanup [--apply] delete the test rows (image_lookups, entity_images, orphaned images)
                      and their archive files; dry-run without --apply

scripts/e2e_images.sh drives this plus the norteia-api side (scripts/e2e_images_api.php).
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, func, select

from brave.clients.factory import clients_for
from brave.clients.image_store import image_store_for
from brave.core.models import EntityImage, Image, ImageLookup, RioRecord
from brave.domains.images.agent import ImageLaneAgent
from brave.tasks.pipeline import _get_session, _load_config, push_pending_media

# Atrativos first: a destino cover falls back to its city's atrativo covers.
ATRATIVOS = {
    "MG:Ouro Preto": ["4601254", "313966", "555142", "313967"],
    "BA:Salvador": ["312079", "2349194", "2177922", "2349311"],
    "PE:Recife": ["2302662", "318355", "2359530", "2359523"],
    "PR:Foz do Iguaçu": ["312332", "737277", "312333", "2331701"],
    "PR:Curitiba": ["1493739", "1872890", "2093487", "1972400"],
}
DESTINOS = ["ibge:MG:3146107", "ibge:BA:2927408", "ibge:PE:2611606", "ibge:PR:4108304", "ibge:PR:4106902"]
KEYS = [f"tripadvisor:attraction:{i}" for ids in ATRATIVOS.values() for i in ids] + DESTINOS


async def _lane(session, agent, clients) -> None:
    async with clients:
        for key in KEYS:
            rio = session.scalar(select(RioRecord).where(RioRecord.canonical_key == key))
            if rio is None:
                print(f"{key}\tsem rio_record")
                continue
            try:
                result = await agent.run(rio)
                session.commit()
            except Exception as exc:  # noqa: BLE001 — report and go on, like _images_chunk
                session.rollback()
                print(f"{key}\tFALHOU {type(exc).__name__}: {exc}")
                continue
            if result is None:
                print(f"{key}\tjá tinha lookup final (rode cleanup antes)")
            else:
                picks = " ".join(f"{p.role}:{p.lane}" for p in result.picks)
                print(f"{key}\t{result.outcome}\t{picks}\trejeitadas={dict(result.rejected)}")


def run() -> int:
    session, _ = _get_session()
    try:
        effective = _load_config(session)
        if not (effective.run_real_externals and effective.images_enrichment_enabled
                and effective.images_push_enabled):
            print("lane desligada: precisa de RUN_REAL_EXTERNALS, images_enrichment_enabled e images_push_enabled")
            return 1
        clients = clients_for(effective)
        agent = ImageLaneAgent(session, clients.wikimedia, image_store_for(effective.images), effective.images)
        asyncio.run(_lane(session, agent, clients))
    finally:
        session.close()
    # The beat also drains the outbox every 15 min; this just doesn't wait for it.
    total = 0
    while (n := push_pending_media()) > 0:
        total += n
    print(f"push: {total} foto(s) enviadas à norteia-api")
    return status()


def status() -> int:
    session, _ = _get_session()
    try:
        lookups = dict(session.execute(
            select(ImageLookup.canonical_key, ImageLookup.outcome).where(ImageLookup.canonical_key.in_(KEYS))
        ).all())
        rows = session.execute(
            select(EntityImage.canonical_key, EntityImage.role,
                   func.count().filter(EntityImage.pushed_at.is_not(None)),
                   func.count().filter(EntityImage.pushed_at.is_(None), EntityImage.push_error.is_(None)),
                   func.string_agg(EntityImage.push_error, " | "))
            .where(EntityImage.canonical_key.in_(KEYS), EntityImage.removed_at.is_(None))
            .group_by(EntityImage.canonical_key, EntityImage.role)
        ).all()
    finally:
        session.close()
    per: dict[str, list[str]] = {}
    for key, role, pushed, pending, errors in rows:
        per.setdefault(key, []).append(f"{role}: {pushed} enviada(s), {pending} pendente(s)"
                                       + (f", erro: {errors}" if errors else ""))
    with_photo = sum(1 for k in KEYS if k in per)
    for key in KEYS:
        print(f"{key}\t{lookups.get(key, '-')}\t{'; '.join(per.get(key, [])) or 'sem foto'}")
    print(f"com foto: {with_photo}/{len(KEYS)} (atrativos {sum(1 for k in KEYS[:20] if k in per)}/20, "
          f"destinos {sum(1 for k in DESTINOS if k in per)}/5)")
    return 0


def cleanup(apply: bool) -> int:
    session, _ = _get_session()
    try:
        store = image_store_for(_load_config(session).images)
        image_ids = set(session.scalars(select(EntityImage.image_id).where(EntityImage.canonical_key.in_(KEYS))))
        # An image another (non-test) entity also uses stays.
        shared = set(session.scalars(select(EntityImage.image_id).where(
            EntityImage.image_id.in_(image_ids), EntityImage.canonical_key.not_in(KEYS))))
        orphans = session.execute(select(Image.id, Image.storage_key).where(Image.id.in_(image_ids - shared))).all()
        n_links = session.execute(delete(EntityImage).where(EntityImage.canonical_key.in_(KEYS))).rowcount
        n_lookups = session.execute(delete(ImageLookup).where(ImageLookup.canonical_key.in_(KEYS))).rowcount
        n_images = session.execute(delete(Image).where(Image.id.in_([i for i, _ in orphans]))).rowcount
        print(f"brave: entity_images={n_links} image_lookups={n_lookups} images={n_images} "
              f"arquivos={len(orphans)} (mantidas por uso fora do teste: {len(shared)})")
        if not apply:
            session.rollback()
            print("dry-run: nada apagado (use --apply)")
            return 0
        session.commit()
    finally:
        session.close()
    # After the commit: a failed file delete leaves an orphan file, never a row pointing at nothing.
    for _, key in orphans:
        store.delete(key)
    print("brave: apagado")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "keys":
        print(",".join(KEYS))
        return 0
    if cmd == "run":
        return run()
    if cmd == "status":
        return status()
    if cmd == "cleanup":
        return cleanup("--apply" in sys.argv)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
