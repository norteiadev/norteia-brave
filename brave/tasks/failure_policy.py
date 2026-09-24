"""Failure policy shared by the Celery tasks — one place for retry / quarantine / pause.

Used inside a task body, around its work; the task keeps its session and its finally:

    session, engine = _get_session()
    try:
        with task_failure_policy(self, session, "brave.find_contacts", payload={"rio_id": rio_id}):
            ...
    finally:
        session.close()

Every branch rolls the task's session back first, then:

  ProviderBalanceError → pause the motor with the reason (``pause_action`` feeds the
                         Painel's Continuar); no retry, no quarantine, task SUCCESS.
  ComplianceError      → blocked send, logged; no retry, no quarantine, task SUCCESS.
  PermanentError       → quarantine, no retry, task SUCCESS (``quarantine=False``:
                         re-raised, task FAILURE).
  anything else        → self.retry(exc=exc). Once retries are exhausted Celery re-raises
                         ``exc`` itself — never MaxRetriesExceededError when exc= is given
                         (celery/app/task.py) — so that is caught here: quarantine, then
                         re-raise so the task ends FAILURE.

Celery's Retry always propagates untouched: the producers' _producer_done skips it.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog
from celery.exceptions import Retry

from brave.shared.exceptions import ComplianceError, PermanentError, ProviderBalanceError

logger = structlog.get_logger(__name__)


@contextmanager
def task_failure_policy(
    task: Any,
    session: Any,
    task_name: str,
    *,
    nascente_id: Any = None,
    payload: dict | None = None,
    quarantine: bool = True,
    pause_action: str | None = None,
    passthrough: tuple[type[BaseException], ...] = (),
) -> Iterator[None]:
    """Apply the task failure policy to the ``with`` body (see the module docstring).

    ``nascente_id`` / ``payload`` are the quarantine row's context, as the task passes
    them. ``passthrough`` exceptions reach the task untouched (its own except handles them).
    """
    try:
        yield
    except (Retry, *passthrough):
        raise
    except ProviderBalanceError as exc:
        session.rollback()
        import redis as _redis_lib  # noqa: PLC0415

        from brave.core import engine as collection_engine  # noqa: PLC0415

        rc = _redis_lib.from_url(os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"))
        collection_engine.pause_with_reason(
            rc, "provider_balance", exc.provider, action=pause_action
        )
        logger.warning("task_provider_balance", task=task_name, provider=exc.provider)
    except ComplianceError as exc:
        session.rollback()
        logger.warning("task_compliance_blocked", task=task_name, error=str(exc))
    except PermanentError as exc:
        session.rollback()
        if not quarantine:
            raise
        _quarantine(task_name, exc, nascente_id, payload)
    except Exception as exc:
        session.rollback()
        try:
            raise task.retry(exc=exc)
        except Retry:
            raise
        except BaseException as retry_exc:
            # Inline .run() (Celery re-raises exc at once): not exhausted — the caller decides,
            # and quarantining here would add a second row when the caller's own policy runs.
            if getattr(task.request, "called_directly", False):
                raise
            # Exhausted (Celery re-raised exc), or the retry itself failed (e.g. broker down).
            if quarantine:
                note = "" if retry_exc is exc else f" (retry failed: {type(retry_exc).__name__})"
                _quarantine(task_name, exc, nascente_id, payload, note)
            raise


def _quarantine(
    task_name: str, exc: BaseException, nascente_id: Any, payload: Any, note: str = ""
) -> None:
    """PoisonQuarantine row in a fresh session (the task's own was rolled back).

    Both names are looked up on their modules at call time, so a test patching
    ``brave.core.quarantine.quarantine_poison`` or ``pipeline._get_session`` reaches
    every task.
    """
    from brave.core import quarantine  # noqa: PLC0415
    from brave.tasks import pipeline  # noqa: PLC0415 — pipeline imports this module

    q_session, _ = pipeline._get_session()
    try:
        quarantine.quarantine_poison(
            session=q_session,
            nascente_id=nascente_id,
            task_name=task_name,
            error=str(exc) + note,
            payload=payload,
        )
        q_session.commit()
    finally:
        q_session.close()
