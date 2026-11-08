"""Database coordination helpers for instance lifecycle operations."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from .instance_tasks import create_instance_task_binding, dispatch_instance_task
from .models import InstanceLifecycleLock, InstanceTask, Instances


LIFECYCLE_LOCK_ID = 1
RESERVED_LIFECYCLE_STATES = (
    Instances.LifecycleState.STARTING,
    Instances.LifecycleState.RUNNING,
    Instances.LifecycleState.STOPPING,
    Instances.LifecycleState.UNKNOWN,
)


@contextmanager
def lifecycle_transaction():
    """Serialize lifecycle decisions through the fixed database mutex row.

    The migration seeds row 1 for normal operation. ``get_or_create`` also
    repairs it after Django ``TransactionTestCase`` flushes and remains safe
    when two fresh connections attempt to initialize the row concurrently.
    The lock row must be acquired before locking an owner, instance, or port.
    """
    with transaction.atomic():
        InstanceLifecycleLock.objects.get_or_create(pk=LIFECYCLE_LOCK_ID)
        InstanceLifecycleLock.objects.select_for_update().get(pk=LIFECYCLE_LOCK_ID)
        yield


def require_lifecycle_autocommit() -> None:
    """Reject admission before file, database, or queue side effects."""
    if connection.in_atomic_block or not connection.get_autocommit():
        raise RuntimeError(
            "Instance task dispatch must happen outside an active transaction."
        )


def is_instance_lifecycle_busy(instance: Instances) -> bool:
    """Return whether an instance is reserved or has unfinished registered work."""
    if (
        instance.lifecycle_state in RESERVED_LIFECYCLE_STATES
        or instance.is_running
        or instance.pid is not None
    ):
        return True
    return instance.tasks.filter(
        finished_at__isnull=True,
        superseded_at__isnull=True,
    ).exists()


class StopReservationRejected(RuntimeError):
    """A stop could not safely reserve the current lifecycle run."""

    def __init__(self, reason: str, *, retry_after: float | None = None):
        super().__init__(reason)
        self.retry_after = retry_after


@dataclass(frozen=True)
class StopReservation:
    instance_id: int
    run_id: str
    binding: InstanceTask
    previous_state: str
    previous_task_id: str | None
    timeout_task_id: str | None
    existing: bool = False
    republish: bool = False

    @property
    def task_id(self) -> str:
        return self.binding.task_id


def reserve_stop(
    instance_id: int,
    *,
    expected_run_id: str | None = None,
    expected_shutdown_at=None,
    recover_after_seconds: int | None = None,
) -> StopReservation:
    """Reserve one verified stop task for the current run under the global lock."""
    with lifecycle_transaction():
        try:
            instance = Instances.objects.select_for_update().get(pk=instance_id)
        except Instances.DoesNotExist:
            raise StopReservationRejected("missing") from None

        current_run_id = str(instance.run_id) if instance.run_id else None
        if expected_run_id is not None and current_run_id != str(expected_run_id):
            raise StopReservationRejected("stale")
        if expected_shutdown_at is not None and instance.shutdown_at != expected_shutdown_at:
            raise StopReservationRejected("stale")
        if instance.lifecycle_state == Instances.LifecycleState.STOPPED:
            if instance.is_running or instance.pid is not None:
                raise StopReservationRejected("unverified")
            raise StopReservationRejected("not_running")
        if not current_run_id or not instance.process_executable:
            raise StopReservationRejected("unverified")

        if instance.lifecycle_state == Instances.LifecycleState.STOPPING:
            active_binding = InstanceTask.objects.filter(
                pk=instance.lifecycle_task_id,
                instance=instance,
                kind=InstanceTask.Kind.STOP,
                superseded_at__isnull=True,
            ).first()
            if active_binding and active_binding.finished_at is None:
                if active_binding.claimed_at is not None and recover_after_seconds is not None:
                    recovery_at = active_binding.claimed_at + timedelta(
                        seconds=recover_after_seconds
                    )
                    now = timezone.now()
                    if now < recovery_at:
                        raise StopReservationRejected(
                            "recovery_wait",
                            retry_after=(recovery_at - now).total_seconds(),
                        )
                elif active_binding.claimed_at is not None:
                    return StopReservation(
                        instance_id=instance.pk,
                        run_id=current_run_id,
                        binding=active_binding,
                        previous_state=instance.lifecycle_state,
                        previous_task_id=instance.lifecycle_task_id,
                        timeout_task_id=instance.timeout_task_id,
                        existing=True,
                    )
                else:
                    now = timezone.now()
                    republish_at = active_binding.created_at + timedelta(
                        seconds=recover_after_seconds or 0
                    )
                    return StopReservation(
                        instance_id=instance.pk,
                        run_id=current_run_id,
                        binding=active_binding,
                        previous_state=instance.lifecycle_state,
                        previous_task_id=instance.lifecycle_task_id,
                        timeout_task_id=instance.timeout_task_id,
                        existing=True,
                        republish=(
                            recover_after_seconds is not None and now >= republish_at
                        ),
                    )
            elif active_binding is None and recover_after_seconds is None:
                # Legacy/corrupt stopping rows can still be retried explicitly.
                pass
        elif instance.lifecycle_state not in (
            Instances.LifecycleState.RUNNING,
            Instances.LifecycleState.UNKNOWN,
        ):
            raise StopReservationRejected("not_running")

        previous_state = instance.lifecycle_state
        previous_task_id = instance.lifecycle_task_id
        timeout_task_id = instance.timeout_task_id
        binding = create_instance_task_binding(instance, InstanceTask.Kind.STOP)
        instance.lifecycle_state = Instances.LifecycleState.STOPPING
        instance.lifecycle_task_id = binding.task_id
        instance.save(update_fields=["lifecycle_state", "lifecycle_task_id"])
        return StopReservation(
            instance_id=instance.pk,
            run_id=current_run_id,
            binding=binding,
            previous_state=previous_state,
            previous_task_id=previous_task_id,
            timeout_task_id=timeout_task_id,
        )


def restore_stop_reservation(reservation: StopReservation) -> None:
    """Restore the previous state only if this unclaimed stop still owns it."""
    if reservation.existing:
        return
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(
            pk=reservation.instance_id,
            run_id=reservation.run_id,
            lifecycle_state=Instances.LifecycleState.STOPPING,
            lifecycle_task_id=reservation.task_id,
        ).first()
        binding = InstanceTask.objects.filter(pk=reservation.task_id).first()
        if (
            instance is None
            or (
                binding is not None
                and (
                    binding.claimed_at is not None
                    or binding.superseded_at is not None
                )
            )
        ):
            return
        instance.lifecycle_state = reservation.previous_state
        instance.lifecycle_task_id = reservation.previous_task_id
        instance.save(update_fields=["lifecycle_state", "lifecycle_task_id"])


def dispatch_stop_reservation(reservation: StopReservation, task) -> str:
    """Publish a committed stop binding or return a proven already-delivered ID."""
    if reservation.existing and not reservation.republish:
        return reservation.task_id
    try:
        dispatch_kwargs = {
            "instance_id": reservation.instance_id,
            "run_id": reservation.run_id,
        }
        if reservation.existing:
            return dispatch_instance_task(
                reservation.binding,
                task,
                kwargs=dispatch_kwargs,
                preserve_binding_on_error=True,
            )
        return dispatch_instance_task(
            reservation.binding,
            task,
            kwargs=dispatch_kwargs,
        )
    except Exception:
        binding = InstanceTask.objects.filter(pk=reservation.task_id).first()
        if binding is not None and binding.superseded_at is not None:
            raise
        if binding is not None and binding.claimed_at is not None:
            # The worker claim proves that the broker delivered the task even
            # if the publisher did not receive an acknowledgement.
            return reservation.task_id
        restore_stop_reservation(reservation)
        raise
