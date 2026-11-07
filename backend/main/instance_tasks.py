"""Persist and track Celery task ownership for instance operations."""

from __future__ import annotations

from functools import wraps
import sys
from uuid import uuid4

from celery.exceptions import Retry
from django.db import connection, transaction
from django.utils import timezone

from .models import InstanceTask


def _require_post_commit_dispatch() -> None:
    """Reject publishing while the default connection can still roll back."""
    in_atomic_block = connection.in_atomic_block
    autocommit_enabled = connection.get_autocommit()
    if in_atomic_block or not autocommit_enabled:
        raise RuntimeError(
            "Instance task dispatch must happen outside an active transaction."
        )


def create_instance_task_binding(instance, kind: str) -> InstanceTask:
    """Create a task ID and durable instance binding without dispatching work.

    Call this inside an admission transaction when the binding must commit with
    related state. Dispatch only after that transaction commits.
    """
    if kind not in InstanceTask.Kind.values:
        raise ValueError(f"Unsupported instance task kind: {kind}")
    return InstanceTask.objects.create(
        task_id=str(uuid4()),
        instance=instance,
        kind=kind,
    )


def dispatch_instance_task(binding: InstanceTask, task, *, kwargs: dict) -> str:
    """Dispatch a committed binding and remove it if enqueue fails.

    This primitive can run from a Task 5 ``transaction.on_commit`` callback.
    It rejects calls while any outer or manual transaction is still active.
    """
    _require_post_commit_dispatch()
    try:
        task.apply_async(kwargs=kwargs, task_id=binding.task_id)
    except Exception:
        InstanceTask.objects.filter(pk=binding.task_id).delete()
        raise
    return binding.task_id


def enqueue_instance_task(instance, kind: str, task, *, kwargs: dict) -> str:
    """Commit a binding and enqueue only when the caller is in autocommit mode.

    The preflight check must run before the binding is created. Callers that
    already have an admission transaction should instead create the binding
    there and invoke ``dispatch_instance_task`` from ``transaction.on_commit``.
    """
    _require_post_commit_dispatch()
    with transaction.atomic():
        binding = create_instance_task_binding(instance, kind)
    return dispatch_instance_task(binding, task, kwargs=kwargs)


def mark_instance_task_finished(task_id: str | None) -> None:
    """Set a registered task's terminal timestamp; unregistered task IDs are safe."""
    if not task_id:
        return
    InstanceTask.objects.filter(pk=task_id, finished_at__isnull=True).update(
        finished_at=timezone.now()
    )


def track_instance_task(function):
    """Finish persisted bindings after terminal task execution, preserving retry."""
    @wraps(function)
    def wrapped(task, *args, **kwargs):
        retry_pending = False
        try:
            result = function(task, *args, **kwargs)
            retry_pending = isinstance(result, Retry)
            return result
        finally:
            current_exception = sys.exc_info()[1]
            if not retry_pending and not isinstance(current_exception, Retry):
                request = getattr(task, "request", None)
                mark_instance_task_finished(getattr(request, "id", None))

    return wrapped
