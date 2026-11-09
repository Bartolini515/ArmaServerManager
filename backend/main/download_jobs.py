"""Durable admission and worker entrypoints for shared Steam downloads."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import unquote, urlsplit

from celery.exceptions import Ignore
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from redis import Redis
from redis.backoff import NoBackoff
from redis.connection import BlockingConnectionPool
from redis.retry import Retry

from .download_leases import (
    AtomicDownloadLease,
    BoundedConnection,
    BoundedSSLConnection,
    LeaseConfigurationError,
    LeaseLostError,
    LeaseUnavailableError,
)
from .instance_lifecycle import lifecycle_transaction, require_lifecycle_autocommit
from .instance_tasks import create_instance_task_binding
from .models import InstanceTask, Instances
from .modpreset.modpathing import check_installed
from .modpreset.preset_extraction import preset_parser
from .steamcmd.mods_download import download_mods
from .steamcmd.processes import CleanupUncertainError
from .utils.config import config
from .utils.operation_logging import operation_loggers
from .utils.steam_errors import SteamCommandError, SteamSecretRedactor, safe_steam_diagnostic


class DownloadAdmissionError(RuntimeError):
    """A download cannot be admitted because its durable owner is unsafe."""

    _MESSAGES = {
        "not_found": "Instancja nie istnieje.",
        "not_stopped": "Serwer musi być zatrzymany przed pobraniem modów.",
        "busy": "Inna operacja serwera lub pobierania jest już w toku.",
    }
    _HTTP_STATUS = {
        "not_found": 404,
        "not_stopped": 409,
        "busy": 409,
    }

    def __init__(self, code: str):
        if code not in self._MESSAGES:
            code = "busy"
        self.code = code
        self.status_code = self._HTTP_STATUS[code]
        super().__init__(self._MESSAGES[code])


class DownloadLeasePreparationError(RuntimeError):
    """Static failure while preparing the dedicated Redis lease client."""

    code = "download_lease_unavailable"

    def __init__(self):
        super().__init__("Nie można przygotować blokady pobierania modów.")


def reserve_download(
    instance_id: int,
    *,
    preflight: Callable[[], None] | None = None,
):
    """Reserve or reuse a committed download binding under the global mutex.

    New-admission preflight runs only after reuse and conflict decisions, while
    the lifecycle mutex still serializes the decision. A failed preflight exits
    the transaction before any binding is created.
    """
    require_lifecycle_autocommit()
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        if instance is None:
            raise DownloadAdmissionError("not_found")

        same_instance = InstanceTask.objects.select_for_update().filter(
            instance=instance,
            kind=InstanceTask.Kind.DOWNLOAD,
            finished_at__isnull=True,
            superseded_at__isnull=True,
        ).order_by("created_at", "task_id").first()
        if same_instance is not None:
            return same_instance, True

        if (
            instance.lifecycle_state != Instances.LifecycleState.STOPPED
            or instance.is_running
            or instance.pid is not None
        ):
            raise DownloadAdmissionError("not_stopped")

        if InstanceTask.objects.filter(
            kind=InstanceTask.Kind.DOWNLOAD,
            finished_at__isnull=True,
            superseded_at__isnull=True,
        ).exists():
            raise DownloadAdmissionError("busy")

        if InstanceTask.objects.filter(
            instance=instance,
            finished_at__isnull=True,
            superseded_at__isnull=True,
        ).exists():
            raise DownloadAdmissionError("busy")

        if preflight is not None:
            preflight()

        binding = create_instance_task_binding(instance, InstanceTask.Kind.DOWNLOAD)
        return binding, False


def build_download_redis_client(
    location: str | None = None,
    *,
    resolver: Callable[..., Any] | None = None,
):
    """Build one dedicated bounded Redis client without shared cache hooks."""
    if location is None:
        location = _configured_download_redis_location()
    if type(location) is not str:
        raise DownloadLeasePreparationError() from None

    parsed = _parse_download_redis_location(location)
    scheme, host, port, database, username, password = parsed
    numeric_host = _resolve_numeric_host(host, port, resolver=resolver)
    use_tls = scheme == "rediss"

    connection_options: dict[str, Any] = {
        "host": host,
        "numeric_host": numeric_host,
        "port": port,
        "db": database,
        "socket_connect_timeout": 1,
        "socket_timeout": 1,
        "retry": Retry(NoBackoff(), 0),
        "encoding": "utf-8",
        "encoding_errors": "strict",
        "health_check_interval": 0,
    }
    if username is not None:
        connection_options["username"] = username
    if password is not None:
        connection_options["password"] = password

    connection_class = BoundedConnection
    if use_tls:
        connection_class = BoundedSSLConnection
        connection_options.update(
            {
                "ssl_cert_reqs": "required",
                "ssl_check_hostname": True,
                "ssl_validate_ocsp": False,
                "ssl_validate_ocsp_stapled": False,
            }
        )

    try:
        pool = BlockingConnectionPool(
            connection_class=connection_class,
            max_connections=4,
            timeout=1,
            **connection_options,
        )
        client = Redis(connection_pool=pool, single_connection_client=False)
    except Exception:
        raise DownloadLeasePreparationError() from None
    return client


def _configured_download_redis_location() -> str:
    try:
        cache_config = settings.CACHES["default"]
        if type(cache_config) is not dict:
            raise ValueError
        supported_top_level = {
            "BACKEND",
            "LOCATION",
            "OPTIONS",
            "KEY_FUNCTION",
            "KEY_PREFIX",
            "TIMEOUT",
            "VERSION",
        }
        if set(cache_config) - supported_top_level:
            raise ValueError
        if cache_config.get("BACKEND") != "django_redis.cache.RedisCache":
            raise ValueError
        if cache_config.get("KEY_FUNCTION") is not None:
            raise ValueError
        if "KEY_PREFIX" in cache_config and type(cache_config["KEY_PREFIX"]) is not str:
            raise ValueError
        if "VERSION" in cache_config and type(cache_config["VERSION"]) is not int:
            raise ValueError
        timeout = cache_config.get("TIMEOUT")
        if timeout is not None and type(timeout) not in (int, float):
            raise ValueError
        options = cache_config.get("OPTIONS", {})
        if type(options) is not dict:
            raise ValueError
        if set(options) - {"CLIENT_CLASS"}:
            raise ValueError
        if options.get("CLIENT_CLASS", "django_redis.client.DefaultClient") != (
            "django_redis.client.DefaultClient"
        ):
            raise ValueError
        location = cache_config.get("LOCATION")
        if type(location) is not str:
            raise ValueError
        return location
    except Exception:
        raise DownloadLeasePreparationError() from None


def _parse_download_redis_location(
    location: str,
) -> tuple[str, str, int, int, str | None, str | None]:
    try:
        parsed = urlsplit(location)
        if parsed.scheme not in {"redis", "rediss"}:
            raise ValueError
        if "?" in location or "#" in location or not parsed.netloc:
            raise ValueError
        host = parsed.hostname
        if (
            type(host) is not str
            or not host
            or any(character in host for character in (",", "/", "\\", "%"))
        ):
            raise ValueError
        parsed_port = parsed.port
        port = parsed_port if parsed_port is not None else 6379
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError
        raw_database = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
        if raw_database == "":
            database = 0
        elif raw_database.isascii() and raw_database.isdecimal():
            database = int(raw_database)
        else:
            raise ValueError
        username = unquote(parsed.username) if parsed.username is not None else None
        password = unquote(parsed.password) if parsed.password is not None else None
        return parsed.scheme, host, port, database, username, password
    except Exception:
        raise DownloadLeasePreparationError() from None


def _resolve_numeric_host(
    host: str,
    port: int,
    *,
    resolver: Callable[..., Any] | None,
) -> str:
    if "%" in host:
        raise DownloadLeasePreparationError() from None
    try:
        address = ipaddress.ip_address(host)
        return address.compressed
    except ValueError:
        pass

    resolve = resolver or socket.getaddrinfo
    try:
        results = resolve(host, port, type=socket.SOCK_STREAM)
        for result in results:
            if not isinstance(result, (tuple, list)) or len(result) < 5:
                continue
            family = result[0]
            sockaddr = result[4]
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            if not isinstance(sockaddr, (tuple, list)) or not sockaddr:
                continue
            candidate = sockaddr[0]
            if type(candidate) is not str or "%" in candidate:
                continue
            try:
                numeric = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if (family == socket.AF_INET and numeric.version == 4) or (
                family == socket.AF_INET6 and numeric.version == 6
            ):
                return numeric.compressed
    except Exception:
        raise DownloadLeasePreparationError() from None
    raise DownloadLeasePreparationError() from None


def _validate_current_preset(instance, config_snapshot: dict[str, Any]) -> str:
    """Load the read-only preset validator lazily to avoid import cycles."""
    from .instance_management import validate_instance_preset

    return validate_instance_preset(instance, config_snapshot)


def run_download_job(
    task,
    instance_id: int,
    name: str | None = None,
    user: str | None = None,
    file_path: str | None = None,
    mods_directory: str | None = None,
):
    """Claim and run one durable download using the instance's current state."""
    del name, user, file_path, mods_directory
    task_id = _task_request_id(task)
    if task_id is None:
        raise Ignore()

    instance, binding = _claim_download(task_id, instance_id)
    try:
        owner_username = instance.user.username
        snapshot = config.snapshot()
        paths = _required_paths(snapshot)
        preset_path = _validate_current_preset(instance, snapshot)
        if type(preset_path) is not str or not preset_path or not _is_absolute_path(preset_path):
            raise SteamCommandError("Nie udało się odczytać aktualnego presetu instancji.")
        steam_auth = snapshot.get("steam_auth", {}) if isinstance(snapshot, Mapping) else {}
        if not isinstance(steam_auth, Mapping):
            raise SteamCommandError("Nie udało się przygotować konfiguracji pobierania modów.")
        redactor = SteamSecretRedactor(
            (
                _credential_text(steam_auth.get("username")),
                _credential_text(steam_auth.get("password")),
                _credential_text(steam_auth.get("shared_secret")),
            )
        )
        lease = _new_download_lease()
    except Ignore:
        raise
    except Exception as exc:
        raise _safe_worker_error(exc, None) from None

    acquired = False
    try:
        try:
            acquired = lease.acquire()
        except (LeaseConfigurationError, LeaseLostError, LeaseUnavailableError):
            raise DownloadAdmissionError("busy") from None
        except Exception:
            raise DownloadAdmissionError("busy") from None
        if not acquired:
            raise DownloadAdmissionError("busy")

        try:
            lease.start_heartbeat()
        except Exception:
            raise DownloadAdmissionError("busy") from None

        _best_effort_progress(task, lease, task_id, instance_id, "Rozpoczynanie pobierania...")

        cleanup_pending = False
        process_state_uncertain = False
        try:
            _assert_current_claim(task_id, instance_id, lease)
            with operation_loggers(
                paths["logs_directory"],
                owner_username,
                ("operations", "download"),
                grouped=True,
                secret_redactor=redactor,
                defer_close_on_exception=lambda error: isinstance(error, Ignore),
            ) as loggers:
                operations_logger = loggers["operations"]
                download_logger = loggers["download"]
                safe_operations_log = _guarded_callback(
                    operations_logger.info,
                    task_id,
                    instance_id,
                    lease,
                )
                safe_download_log = _guarded_callback(
                    download_logger.info,
                    task_id,
                    instance_id,
                    lease,
                )
                safe_download_error = _guarded_callback(
                    download_logger.error,
                    task_id,
                    instance_id,
                    lease,
                )

                try:
                    workshop_ids = preset_parser(
                        preset_path,
                        log_callback=safe_operations_log,
                    )
                    _assert_current_claim(task_id, instance_id, lease)
                    _installed_before, missing_before = check_installed(
                        wids=workshop_ids,
                        mods_dir=paths["mods_directory"],
                        log_callback=safe_operations_log,
                    )
                    if missing_before:
                        def progress_callback(current: int, total: int) -> None:
                            _best_effort_progress(
                                task,
                                lease,
                                task_id,
                                instance_id,
                                f"Pobieranie modów... {current}/{total}",
                            )

                        def download_error_callback(_message: str) -> None:
                            safe_download_error("SteamCMD reported a download error.")

                        process_state_uncertain = True
                        download_result = download_mods(
                            mods_to_download=list(missing_before),
                            name=instance.name,
                            log_callback=safe_download_log,
                            error_callback=download_error_callback,
                            progress_callback=progress_callback,
                            secret_redactor=redactor,
                            config_snapshot=snapshot,
                            cancel_check=lambda: lease.is_lost,
                            before_shared_write=lambda: _assert_current_claim(
                                task_id,
                                instance_id,
                                lease,
                            ),
                        )
                        process_state_uncertain = False
                        failed_mods = list(download_result or ())
                        cleanup_pending = bool(
                            getattr(download_result, "cleanup_pending", False)
                        )
                        if failed_mods:
                            raise SteamCommandError(
                                "Nie udało się pobrać wszystkich wymaganych modów.",
                                diagnostic="Steam workshop download reported failed items",
                            )

                    _assert_current_claim(task_id, instance_id, lease)
                    _paths_after, missing_after = check_installed(
                        wids=workshop_ids,
                        mods_dir=paths["mods_directory"],
                        log_callback=safe_operations_log,
                    )
                    if missing_after:
                        raise SteamCommandError(
                            "Nie udało się zweryfikować wszystkich wymaganych modów.",
                            diagnostic="Steam workshop content verification failed",
                        )
                except CleanupUncertainError as exc:
                    try:
                        _persist_attention(
                            task_id,
                            instance_id,
                            reason=InstanceTask.AttentionReason.PROCESS_CLEANUP_UNCONFIRMED,
                            workspace=getattr(exc, "workspace_path", None),
                        )
                    except BaseException:
                        # The claimed unfinished row remains the fence even if
                        # recording its attention marker is unavailable.
                        pass
                    raise Ignore() from None
                except (Ignore, DownloadAdmissionError):
                    raise
                except Exception as exc:
                    diagnostic = safe_steam_diagnostic(exc, redactor)
                    if diagnostic:
                        safe_download_error("Steam download diagnostic: %s", diagnostic)
                    safe_download_error("Mod download task failed.")
                    raise
                except BaseException:
                    if process_state_uncertain:
                        try:
                            _persist_attention(
                                task_id,
                                instance_id,
                                reason=InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
                                workspace=None,
                            )
                        except BaseException:
                            pass
                        raise Ignore() from None
                    raise

            _persist_ready(
                task_id,
                instance_id,
                ready=True,
                lease=lease,
            )
            _best_effort_progress(
                task,
                lease,
                task_id,
                instance_id,
                "Pobieranie zakończone pomyślnie!",
            )
            result = {"status": "Pobieranie zakończone pomyślnie!"}
            if cleanup_pending:
                result["cleanup_pending"] = True
                result["cleanup_reason"] = "filesystem_cleanup_pending"
            return result
        except CleanupUncertainError as exc:
            _persist_attention(
                task_id,
                instance_id,
                reason=InstanceTask.AttentionReason.PROCESS_CLEANUP_UNCONFIRMED,
                workspace=getattr(exc, "workspace_path", None),
            )
            raise Ignore()
        except Ignore:
            raise
        except DownloadAdmissionError:
            raise
        except Exception as exc:
            raise _safe_worker_error(exc, redactor) from None
        except BaseException:
            if process_state_uncertain:
                _persist_attention(
                    task_id,
                    instance_id,
                    reason=InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
                    workspace=None,
                )
                raise Ignore() from None
            raise
    except Ignore:
        raise
    finally:
        if acquired:
            try:
                lease.release()
            except Exception:
                # Release is owner-token guarded and never authorizes a new DB claim.
                pass


def _task_request_id(task) -> str | None:
    try:
        task_id = getattr(getattr(task, "request", None), "id", None)
        return str(task_id) if task_id else None
    except Exception:
        return None


def _claim_download(task_id: str, instance_id: int):
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        if instance is None:
            raise Ignore()
        binding = InstanceTask.objects.select_for_update().filter(pk=task_id).first()
        if (
            binding is None
            or binding.instance_id != instance.pk
            or binding.kind != InstanceTask.Kind.DOWNLOAD
            or binding.finished_at is not None
            or binding.superseded_at is not None
            or binding.requires_attention
        ):
            raise Ignore()
        if binding.claimed_at is not None:
            raise Ignore()
        if (
            instance.lifecycle_state != Instances.LifecycleState.STOPPED
            or instance.is_running
            or instance.pid is not None
        ):
            raise DownloadAdmissionError("not_stopped")
        if _other_download_fence_exists(binding):
            raise DownloadAdmissionError("busy")
        if InstanceTask.objects.filter(
            instance=instance,
            finished_at__isnull=True,
            superseded_at__isnull=True,
        ).exclude(pk=binding.pk).exclude(kind=InstanceTask.Kind.DOWNLOAD).exists():
            raise DownloadAdmissionError("busy")

        binding.claimed_at = timezone.now()
        binding.save(update_fields=["claimed_at"])
        if instance.is_ready:
            instance.is_ready = False
            instance.save(update_fields=["is_ready"])
        return instance, binding


def _required_paths(snapshot: object) -> dict[str, str]:
    if not isinstance(snapshot, Mapping):
        raise SteamCommandError("Nie udało się przygotować konfiguracji pobierania modów.")
    paths = snapshot.get("paths")
    if not isinstance(paths, Mapping):
        raise SteamCommandError("Nie udało się przygotować konfiguracji pobierania modów.")
    result = {}
    for name in ("logs_directory", "mods_directory", "steamcmd", "download_directory"):
        value = paths.get(name)
        if type(value) is not str or not value.strip() or not _is_absolute_path(value):
            raise SteamCommandError("Nie udało się przygotować konfiguracji pobierania modów.")
        result[name] = value
    return result


def _credential_text(value: object) -> str:
    return value if type(value) is str else ""


def _is_absolute_path(path: str) -> bool:
    import os

    return os.path.isabs(path)


def _new_download_lease():
    try:
        return AtomicDownloadLease(
            build_download_redis_client(),
            key="asm:steam-download:v1",
            ttl_seconds=120,
            heartbeat_seconds=30,
        )
    except DownloadLeasePreparationError:
        raise
    except Exception:
        raise DownloadLeasePreparationError() from None


def _other_download_fence_exists(binding: InstanceTask) -> bool:
    """Return whether another active download is claimed or attention-fenced."""
    return (
        InstanceTask.objects.filter(
            kind=InstanceTask.Kind.DOWNLOAD,
            finished_at__isnull=True,
            superseded_at__isnull=True,
        )
        .exclude(pk=binding.pk)
        .filter(Q(claimed_at__isnull=False) | Q(requires_attention=True))
        .exists()
    )


def _assert_current_claim(
    task_id: str,
    instance_id: int,
    lease: AtomicDownloadLease,
) -> None:
    with lifecycle_transaction():
        instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
        binding = InstanceTask.objects.select_for_update().filter(pk=task_id).first()
        if (
            instance is None
            or binding is None
            or binding.instance_id != instance_id
            or binding.kind != InstanceTask.Kind.DOWNLOAD
            or binding.claimed_at is None
            or binding.finished_at is not None
            or binding.superseded_at is not None
            or binding.requires_attention
            or instance.lifecycle_state != Instances.LifecycleState.STOPPED
            or instance.is_running
            or instance.pid is not None
        ):
            raise DownloadAdmissionError("busy")
        if _other_download_fence_exists(binding):
            raise DownloadAdmissionError("busy")
    lease.assert_owned()


def _persist_ready(
    task_id: str,
    instance_id: int,
    *,
    ready: bool,
    lease: AtomicDownloadLease,
) -> None:
    try:
        with lifecycle_transaction():
            instance = Instances.objects.select_for_update().filter(pk=instance_id).first()
            binding = InstanceTask.objects.select_for_update().filter(pk=task_id).first()
            if (
                instance is None
                or binding is None
                or binding.instance_id != instance_id
                or binding.kind != InstanceTask.Kind.DOWNLOAD
                or binding.claimed_at is None
                or binding.finished_at is not None
                or binding.superseded_at is not None
                or binding.requires_attention
                or instance.lifecycle_state != Instances.LifecycleState.STOPPED
                or instance.is_running
                or instance.pid is not None
            ):
                raise DownloadAdmissionError("busy")
            if _other_download_fence_exists(binding):
                raise DownloadAdmissionError("busy")
            lease.assert_owned()
            instance.is_ready = ready
            instance.save(update_fields=["is_ready"])
    except DownloadAdmissionError:
        raise
    except (LeaseConfigurationError, LeaseLostError, LeaseUnavailableError):
        raise DownloadAdmissionError("busy") from None
    except Exception:
        _persist_attention(
            task_id,
            instance_id,
            reason=InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
            workspace=None,
        )
        raise Ignore() from None


def _persist_attention(
    task_id: str,
    instance_id: int,
    *,
    reason: str,
    workspace: object,
) -> bool:
    if reason not in {
        InstanceTask.AttentionReason.PROCESS_CLEANUP_UNCONFIRMED,
        InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
    }:
        reason = InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED
    safe_workspace = ""
    if type(workspace) is str and len(workspace) <= 2048:
        safe_workspace = workspace
    try:
        with lifecycle_transaction():
            binding = InstanceTask.objects.select_for_update().filter(pk=task_id).first()
            if (
                binding is None
                or binding.instance_id != instance_id
                or binding.kind != InstanceTask.Kind.DOWNLOAD
                or binding.claimed_at is None
                or binding.finished_at is not None
                or binding.superseded_at is not None
            ):
                return False
            binding.requires_attention = True
            binding.attention_reason = reason
            binding.attention_workspace = safe_workspace
            binding.save(
                update_fields=(
                    "requires_attention",
                    "attention_reason",
                    "attention_workspace",
                )
            )
            return True
    except Exception:
        return False


def _guarded_callback(
    callback: Callable[..., Any],
    task_id: str,
    instance_id: int,
    lease: AtomicDownloadLease,
) -> Callable[..., None]:
    def guarded(*args: Any, **kwargs: Any) -> None:
        try:
            if lease.is_lost:
                return
            _assert_current_claim(task_id, instance_id, lease)
            callback(*args, **kwargs)
        except Exception:
            # Log and progress backends are best effort. The durable DB binding
            # remains the host fence if either backend is unavailable.
            return

    return guarded


def _best_effort_progress(
    task,
    lease: AtomicDownloadLease,
    task_id: str,
    instance_id: int,
    status: str,
) -> None:
    try:
        _assert_current_claim(task_id, instance_id, lease)
        task.update_state(state="PROGRESS", meta={"status": status})
    except Exception:
        # A result-backend failure cannot stop a verified host operation or
        # replace trusted process-cleanup uncertainty.
        pass


def _safe_worker_error(exc: BaseException, redactor: SteamSecretRedactor | None):
    if isinstance(exc, CleanupUncertainError):
        diagnostic = CleanupUncertainError.public_diagnostic
    elif redactor is None:
        # Preparation can fail before a credential redactor exists. Do not
        # inspect or format the original exception in this branch.
        diagnostic = "Preparation failed before download setup; details omitted"
    else:
        diagnostic = safe_steam_diagnostic(exc, redactor)
    return SteamCommandError(
        "Nie udało się pobrać modów. Sprawdź logi operacji.",
        diagnostic=diagnostic,
    )
