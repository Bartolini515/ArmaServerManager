import os
import time
import math
from collections.abc import Mapping
from datetime import timedelta

from celery import shared_task
from celery.exceptions import Ignore, Retry
from django.utils import timezone
from django.utils.dateparse import parse_datetime
import psutil
from servermanager.celery import app as celery_app

from .instance_lifecycle import (
    lifecycle_transaction,
    reserve_stop,
    dispatch_stop_reservation,
    StopReservationRejected,
)
from .models import InstanceTask, Instances
from .instance_tasks import track_instance_task
from .download_jobs import run_download_job
from .serverhandling.start_server import (
    ProcessCreationFailedError,
    start_server,
)
from .serverhandling.process_identity import (
    RUN_ID_ENVIRONMENT_KEY,
    ProcessIdentity,
    UnverifiedProcessError,
    capture_process_identity,
    find_udp_processes,
    get_verified_process,
)

@shared_task(bind=True)
@track_instance_task
def download_mods_task(
    self,
    instance_id: int,
    name: str | None = None,
    user: str | None = None,
    file_path: str | None = None,
    mods_directory: str | None = None,
) -> dict:
    """Run the durable download binding for the current instance state.

    Legacy path and identity hints remain accepted for queued task messages,
    but the worker deliberately ignores them.
    """
    del name, user, file_path, mods_directory
    return run_download_job(self, instance_id)

@shared_task(bind=True)
@track_instance_task
def start_server_task(self, instance_id: int, arma3_dir: str, run_id: str) -> dict:
    """Launch only the claimed reservation and persist verified process identity."""
    task_id = getattr(getattr(self, "request", None), "id", None)
    snapshot = None
    ignore_delivery = False

    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        binding = InstanceTask.objects.select_for_update().filter(pk=task_id).first()
        if instance is None or binding is None:
            ignore_delivery = True
        elif binding.superseded_at is not None or binding.finished_at is not None:
            ignore_delivery = True
        elif binding.claimed_at is not None:
            # A duplicate delivery never retires a binding still owned by the
            # original worker, even after that worker has changed lifecycle state.
            ignore_delivery = True
        elif (
            binding.instance_id != instance.pk
            or binding.kind != InstanceTask.Kind.START
            or str(instance.run_id) != str(run_id)
            or instance.lifecycle_task_id != task_id
            or instance.lifecycle_state != Instances.LifecycleState.STARTING
        ):
            if binding.finished_at is None:
                binding.finished_at = timezone.now()
                binding.save(update_fields=["finished_at"])
            ignore_delivery = True
        elif instance.start_claimed_at is not None or binding.claimed_at is not None:
            ignore_delivery = True
        else:
            claimed_at = timezone.now()
            instance.start_claimed_at = claimed_at
            instance.save(update_fields=["start_claimed_at"])
            binding.claimed_at = claimed_at
            binding.save(update_fields=["claimed_at"])
            snapshot = instance

    if ignore_delivery:
        raise Ignore()

    start_signal_guard = _start_attempt_signal_guard(
        instance_id,
        str(run_id),
        task_id,
    )

    def launch_signal_guard(operation, process):
        return _signal_if_current_start_launch(
            instance_id,
            str(run_id),
            task_id,
            operation,
            process,
        )

    _update_task_state_safely(
        self,
        state="PROGRESS",
        meta={"status": "Uruchamianie serwera..."},
    )
    try:
        if (
            not isinstance(snapshot.process_executable, str)
            or not snapshot.process_executable.strip()
            or not isinstance(snapshot.start_file_path, str)
            or not snapshot.start_file_path.strip()
            or not snapshot.log_file
        ):
            raise RuntimeError("The start reservation is missing its launch snapshot.")
        expected_arma_directory = os.path.dirname(snapshot.process_executable)
        log_file_path = os.fspath(snapshot.log_file.path)
        if not log_file_path.strip():
            raise RuntimeError("The start reservation is missing its launch snapshot.")
    except Exception:
        # Nothing can have launched before the call below. Release only this
        # run/task reservation; a stale preflight failure cannot clear a newer
        # attempt because _finish_start_without_process applies both fences.
        _finish_start_without_process(instance_id, str(run_id), task_id)
        raise RuntimeError("The server start could not be prepared.") from None

    try:
        process = start_server(
            expected_arma_directory,
            snapshot.start_file_path,
            log_file_path,
            run_id=str(run_id),
            launch_signal_guard=launch_signal_guard,
        )
    except Retry:
        raise
    except ProcessCreationFailedError:
        _finish_start_without_process(instance_id, str(run_id), task_id)
        raise
    except Exception as exc:
        launch_identity = getattr(exc, "launch_identity", None)
        if isinstance(launch_identity, ProcessIdentity):
            _mark_run_unknown(
                instance_id,
                str(run_id),
                task_id,
                pid=launch_identity.pid,
                process_created_at=launch_identity.created_at,
                process_executable=snapshot.process_executable,
                may_be_live=True,
                clear_deadline=True,
            )
            raise
        _mark_run_unknown(
            instance_id,
            str(run_id),
            task_id,
            may_be_live=True,
            clear_deadline=True,
        )
        raise

    root_identity = getattr(process, "_asm_process_identity", None)
    if not isinstance(root_identity, ProcessIdentity):
        _mark_run_unknown(
            instance_id,
            str(run_id),
            task_id,
            may_be_live=True,
            clear_deadline=True,
        )
        raise UnverifiedProcessError("Launch identity was not anchored to its child process")

    try:
        identity = _resolve_launched_identity(
            process,
            root_identity,
            str(run_id),
            snapshot.process_executable,
        )
    except Exception:
        _mark_run_unknown(
            instance_id,
            str(run_id),
            task_id,
            pid=root_identity.pid,
            process_created_at=root_identity.created_at,
            process_executable=snapshot.process_executable,
            may_be_live=True,
            clear_deadline=True,
        )
        raise

    launched_at = timezone.now()

    try:
        identity_persisted = _persist_started_identity(
            instance_id,
            str(run_id),
            task_id,
            identity,
            snapshot.process_executable,
        )
    except Exception:
        _compensate_unpersisted_launch(
            snapshot,
            identity,
            str(run_id),
            task_id,
            signal_guard=start_signal_guard,
        )
        raise

    if not identity_persisted:
        _terminate_launched_identity(
            snapshot,
            identity,
            str(run_id),
            task_id=task_id,
            signal_guard=start_signal_guard,
        )
        raise Ignore()

    deadline = None
    timeout_task_id = None
    if not snapshot.is_admin_instance:
        deadline = launched_at + timedelta(hours=1)
        try:
            timeout_result = instance_timeout_task.apply_async(
                kwargs={
                    "instance_id": instance_id,
                    "run_id": str(run_id),
                    "shutdown_at": deadline.isoformat(),
                },
                countdown=3600,
            )
        except Exception:
            if not _compensate_started_process(
                instance_id,
                str(run_id),
                task_id,
                signal_guard=start_signal_guard,
            ):
                _terminate_launched_identity(
                    snapshot,
                    identity,
                    str(run_id),
                    task_id=task_id,
                    signal_guard=start_signal_guard,
                )
            raise

        timeout_task_id = getattr(timeout_result, "id", None)
        if not timeout_task_id:
            if not _compensate_started_process(
                instance_id,
                str(run_id),
                task_id,
                signal_guard=start_signal_guard,
            ):
                _terminate_launched_identity(
                    snapshot,
                    identity,
                    str(run_id),
                    task_id=task_id,
                    signal_guard=start_signal_guard,
                )
            raise RuntimeError("The server timeout could not be registered safely.")

    persisted = False
    try:
        with lifecycle_transaction():
            current = Instances.objects.select_for_update().filter(pk=instance_id).first()
            if (
                current is not None
                and str(current.run_id) == str(run_id)
                and current.lifecycle_task_id == task_id
                and current.lifecycle_state == Instances.LifecycleState.STARTING
                and current.pid == identity.pid
                and current.process_created_at == identity.created_at
            ):
                current.lifecycle_state = Instances.LifecycleState.RUNNING
                current.is_running = True
                current.shutdown_at = deadline
                current.timeout_task_id = timeout_task_id
                current.save(
                    update_fields=[
                        "lifecycle_state",
                        "is_running",
                        "shutdown_at",
                        "timeout_task_id",
                    ]
                )
                persisted = True
    except Exception:
        try:
            if not _compensate_started_process(
                instance_id,
                str(run_id),
                task_id,
                signal_guard=start_signal_guard,
                deadline=deadline,
                timeout_task_id=timeout_task_id,
            ):
                _terminate_launched_identity(
                    snapshot,
                    identity,
                    str(run_id),
                    task_id=task_id,
                    signal_guard=start_signal_guard,
                )
        except Exception:
            _mark_run_unknown(
                instance_id,
                str(run_id),
                task_id,
                pid=identity.pid,
                process_created_at=identity.created_at,
                process_executable=snapshot.process_executable,
                may_be_live=True,
                shutdown_at=deadline,
                timeout_task_id=timeout_task_id,
            )
            raise
        raise

    if not persisted:
        try:
            if not _compensate_started_process(
                instance_id,
                str(run_id),
                task_id,
                signal_guard=start_signal_guard,
                deadline=deadline,
                timeout_task_id=timeout_task_id,
            ):
                _terminate_launched_identity(
                    snapshot,
                    identity,
                    str(run_id),
                    task_id=task_id,
                    signal_guard=start_signal_guard,
                )
        except Exception:
            _mark_run_unknown(
                instance_id,
                str(run_id),
                task_id,
                pid=identity.pid,
                process_created_at=identity.created_at,
                process_executable=snapshot.process_executable,
                may_be_live=True,
                shutdown_at=deadline,
                timeout_task_id=timeout_task_id,
            )
            raise
        raise Ignore()

    _update_task_state_safely(
        self,
        state="SUCCESS",
        meta={"status": "Serwer został uruchomiony pomyślnie."},
    )
    return {"status": "Serwer został uruchomiony pomyślnie."}

@shared_task(bind=True)
@track_instance_task
def stop_server_task(self, instance_id: int, run_id: str) -> dict:
    """Stop only the matching reserved run after identity-checked verification."""
    task_id = getattr(getattr(self, "request", None), "id", None)
    snapshot = None
    ignore_delivery = False
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        binding = InstanceTask.objects.select_for_update().filter(pk=task_id).first()
        if instance is None or binding is None:
            ignore_delivery = True
        elif binding.superseded_at is not None or binding.finished_at is not None:
            ignore_delivery = True
        elif binding.claimed_at is not None:
            ignore_delivery = True
        elif (
            binding.instance_id != instance.pk
            or binding.kind != InstanceTask.Kind.STOP
            or str(instance.run_id) != str(run_id)
            or instance.lifecycle_task_id != task_id
            or instance.lifecycle_state != Instances.LifecycleState.STOPPING
        ):
            if binding.finished_at is None:
                binding.finished_at = timezone.now()
                binding.save(update_fields=["finished_at"])
            ignore_delivery = True
        else:
            binding.claimed_at = timezone.now()
            binding.save(update_fields=["claimed_at"])
            snapshot = instance

    if ignore_delivery:
        raise Ignore()

    _update_task_state_safely(
        self,
        state="PROGRESS",
        meta={"status": "Zatrzymywanie serwera..."},
    )

    def signal_guard(identity, operation, *, root_identity=None, expected_executable=None):
        return _signal_if_current_stop_attempt(
            instance_id,
            str(run_id),
            task_id,
            identity,
            operation,
            root_identity=root_identity,
            expected_executable=expected_executable,
        )

    try:
        _stop_verified_instance(
            snapshot,
            str(run_id),
            signal_guard=signal_guard,
        )
    except Ignore:
        raise
    except Exception:
        _mark_run_unknown(
            instance_id,
            str(run_id),
            task_id,
            may_be_live=True,
        )
        raise

    stopped_timeout_task_id = _mark_run_stopped(
        instance_id,
        str(run_id),
        expected_task_id=task_id,
    )
    if stopped_timeout_task_id is None:
        raise RuntimeError("The server stopped but its lifecycle reservation changed.")

    _update_task_state_safely(
        self,
        state="SUCCESS",
        meta={"status": "Zatrzymywanie serwera zakończone pomyślnie!"},
    )
    return {"status": "Serwer został zatrzymany pomyślnie."}
    
def _update_task_state_safely(task, *, state: str, meta: dict) -> None:
    """Result-backend outages must not alter a safely coordinated operation."""
    try:
        task.update_state(state=state, meta=meta)
    except Exception:
        return


def _signal_if_current_stop_attempt(
    instance_id: int,
    run_id: str,
    task_id: str,
    identity: ProcessIdentity,
    operation: str,
    *,
    root_identity: ProcessIdentity | None = None,
    expected_executable: str | None = None,
) -> bool:
    """Fence a process signal to the currently reserved stop attempt."""
    if operation not in ("terminate", "kill"):
        raise ValueError("Unsupported process signal operation.")
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        binding = InstanceTask.objects.select_for_update().filter(pk=task_id).first()
        if (
            instance is None
            or str(instance.run_id) != run_id
            or instance.lifecycle_state != Instances.LifecycleState.STOPPING
            or instance.lifecycle_task_id != task_id
            or binding is None
            or binding.instance_id != instance.pk
            or binding.kind != InstanceTask.Kind.STOP
            or binding.claimed_at is None
            or binding.finished_at is not None
            or binding.superseded_at is not None
        ):
            raise Ignore()

        process = _verify_signal_target(
            identity,
            run_id,
            root_identity=root_identity,
            expected_executable=expected_executable,
        )
        if process is None:
            return False
        getattr(process, operation)()
        return True


def _current_start_attempt_locked(instance, binding, run_id: str, task_id: str) -> bool:
    return bool(
        instance is not None
        and binding is not None
        and str(instance.run_id) == run_id
        and instance.lifecycle_task_id == task_id
        and instance.start_claimed_at is not None
        and instance.lifecycle_state
        in (
            Instances.LifecycleState.STARTING,
            Instances.LifecycleState.RUNNING,
            Instances.LifecycleState.UNKNOWN,
        )
        and binding.instance_id == instance.pk
        and binding.kind == InstanceTask.Kind.START
        and binding.claimed_at is not None
        and binding.finished_at is None
        and binding.superseded_at is None
    )


def _start_attempt_signal_guard(instance_id: int, run_id: str, task_id: str):
    """Build the required attempt-fenced guard for launch compensation signals."""
    def signal_guard(identity, operation, *, root_identity=None, expected_executable=None):
        return _signal_if_current_start_attempt(
            instance_id,
            run_id,
            task_id,
            identity,
            operation,
            root_identity=root_identity,
            expected_executable=expected_executable,
        )

    return signal_guard


def _require_current_start_attempt(instance_id: int, run_id: str, task_id: str):
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        binding = InstanceTask.objects.select_for_update().filter(pk=task_id).first()
        if not _current_start_attempt_locked(instance, binding, run_id, task_id):
            raise Ignore()
        return instance


def _signal_if_current_start_attempt(
    instance_id: int,
    run_id: str,
    task_id: str,
    identity: ProcessIdentity,
    operation: str,
    *,
    root_identity: ProcessIdentity | None = None,
    expected_executable: str | None = None,
) -> bool:
    """Fence a launch-compensation signal to its still-current start attempt."""
    if operation not in ("terminate", "kill"):
        raise ValueError("Unsupported process signal operation.")
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        binding = InstanceTask.objects.select_for_update().filter(pk=task_id).first()
        if not _current_start_attempt_locked(instance, binding, run_id, task_id):
            raise Ignore()
        process = _verify_signal_target(
            identity,
            run_id,
            root_identity=root_identity,
            expected_executable=expected_executable,
        )
        if process is None:
            return False
        getattr(process, operation)()
        return True


def _signal_if_current_start_launch(
    instance_id: int,
    run_id: str,
    task_id: str,
    operation: str,
    process,
) -> bool:
    """Signal only a Popen child with verified birth, run marker and attempt."""
    if operation != "terminate":
        raise ValueError("Only terminate is allowed for Popen-anchored cleanup.")
    identity = getattr(process, "_asm_process_identity", None)
    try:
        if not isinstance(identity, ProcessIdentity) or process.pid != identity.pid:
            return False
    except Exception:
        return False
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        binding = InstanceTask.objects.select_for_update().filter(pk=task_id).first()
        if not _current_start_attempt_locked(instance, binding, run_id, task_id):
            raise Ignore()
        if getattr(process, "_asm_process_identity", None) != identity:
            return False
        try:
            if process.poll() is not None:
                return False
            verified = _verify_signal_target(identity, run_id)
        except UnverifiedProcessError:
            return False
        except Exception:
            return False
        if verified is None:
            return False
        if process.poll() is not None:
            return False
        # Birth, marker and attempt are revalidated under the lifecycle mutex;
        # Popen's handle keeps this cleanup attached to its original child.
        process.terminate()
        return True


def _resolve_launched_identity(
    process,
    root_identity: ProcessIdentity,
    run_id: str,
    expected_executable: str,
) -> ProcessIdentity:
    """Resolve the expected Arma process during a bounded shell startup window."""
    if getattr(process, "_asm_process_identity", None) != root_identity:
        raise UnverifiedProcessError("Launch identity is not attached to the Popen child")
    readiness_deadline = time.monotonic() + 10.0
    while True:
        try:
            if process.poll() is not None:
                raise UnverifiedProcessError("Popen child exited before launch verification")
        except UnverifiedProcessError:
            raise
        except Exception:
            raise UnverifiedProcessError("Popen child handle could not be checked") from None

        root = get_verified_process(root_identity)
        if root is None:
            raise UnverifiedProcessError("Launched process is no longer present")
        _process_marker(root, root_identity, run_id)
        if _process_executable_matches(root, root_identity, expected_executable):
            root_after = get_verified_process(root_identity)
            if root_after is None or process.poll() is not None:
                raise UnverifiedProcessError("Launched process identity changed")
            _process_marker(root_after, root_identity, run_id)
            if _process_executable_matches(root_after, root_identity, expected_executable):
                return root_identity
        else:
            try:
                children = root.children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
            except Exception:
                raise UnverifiedProcessError(
                    "Launched process children could not be inspected"
                ) from None

            candidates: list[ProcessIdentity] = []
            for child in children:
                try:
                    child_identity = capture_process_identity(child.pid)
                except UnverifiedProcessError:
                    try:
                        psutil.Process(child.pid)
                    except psutil.NoSuchProcess:
                        continue
                    raise UnverifiedProcessError(
                        "Launched child identity could not be captured"
                    ) from None

                child_process = get_verified_process(child_identity)
                if child_process is None:
                    continue
                if not _process_executable_matches(
                    child_process,
                    child_identity,
                    expected_executable,
                ):
                    continue
                _process_marker(child_process, child_identity, run_id)
                if _descendant_depth(child_identity, root_identity, run_id) is not None:
                    candidates.append(child_identity)

            root_after = get_verified_process(root_identity)
            if root_after is None or process.poll() is not None:
                raise UnverifiedProcessError(
                    "Launch parent identity changed during child verification"
                )
            _process_marker(root_after, root_identity, run_id)

            if len(candidates) == 1:
                selected = candidates[0]
                selected_process = get_verified_process(selected)
                if selected_process is not None:
                    _process_marker(selected_process, selected, run_id)
                    if (
                        _process_executable_matches(
                            selected_process,
                            selected,
                            expected_executable,
                        )
                        and _descendant_depth(selected, root_identity, run_id) is not None
                    ):
                        return selected

        remaining = readiness_deadline - time.monotonic()
        if remaining <= 0:
            raise UnverifiedProcessError(
                "Expected Arma process did not become uniquely verifiable before timeout"
            )
        time.sleep(min(0.1, remaining))

@shared_task()
def check_all_servers_status_task():
    """Reconcile lifecycle state without signalling or clearing pending starts."""
    first_error = None
    for instance_id in Instances.objects.values_list("pk", flat=True):
        timeout_task_id = None
        with lifecycle_transaction():
            instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
            if instance is None or instance.lifecycle_state == Instances.LifecycleState.STOPPED:
                continue
            if instance.lifecycle_state == Instances.LifecycleState.STARTING:
                # Admission is already a reservation. A pending or claimed start
                # remains owned by its worker through launch and scheduling.
                continue
            try:
                timeout_task_id = _reconcile_instance_locked(instance)
            except Exception as exc:
                # Save uncertainty in the outer transaction. A nested update
                # followed by re-raising would roll it back with the failure.
                instance.refresh_from_db()
                instance.lifecycle_state = Instances.LifecycleState.UNKNOWN
                instance.is_running = True
                instance.save(update_fields=["lifecycle_state", "is_running"])
                if first_error is None:
                    first_error = exc
    if first_error is not None:
        raise first_error
    return {"status": "Status wszystkich serwerów został zaktualizowany."}

@shared_task(bind=True, max_retries=None)
def instance_timeout_task(self, instance_id: int, run_id: str, shutdown_at: str):
    """Request a verified stop only for the run and deadline this job owns."""
    deadline = parse_datetime(shutdown_at) if isinstance(shutdown_at, str) else None
    if deadline is None or timezone.is_naive(deadline):
        return {"status": "Stare lub nieprawidłowe zadanie limitu czasu."}

    retry_countdown = None
    current_task_id = getattr(getattr(self, "request", None), "id", None)
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        if instance is None or str(instance.run_id) != str(run_id):
            return {"status": "Zadanie limitu czasu jest nieaktualne."}
        now = timezone.now()
        claimed_start_binding = InstanceTask.objects.filter(
            pk=instance.lifecycle_task_id,
            instance=instance,
            kind=InstanceTask.Kind.START,
            claimed_at__isnull=False,
            superseded_at__isnull=True,
        ).exists()
        start_publication_window = (
            instance.lifecycle_state
            in (Instances.LifecycleState.STARTING, Instances.LifecycleState.UNKNOWN)
            and instance.start_claimed_at is not None
            and claimed_start_binding
            and instance.shutdown_at in (None, deadline)
        )
        if start_publication_window:
            # The job can run after publication but before the worker commits
            # its running state and deadline. Once that deadline has elapsed,
            # recover the stalled launch as unknown and let verified stop logic
            # handle it instead of consuming the only timeout delivery.
            if deadline > now:
                retry_countdown = max(1, math.ceil((deadline - now).total_seconds()))
            else:
                instance.lifecycle_state = Instances.LifecycleState.UNKNOWN
                instance.is_running = True
                instance.shutdown_at = deadline
                if not instance.timeout_task_id and current_task_id:
                    instance.timeout_task_id = current_task_id
                instance.save(
                    update_fields=[
                        "lifecycle_state",
                        "is_running",
                        "shutdown_at",
                        "timeout_task_id",
                    ]
                )
        elif (
            instance.shutdown_at != deadline
            or instance.lifecycle_state
            not in (
                Instances.LifecycleState.RUNNING,
                Instances.LifecycleState.STOPPING,
                Instances.LifecycleState.UNKNOWN,
            )
        ):
            return {"status": "Zadanie limitu czasu jest nieaktualne."}
        elif deadline > now:
            retry_countdown = max(1, math.ceil((deadline - now).total_seconds()))

    if retry_countdown is not None:
        return self.retry(countdown=retry_countdown)

    try:
        reservation = reserve_stop(
            instance_id,
            expected_run_id=str(run_id),
            expected_shutdown_at=deadline,
            recover_after_seconds=60,
        )
    except StopReservationRejected as exc:
        if exc.retry_after is not None:
            return self.retry(countdown=max(1, math.ceil(exc.retry_after)))
        return self.retry(countdown=30)

    try:
        dispatch_stop_reservation(reservation, stop_server_task)
    except Exception as exc:
        return self.retry(countdown=60 if reservation.republish else 30, exc=exc)
    # Do not revoke the deadline after merely publishing stop work. It retries
    # until that worker confirms exit, and then becomes a guarded no-op.
    return self.retry(countdown=60 if reservation.republish else 30)


def _finish_start_without_process(instance_id: int, run_id: str, task_id: str) -> None:
    """Release only a matching start reservation when launch created no process."""
    _mark_run_stopped(instance_id, run_id, expected_task_id=task_id)


def _revoke_timeout_task(task_id: str | None) -> None:
    """Revoke only a captured per-run timeout, never terminating its worker."""
    if not task_id:
        return
    try:
        celery_app.control.revoke(task_id, terminate=False)
    except Exception:
        # The timeout's run/deadline guard remains authoritative if revocation
        # cannot be confirmed.
        return


def _persist_started_identity(
    instance_id: int,
    run_id: str,
    task_id: str,
    identity: ProcessIdentity,
    executable: str,
) -> bool:
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        if (
            instance is None
            or str(instance.run_id) != run_id
            or instance.lifecycle_task_id != task_id
            or instance.lifecycle_state != Instances.LifecycleState.STARTING
        ):
            return False
        instance.pid = identity.pid
        instance.process_created_at = identity.created_at
        instance.process_executable = executable
        instance.is_running = True
        instance.save(
            update_fields=[
                "pid",
                "process_created_at",
                "process_executable",
                "is_running",
            ]
        )
        return True


def _clear_run_fields(instance: Instances) -> None:
    instance.lifecycle_state = Instances.LifecycleState.STOPPED
    instance.is_running = False
    instance.pid = None
    instance.run_id = None
    instance.process_created_at = None
    instance.process_executable = None
    instance.lifecycle_task_id = None
    instance.start_claimed_at = None
    instance.timeout_task_id = None
    instance.shutdown_at = None


def _supersede_unfinished_lifecycle_bindings(
    instance_id: int,
    *,
    current_task_id: str | None,
) -> None:
    """Retire orphan lifecycle work only after this run's exit is confirmed."""
    bindings = InstanceTask.objects.filter(
        instance_id=instance_id,
        kind__in=(InstanceTask.Kind.START, InstanceTask.Kind.STOP),
        finished_at__isnull=True,
        superseded_at__isnull=True,
    )
    if current_task_id:
        bindings = bindings.exclude(pk=current_task_id)
    bindings.update(superseded_at=timezone.now())


def _mark_run_stopped(
    instance_id: int,
    run_id: str,
    *,
    expected_task_id: str | None = None,
    expected_timeout_task_id: str | None = None,
) -> str | None:
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        if (
            instance is None
            or str(instance.run_id) != str(run_id)
            or (
                expected_task_id is not None
                and instance.lifecycle_task_id != expected_task_id
            )
        ):
            return None
        if expected_task_id:
            current_binding = InstanceTask.objects.select_for_update().filter(
                pk=expected_task_id,
                instance=instance,
            ).first()
            if current_binding is not None:
                if current_binding.superseded_at is not None:
                    return None
                if (
                    current_binding.kind == InstanceTask.Kind.STOP
                    and current_binding.claimed_at is None
                ):
                    return None
        timeout_task_id = instance.timeout_task_id or expected_timeout_task_id
        _supersede_unfinished_lifecycle_bindings(
            instance.pk,
            current_task_id=expected_task_id,
        )
        _revoke_timeout_task(timeout_task_id)
        _clear_run_fields(instance)
        instance.save(
            update_fields=[
                "lifecycle_state",
                "is_running",
                "pid",
                "run_id",
                "process_created_at",
                "process_executable",
                "lifecycle_task_id",
                "start_claimed_at",
                "timeout_task_id",
                "shutdown_at",
            ]
        )
        return timeout_task_id or ""


def _mark_run_unknown(
    instance_id: int,
    run_id: str,
    task_id: str | None,
    *,
    pid=...,
    process_created_at=...,
    process_executable=...,
    shutdown_at=...,
    timeout_task_id=...,
    may_be_live: bool,
    clear_deadline: bool = False,
) -> None:
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        if (
            instance is None
            or (run_id and str(instance.run_id) != str(run_id))
            or (task_id is not None and instance.lifecycle_task_id != task_id)
        ):
            return
        if task_id:
            binding = InstanceTask.objects.select_for_update().filter(
                pk=task_id,
                instance=instance,
            ).first()
            if binding is not None and binding.superseded_at is not None:
                return
        instance.lifecycle_state = Instances.LifecycleState.UNKNOWN
        if pid is not ...:
            instance.pid = pid
        if process_created_at is not ...:
            instance.process_created_at = process_created_at
        if process_executable is not ...:
            instance.process_executable = process_executable
        if may_be_live:
            instance.is_running = True
        if clear_deadline:
            instance.shutdown_at = None
            instance.timeout_task_id = None
        if shutdown_at is not ...:
            instance.shutdown_at = shutdown_at
        if timeout_task_id is not ...:
            instance.timeout_task_id = timeout_task_id
        instance.save()


def _finish_start_without_identity(
    instance_id: int,
    run_id: str,
    task_id: str,
    process,
    executable: str,
) -> None:
    _mark_run_unknown(
        instance_id,
        run_id,
        task_id,
        pid=process.pid,
        process_created_at=None,
        process_executable=executable,
        may_be_live=True,
        clear_deadline=True,
    )


def _compensate_started_process(
    instance_id: int,
    run_id: str,
    task_id: str,
    *,
    signal_guard,
    deadline=None,
    timeout_task_id: str | None = None,
) -> bool:
    instance = _require_current_start_attempt(instance_id, run_id, task_id)
    try:
        _stop_verified_instance(instance, run_id, signal_guard=signal_guard)
    except Ignore:
        raise
    except Exception:
        _mark_run_unknown(
            instance_id,
            run_id,
            task_id,
            shutdown_at=deadline if timeout_task_id else ...,
            timeout_task_id=timeout_task_id if timeout_task_id else ...,
            may_be_live=True,
            clear_deadline=not bool(timeout_task_id),
        )
        raise
    return _mark_run_stopped(
        instance_id,
        run_id,
        expected_task_id=task_id,
        expected_timeout_task_id=timeout_task_id,
    ) is not None


def _compensate_unpersisted_launch(
    instance: Instances,
    identity: ProcessIdentity,
    run_id: str,
    task_id: str,
    *,
    signal_guard,
) -> None:
    """Use the captured attempt identity even when its DB write failed."""
    instance = _require_current_start_attempt(instance.pk, run_id, task_id)
    instance.pid = identity.pid
    instance.process_created_at = identity.created_at
    instance.is_running = True
    try:
        _stop_verified_instance(instance, run_id, signal_guard=signal_guard)
    except Ignore:
        raise
    except Exception:
        _mark_run_unknown(
            instance.pk,
            run_id,
            task_id,
            pid=identity.pid,
            process_created_at=identity.created_at,
            process_executable=instance.process_executable,
            may_be_live=True,
            clear_deadline=True,
        )
        raise
    _mark_run_stopped(instance.pk, run_id, expected_task_id=task_id)


def _terminate_launched_identity(
    instance: Instances,
    identity: ProcessIdentity,
    run_id: str,
    *,
    task_id: str,
    signal_guard,
) -> None:
    """Compensate a launch using its captured identity and run snapshot."""
    instance = _require_current_start_attempt(instance.pk, run_id, task_id)
    instance.pid = identity.pid
    instance.process_created_at = identity.created_at
    instance.is_running = True
    _stop_verified_instance(instance, run_id, signal_guard=signal_guard)


def _process_marker(process, identity: ProcessIdentity, run_id: str) -> bool:
    try:
        environment = process.environ()
    except psutil.NoSuchProcess:
        if get_verified_process(identity) is None:
            return False
        raise UnverifiedProcessError("Process run marker could not be read") from None
    except Exception:
        raise UnverifiedProcessError("Process run marker could not be read") from None
    if not isinstance(environment, Mapping):
        raise UnverifiedProcessError("Process run marker could not be verified")
    if environment.get(RUN_ID_ENVIRONMENT_KEY) != run_id:
        raise UnverifiedProcessError("Process run marker no longer matches")
    return True


def _process_executable_matches(process, identity: ProcessIdentity, expected: str) -> bool:
    try:
        executable = process.exe()
    except psutil.NoSuchProcess:
        if get_verified_process(identity) is None:
            return False
        raise UnverifiedProcessError("Process executable could not be read") from None
    except Exception:
        raise UnverifiedProcessError("Process executable could not be verified") from None
    return os.path.normcase(os.path.realpath(executable)) == os.path.normcase(
        os.path.realpath(expected)
    )


def _descendant_depth(
    identity: ProcessIdentity,
    root_identity: ProcessIdentity,
    run_id: str,
) -> int | None:
    process = get_verified_process(identity)
    if process is None:
        return None
    if not _process_marker(process, identity, run_id):
        return None

    depth = 0
    current = process
    seen = {identity.pid}
    while depth < 128:
        try:
            parent = current.parent()
        except psutil.NoSuchProcess:
            return None
        except Exception:
            raise UnverifiedProcessError("Child process relationship could not be verified") from None
        if parent is None:
            return None
        try:
            parent_identity = capture_process_identity(parent.pid)
        except Exception:
            raise UnverifiedProcessError("Parent process identity could not be captured") from None
        verified_parent = get_verified_process(parent_identity)
        if verified_parent is None:
            return None
        depth += 1
        if parent_identity == root_identity:
            if parent.pid in seen:
                raise UnverifiedProcessError("Child process relationship is cyclic")
            return depth
        if parent.pid in seen:
            raise UnverifiedProcessError("Child process relationship is cyclic")
        seen.add(parent.pid)
        current = verified_parent
    raise UnverifiedProcessError("Child process relationship is too deep to verify")


def _verify_signal_target(
    identity: ProcessIdentity,
    run_id: str,
    *,
    root_identity: ProcessIdentity | None = None,
    expected_executable: str | None = None,
):
    process = get_verified_process(identity)
    if process is None:
        return None
    if not _process_marker(process, identity, run_id):
        return None
    if expected_executable and not _process_executable_matches(
        process, identity, expected_executable
    ):
        return None
    if root_identity is not None:
        depth = _descendant_depth(identity, root_identity, run_id)
        if depth is None:
            if get_verified_process(identity) is None:
                return None
            raise UnverifiedProcessError("Child process relationship no longer matches")
    return process


def _wait_until_gone(process, identity: ProcessIdentity) -> bool:
    try:
        process.wait(timeout=30)
    except psutil.TimeoutExpired:
        process = get_verified_process(identity)
        if process is None:
            return True
        raise
    except psutil.NoSuchProcess:
        return get_verified_process(identity) is None
    except Exception:
        raise UnverifiedProcessError("Process exit could not be verified") from None
    return get_verified_process(identity) is None


def _terminate_identity(
    identity: ProcessIdentity,
    run_id: str,
    *,
    root_identity: ProcessIdentity | None = None,
    expected_executable: str | None = None,
    signal_guard,
) -> bool:
    process = _verify_signal_target(
        identity,
        run_id,
        root_identity=root_identity,
        expected_executable=expected_executable,
    )
    if process is None:
        return get_verified_process(identity) is None
    try:
        if not signal_guard(
            identity,
            "terminate",
            root_identity=root_identity,
            expected_executable=expected_executable,
        ):
            return get_verified_process(identity) is None
    except Ignore:
        raise
    except psutil.NoSuchProcess:
        return get_verified_process(identity) is None
    except Exception:
        raise UnverifiedProcessError("Verified process could not be terminated") from None

    try:
        exited = _wait_until_gone(process, identity)
    except psutil.TimeoutExpired:
        process = _verify_signal_target(
            identity,
            run_id,
            root_identity=root_identity,
            expected_executable=expected_executable,
        )
        if process is None:
            return get_verified_process(identity) is None
        try:
            if not signal_guard(
                identity,
                "kill",
                root_identity=root_identity,
                expected_executable=expected_executable,
            ):
                return get_verified_process(identity) is None
        except Ignore:
            raise
        except psutil.NoSuchProcess:
            return get_verified_process(identity) is None
        except Exception:
            raise UnverifiedProcessError("Verified process could not be killed") from None
        try:
            exited = _wait_until_gone(process, identity)
        except psutil.TimeoutExpired:
            return False
    return exited and get_verified_process(identity) is None


def _terminate_process_tree(
    root_identity: ProcessIdentity,
    run_id: str,
    *,
    expected_executable: str | None,
    signal_guard,
) -> bool:
    root = _verify_signal_target(
        root_identity,
        run_id,
        expected_executable=expected_executable,
    )
    if root is None:
        return get_verified_process(root_identity) is None

    try:
        children = root.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    except Exception:
        raise UnverifiedProcessError("Child process discovery could not be completed") from None

    child_identities = set()
    for child in children:
        try:
            child_identities.add(capture_process_identity(child.pid))
        except UnverifiedProcessError:
            try:
                psutil.Process(child.pid)
            except psutil.NoSuchProcess:
                continue
            except Exception:
                raise UnverifiedProcessError(
                    "Child process identity could not be captured"
                ) from None
            raise UnverifiedProcessError(
                "Child process identity could not be captured"
            ) from None
    descendants: list[tuple[int, ProcessIdentity]] = []
    for child_identity in child_identities:
        depth = _descendant_depth(child_identity, root_identity, run_id)
        if depth is not None:
            descendants.append((depth, child_identity))
        elif get_verified_process(child_identity) is not None:
            candidate = get_verified_process(child_identity)
            if candidate is not None:
                _process_marker(candidate, child_identity, run_id)
            raise UnverifiedProcessError("Child process ownership is uncertain")

    for _, child_identity in sorted(descendants, key=lambda item: item[0], reverse=True):
        if not _terminate_identity(
            child_identity,
            run_id,
            root_identity=root_identity,
            signal_guard=signal_guard,
        ):
            raise UnverifiedProcessError("An owned child process did not exit")

    if not _terminate_identity(
        root_identity,
        run_id,
        expected_executable=expected_executable,
        signal_guard=signal_guard,
    ):
        return False
    return True


def _stop_verified_instance(
    instance: Instances,
    run_id: str,
    *,
    signal_guard,
) -> None:
    """Stop verified run roots and require stable independent absence evidence."""
    if not instance.process_executable or not instance.port_id:
        raise UnverifiedProcessError("Instance process metadata is incomplete")
    port_number = instance.port.port_number
    expected_executable = instance.process_executable
    direct_identity = None
    if instance.pid is not None or instance.process_created_at is not None:
        if instance.pid is None or instance.process_created_at is None:
            raise UnverifiedProcessError("Persisted process identity is incomplete")
        direct_identity = ProcessIdentity(instance.pid, instance.process_created_at)

    direct_process = (
        get_verified_process(direct_identity) if direct_identity is not None else None
    )
    initial_udp = find_udp_processes(port_number, run_id, expected_executable)
    roots: dict[ProcessIdentity, str | None] = {}
    if direct_process is not None and direct_identity is not None:
        roots[direct_identity] = None
    for identity in initial_udp:
        roots[identity] = expected_executable
    if not roots and direct_identity is None:
        raise UnverifiedProcessError("No process identity confirms this run")

    for identity, executable in roots.items():
        if not _terminate_process_tree(
            identity,
            run_id,
            expected_executable=executable,
            signal_guard=signal_guard,
        ):
            raise UnverifiedProcessError("An owned server process did not exit")

    if direct_identity is not None and get_verified_process(direct_identity) is not None:
        raise UnverifiedProcessError("The recorded server process is still present")

    first_observation = find_udp_processes(port_number, run_id, expected_executable)
    time.sleep(0.1)
    second_observation = find_udp_processes(port_number, run_id, expected_executable)
    if first_observation != second_observation or second_observation:
        raise UnverifiedProcessError("UDP process observations do not confirm shutdown")
    if any(get_verified_process(identity) is not None for identity in roots):
        raise UnverifiedProcessError("An owned server process remains live")
    if direct_identity is None and not initial_udp:
        raise UnverifiedProcessError("Socket absence alone does not confirm shutdown")


def _reconcile_instance_locked(instance: Instances) -> str | None:
    """Reconcile one locked row without signalling processes."""
    run_id = str(instance.run_id) if instance.run_id else None
    if not run_id or not instance.process_executable:
        return None
    if instance.pid is not None and instance.process_created_at is not None:
        identity = ProcessIdentity(instance.pid, instance.process_created_at)
        process = get_verified_process(identity)
        if process is not None:
            _process_marker(process, identity, run_id)
            if instance.lifecycle_state == Instances.LifecycleState.STARTING:
                if instance.timeout_task_id and instance.shutdown_at:
                    instance.lifecycle_state = Instances.LifecycleState.RUNNING
                else:
                    instance.lifecycle_state = Instances.LifecycleState.UNKNOWN
            instance.is_running = True
            instance.save(update_fields=["lifecycle_state", "is_running"])
            return None
        direct_identity_gone = True
    else:
        identity = None
        direct_identity_gone = False

    udp = find_udp_processes(
        instance.port.port_number,
        run_id,
        instance.process_executable,
    )
    if len(udp) == 1:
        candidate = udp[0]
        instance.pid = candidate.pid
        instance.process_created_at = candidate.created_at
        if instance.lifecycle_state == Instances.LifecycleState.STARTING:
            if instance.timeout_task_id and instance.shutdown_at:
                instance.lifecycle_state = Instances.LifecycleState.RUNNING
            else:
                instance.lifecycle_state = Instances.LifecycleState.UNKNOWN
        instance.is_running = True
        instance.save(
            update_fields=[
                "pid",
                "process_created_at",
                "lifecycle_state",
                "is_running",
            ]
        )
        return None
    if udp:
        instance.lifecycle_state = Instances.LifecycleState.UNKNOWN
        instance.is_running = True
        instance.save(update_fields=["lifecycle_state", "is_running"])
        return None

    first_observation = find_udp_processes(
        instance.port.port_number,
        run_id,
        instance.process_executable,
    )
    time.sleep(0.1)
    second_observation = find_udp_processes(
        instance.port.port_number,
        run_id,
        instance.process_executable,
    )
    if first_observation != second_observation or second_observation:
        instance.lifecycle_state = Instances.LifecycleState.UNKNOWN
        instance.is_running = True
        instance.save(update_fields=["lifecycle_state", "is_running"])
        return None
    if direct_identity_gone:
        _supersede_unfinished_lifecycle_bindings(
            instance.pk,
            current_task_id=None,
        )
        _revoke_timeout_task(instance.timeout_task_id)
        _clear_run_fields(instance)
        instance.save()
        return None
    return None
