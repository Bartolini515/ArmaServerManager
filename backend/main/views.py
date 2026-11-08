from contextlib import ExitStack
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.db.models import Q
import os
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from .serializers import *
from rest_framework.response import Response
from .models import *
from django.contrib.auth import get_user_model, authenticate
from django.utils import timezone
from django.core.files.base import ContentFile
from knox.models import AuthToken
import psutil
import platform
from django.db import DatabaseError, transaction
from uuid import uuid4
from .modpreset.modpathing import check_installed
from .modpreset.preset_extraction import preset_parser
from .modpreset.start_files import generate_sh_file, check_sh_file_exists, generate_server_config
from .utils.config import config
from .utils.operation_logging import operation_loggers
from celery.result import AsyncResult
from .tasks import *
from .instance_tasks import create_instance_task_binding, dispatch_instance_task
from .download_jobs import DownloadAdmissionError, reserve_download
from .instance_lifecycle import (
    RESERVED_LIFECYCLE_STATES,
    StopReservationRejected,
    dispatch_stop_reservation,
    is_instance_lifecycle_busy,
    lifecycle_transaction,
    require_lifecycle_autocommit,
    reserve_stop,
)
from .instance_files import (
    ManagedFileError,
    ManagedFileRollbackError,
    UnsafeManagedPathError,
)
from .instance_management import (
    InstanceBusyError,
    InstanceLimitExceeded,
    InstanceManagementError,
    InstanceNotFoundError,
    InstanceOperationError,
    ManagedFileMissingError,
    MissingLogFileError,
    MissingPresetError,
    MutationResult,
    NoAvailablePortError,
    ProfileBusyError,
    ProfileNotFoundError,
    create_instance,
    delete_instance,
    delete_instance_logs,
    delete_profile,
    open_validated_file,
    preflight_managed_root_layout,
    replace_admin_instance_preset,
    staged_first_instance_log,
    validate_instance_log_file,
    validate_instance_preset,
    validate_instance_start_file,
)
User = get_user_model()


class InstanceLifecycleBusy(RuntimeError):
    """An instance changed lifecycle state before an operation was queued."""


def enqueue_instance_task(instance, kind, task, *, kwargs):
    """Create a lifecycle-safe task binding, then dispatch after the lock commits."""
    require_lifecycle_autocommit()
    with lifecycle_transaction():
        locked = Instances.objects.select_for_update().filter(pk=instance.pk).first()
        if locked is None:
            raise InstanceLifecycleBusy("The instance no longer exists.")
        blocking_tasks = InstanceTask.objects.filter(
            instance=locked,
            kind__in=(InstanceTask.Kind.START, InstanceTask.Kind.STOP),
            finished_at__isnull=True,
            superseded_at__isnull=True,
        ).exists()
        if (
            locked.lifecycle_state != Instances.LifecycleState.STOPPED
            or locked.is_running
            or locked.pid is not None
            or blocking_tasks
        ):
            raise InstanceLifecycleBusy("The instance has an active lifecycle operation.")
        binding = create_instance_task_binding(locked, kind)
    return dispatch_instance_task(binding, task, kwargs=kwargs)


def message_response(data, message="Operacja się powiodła"):
    return Response({
        "result": data,
        "message": message
    })


def _mutation_response(result: MutationResult, data, message: str):
    payload = {"result": data, "message": message}
    if result.cleanup_pending is not None:
        payload["cleanup_pending"] = {
            "pending": True,
            "reason": result.cleanup_pending.reason,
            "pending_count": result.cleanup_pending.pending_count,
        }
        payload["message"] = "Operacja zakończona; czyszczenie plików wymaga uwagi administratora."
    return Response(payload, status=200)


def _delete_response(result: MutationResult, message: str):
    payload = {"message": message}
    if result.cleanup_pending is not None:
        payload["cleanup_pending"] = {
            "pending": True,
            "reason": result.cleanup_pending.reason,
            "pending_count": result.cleanup_pending.pending_count,
        }
        payload["message"] = "Operacja zakończona; czyszczenie plików wymaga uwagi administratora."
    return Response(payload, status=200)


def _task_response(task_id: str, cleanup_pending=None):
    payload = {"task_id": task_id}
    if cleanup_pending is not None:
        payload["cleanup_pending"] = {
            "pending": True,
            "reason": cleanup_pending.reason,
            "pending_count": cleanup_pending.pending_count,
        }
        payload["message"] = "Uruchomienie rozpoczęte; czyszczenie plików wymaga uwagi administratora."
    return Response(payload, status=202)


def _close_start_file_stack(file_stack):
    """Compensate staged first-start files and return a static rollback error if needed."""
    try:
        file_stack.close()
    except ManagedFileRollbackError:
        return _operation_error("Nie udało się bezpiecznie wycofać zmian pliku logu.")
    except ManagedFileError:
        return _operation_error("Nie udało się bezpiecznie wycofać zmian pliku logu.")
    except Exception:
        return _operation_error("Nie udało się bezpiecznie wycofać zmian pliku logu.")
    return None


def _snapshot_value(snapshot, key, default=None):
    value = snapshot
    try:
        for component in key.split("."):
            value = value[component]
        return value
    except (KeyError, TypeError):
        return default


def _managed_path_error(message="Pliki instancji wymagają weryfikacji przez administratora."):
    return Response({"message": message}, status=400)


def _operation_error(message="Nie udało się wykonać operacji na plikach instancji."):
    return Response({"message": message}, status=500)
    
def get_super_user():
    try:
        return Profile.objects.get(is_superuser=True)
    except Profile.DoesNotExist:
        return None

class LoginViewset(viewsets.ViewSet):
    permission_classes = [permissions.AllowAny]
    serializer_class = LoginSerializer
    
    def create(self, request):
        serializer = self.serializer_class(data=request.data)
        
        if serializer.is_valid():
            username = serializer.validated_data['username']
            password = serializer.validated_data['password']
            
            user = authenticate(request, username=username, password=password)
            
            if user:
                if user.last_login is not None:
                    user.last_login = timezone.now()
                    user.save()
                _, token=AuthToken.objects.create(user)
                return Response({
                    'user': ProfileSerializer(user).data,
                    'token': token,
                    'isAdmin': user.is_staff,
                    'message': "Zalogowano pomyślnie"
                    })
            else:
                return Response({"message": "Nieprawidłowe dane"}, status=401)
        else:
            return Response(serializer.errors, status=400)

class AccountViewset(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProfileSerializer
    queryset = Profile.objects.all()
    
    def list(self, request):
        serializer = self.serializer_class(self.queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=["post"], url_path="change_password")
    def change_password(self, request, token=None):
        user = self.request.user
        
        serializer = Password_changeSerializer(data=request.data)
        if serializer.is_valid():
            password = serializer.validated_data['password']   
            user.set_password(password)
            user.last_login = timezone.now()
            user.save()
            return Response({"message": "Hasło zmienione"})
        else:
            return Response(serializer.errors, status=400)
    
    @action(detail=False, methods=["get"], url_path="get_user")
    def get_user(self, request):
        user = self.request.user
        
        serializer = self.serializer_class(self.queryset.get(pk=user.id))
        data = serializer.data
            
        return Response({'user': data, 'isAdmin': user.is_staff})
    
class ModeratorPanelViewset(viewsets.ViewSet):
    permission_classes = [permissions.IsAdminUser]
    
    
    # Sekcja profili
    @action(detail=False, methods=["get"], url_path="user")
    def listProfiles(self, request):
        queryset = Profile.objects.all()
        serializer = Profiles_moderatorPanelSerializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=["get"], url_path="user")
    def requestProfile(self, request, pk=None):
        queryset = Profile.objects.get(pk=pk)
        serializer = Profiles_moderatorPanelSerializer(queryset)
        return Response(serializer.data)
    
    @action(detail=True, methods=["put"], url_path="user/update")
    def updateProfile(self, request, pk=None):
        queryset = Profile.objects.get(pk=pk)
        serializer = Profiles_moderatorPanelSerializer(queryset, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return message_response(serializer.data, "Profil zaktualizowany")
        else:
            return Response(serializer.errors, status=400)
        
    @action(detail=False, methods=["post"], url_path="user/create")
    def createProfile(self, request):
        serializer = Profiles_moderatorPanelSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return message_response(serializer.data, "Dodano użytkownika")
        else:
            return Response(serializer.errors, status=400)

    @action(detail=True, methods=["delete"], url_path="user/delete")
    def deleteProfile(self, request, pk=None):
        try:
            require_lifecycle_autocommit()
            result = delete_profile(
                profile_id=pk,
                config_snapshot=config.snapshot(),
            )
        except ProfileNotFoundError:
            return Response({"message": "Użytkownik nie istnieje"}, status=404)
        except ProfileBusyError:
            return Response(
                {"message": "Nie można usunąć użytkownika podczas aktywnej operacji instancji"},
                status=400,
            )
        except UnsafeManagedPathError:
            return _managed_path_error()
        except ManagedFileError:
            return _operation_error()
        except InstanceManagementError:
            return _operation_error()
        except Exception:
            return _operation_error()
        return _delete_response(result, "Użytkownik usunięty")
    
    # Sekcja configu
    @action(detail=False, methods=["get"], url_path="config")
    def getConfig(self, request):
        serializer = ConfigSerializer(config.config)
        config_data = serializer.data
        return Response(config_data)
    
    @action(detail=False, methods=["put"], url_path="config/update")
    def updateConfig(self, request):
        serializer = ConfigSerializer(data=request.data)
        if serializer.is_valid():
            config.update(serializer.validated_data)
            return Response({"message": "Konfiguracja zaktualizowana"})
        return Response(serializer.errors, status=400)

class ServicesViewset(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]
    
    @action(detail=False, methods=["get"], url_path="get_system_info")
    def getSystemInfo(self, request):
        cpu_usage = round(psutil.cpu_percent(interval=None))
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        cpu_count = psutil.cpu_count()
        os_name = platform.system()

        return Response({
            'cpuUsage': cpu_usage,
            'memoryLeft': memory.available,
            'memoryTotal': memory.total,
            'spaceLeft': disk.free,
            'spaceTotal': disk.total,
            'cpuCount': cpu_count,
            'osName': os_name,
        })
        
class InstancesViewset(viewsets.ModelViewSet):
    """Expose instance CRUD and task-backed host operations."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = InstanceSerializer
    queryset = Instances.objects.all()
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return self.queryset.all()
        return self.queryset.filter(user=user, is_admin_instance=False)

    def _get_authorized_instance(self, pk):
        """Look up one instance through the shared owner-or-staff policy."""
        instance = get_object_or_404(self.filter_queryset(self.get_queryset()), pk=pk)
        self.check_object_permissions(self.request, instance)
        return instance

    def list(self, request):
        user = request.user
        all_instances = self.queryset.all()
        user_instances = all_instances.filter(user=user, is_admin_instance=False)
        user_serializer = self.serializer_class(user_instances, many=True)
        if user.is_staff:
            admin_instances = all_instances.filter(is_admin_instance=True)
            admin_serializer = self.serializer_class(admin_instances, many=True)
            other_instances = all_instances.filter(is_admin_instance=False).exclude(user=user)
            other_serializer = self.serializer_class(other_instances, many=True)
        return Response({
            'user_instances': user_serializer.data,
            'admin_instances': admin_serializer.data if user.is_staff else [],
            'other_instances': other_serializer.data if user.is_staff else [],
        })

    def create(self, request):
        """Validate an upload, then publish the instance and its managed files together."""
        user = self.request.user
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        try:
            require_lifecycle_autocommit()
            config_snapshot = config.snapshot()
            preflight_managed_root_layout(config_snapshot)
            log_directory = _snapshot_value(
                config_snapshot,
                "paths.logs_directory",
                "/logs",
            )
            with operation_loggers(log_directory, user.username, ("create",)) as loggers:
                result = create_instance(
                    owner=user,
                    validated_data=serializer.validated_data,
                    config_snapshot=config_snapshot,
                    log_callback=loggers["create"].info,
                )
        except InstanceLimitExceeded:
            return Response({"message": "Przekroczono limit 5 instancji"}, status=403)
        except NoAvailablePortError:
            return Response({"message": "Brak dostępnych portów"}, status=403)
        except MissingPresetError:
            return Response({"message": "Wymagany jest plik preset"}, status=400)
        except UnsafeManagedPathError:
            return _managed_path_error()
        except ManagedFileError:
            return _operation_error("Nie udało się bezpiecznie zapisać plików instancji.")
        except InstanceManagementError:
            return _operation_error("Nie udało się utworzyć instancji.")
        except Exception:
            return _operation_error("Nie udało się utworzyć instancji.")

        return _mutation_response(
            result,
            self.serializer_class(result.instance).data,
            "Instancja została utworzona",
        )
    
    def destroy(self, request, pk=None):
        """Delete a stopped instance through the lifecycle-locked file service."""
        try:
            instance = self.get_object()
        except Http404:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)

        try:
            require_lifecycle_autocommit()
            result = delete_instance(
                instance_id=instance.pk,
                config_snapshot=config.snapshot(),
            )
        except InstanceNotFoundError:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)
        except InstanceBusyError:
            message = (
                "Nie można usunąć działającej instancji"
                if instance.is_running
                else "Nie można usunąć instancji podczas aktywnej operacji"
            )
            return Response({"message": message}, status=400)
        except UnsafeManagedPathError:
            return _managed_path_error()
        except ManagedFileError:
            return _operation_error("Nie udało się bezpiecznie usunąć plików instancji.")
        except InstanceManagementError:
            return _operation_error("Nie udało się usunąć instancji.")
        except Exception:
            return _operation_error("Nie udało się usunąć instancji.")

        return _delete_response(result, "Instancja została usunięta")
    
    @action(detail=True, methods=["post"], url_path="start")
    def start(self, request, pk=None):
        """Validate readiness and enqueue an asynchronous server start."""
        actor = self.request.user
        try:
            require_lifecycle_autocommit()
        except RuntimeError:
            return Response(
                {"message": "Nie udało się rozpocząć zadania uruchamiania."},
                status=500,
            )

        file_stack = ExitStack()
        start_log_journal = None
        cleanup_pending = None
        authorization_exception = None
        try:
            queued = None
            with lifecycle_transaction():
                instance = self.filter_queryset(self.get_queryset()).select_for_update().filter(
                    pk=pk
                ).first()
                if instance is None:
                    return Response(
                        {"message": "Instancja nie została znaleziona. Skontaktuj się z administratorem"},
                        status=404,
                    )
                try:
                    self.check_object_permissions(request, instance)
                except APIException as error:
                    authorization_exception = error
                    raise
                if instance.is_admin_instance and actor.is_staff:
                    operator = get_super_user()
                    if not operator:
                        return Response({"message": "Nie znaleziono administratora."}, status=404)
                else:
                    operator = actor

                if instance.lifecycle_state == Instances.LifecycleState.RUNNING:
                    return Response({"message": "Instancja jest już uruchomiona"}, status=400)

                if instance.lifecycle_state == Instances.LifecycleState.STARTING:
                    existing_start = InstanceTask.objects.filter(
                        pk=instance.lifecycle_task_id,
                        instance=instance,
                        kind=InstanceTask.Kind.START,
                        finished_at__isnull=True,
                        superseded_at__isnull=True,
                    ).first()
                    if existing_start:
                        return Response({"task_id": existing_start.task_id}, status=202)
                    return Response(
                        {"message": "Instancja ma niepewny stan uruchamiania."},
                        status=400,
                    )

                if instance.lifecycle_state != Instances.LifecycleState.STOPPED:
                    return Response(
                        {"message": "Nie można uruchomić instancji w nieznanym stanie."},
                        status=400,
                    )
                if instance.is_running or instance.pid is not None:
                    return Response(
                        {"message": "Nie można uruchomić instancji w nieznanym stanie."},
                        status=400,
                    )

                active_tasks = InstanceTask.objects.filter(
                    instance=instance,
                    finished_at__isnull=True,
                    superseded_at__isnull=True,
                )
                if active_tasks.exists():
                    return Response(
                        {"message": "Instancja ma aktywne zadania. Poczekaj na ich zakończenie."},
                        status=400,
                    )

                if not instance.is_admin_instance:
                    owner_reserved = Instances.objects.filter(
                        user=instance.user,
                        is_admin_instance=False,
                    ).filter(
                        Q(lifecycle_state__in=RESERVED_LIFECYCLE_STATES)
                        | Q(is_running=True)
                        | Q(pid__isnull=False)
                    ).exclude(pk=instance.pk).exists()
                    if owner_reserved:
                        return Response(
                            {"message": "Nie można posiadać uruchomionej więcej niż jednej instancji"},
                            status=400,
                        )

                if (
                    not actor.is_staff
                    and Instances.objects.filter(is_admin_instance=False)
                    .filter(
                        Q(lifecycle_state__in=RESERVED_LIFECYCLE_STATES)
                        | Q(is_running=True)
                        | Q(pid__isnull=False)
                    )
                    .count()
                    >= 5
                ):
                    return Response(
                        {"message": "Przekroczono generalny limit 5 uruchomionych instancji. Spróbuj ponownie później."},
                        status=403,
                    )

                config_snapshot = config.snapshot()
                arma3_dir = _snapshot_value(config_snapshot, "paths.arma3")
                if not isinstance(arma3_dir, (str, os.PathLike)):
                    return Response(
                        {"message": "Plik startowy instancji nie istnieje. Skontaktuj się z administratorem"},
                        status=400,
                    )
                arma3_dir = os.fspath(arma3_dir)
                try:
                    preset_path = validate_instance_preset(instance, config_snapshot)
                    validate_instance_start_file(instance, config_snapshot)
                    log_path = validate_instance_log_file(
                        instance,
                        require_exists=bool(instance.log_file),
                    )
                except (UnsafeManagedPathError, ManagedFileMissingError, ManagedFileError):
                    return Response(
                        {"message": "Plik startowy instancji nie istnieje. Skontaktuj się z administratorem"},
                        status=400,
                    )
                try:
                    preflight_managed_root_layout(config_snapshot)
                except UnsafeManagedPathError:
                    return _managed_path_error()
                expected_executable = os.path.realpath(
                    os.path.join(arma3_dir, "arma3server_x64")
                )
                with operation_loggers(
                    _snapshot_value(config_snapshot, "paths.logs_directory", "/logs"),
                    operator.username,
                    ("start",),
                ) as loggers:
                    start_logger = loggers["start"]
                    try:
                        workshop_ids = preset_parser(
                            preset_path,
                            log_callback=start_logger.info,
                        )
                    except Exception:
                        return _operation_error("Nie udało się zweryfikować plików instancji.")
                    mods_dir = _snapshot_value(config_snapshot, "paths.mods_directory")
                    if check_installed(
                        wids=workshop_ids,
                        mods_dir=mods_dir,
                        log_callback=start_logger.info,
                    )[1]:
                        instance.is_ready = False
                        instance.save(update_fields=["is_ready"])
                        return Response(
                            {"message": "Niektóre mody są niezainstalowane. Proszę je najpierw pobrać."},
                            status=400,
                        )
                    if not instance.log_file:
                        log_filename = "log_file.txt"
                        log_content = (
                            f"Log file for instance '{instance.name}' created at {timezone.now()}.\n"
                        )
                        content_file = ContentFile(
                            log_content.encode("utf-8"),
                            name=log_filename,
                        )
                        (
                            start_log_journal,
                            log_relative_name,
                            log_path,
                        ) = file_stack.enter_context(
                            staged_first_instance_log(
                                instance,
                                config_snapshot,
                                content_file,
                            )
                        )
                        start_log_journal.publish()
                        instance.log_file.name = log_relative_name
                        instance.log_file._committed = True
                        instance.save(update_fields=["log_file"])
                        transaction.on_commit(start_log_journal.mark_committed)
                    if not log_path:
                        raise UnsafeManagedPathError()

                binding = create_instance_task_binding(instance, InstanceTask.Kind.START)
                run_id = uuid4()
                instance.run_id = run_id
                instance.process_created_at = None
                instance.process_executable = expected_executable
                instance.lifecycle_task_id = binding.task_id
                instance.start_claimed_at = None
                instance.timeout_task_id = None
                instance.lifecycle_state = Instances.LifecycleState.STARTING
                instance.shutdown_at = None
                instance.pid = None
                instance.is_running = False
                instance.save(
                    update_fields=[
                        "run_id",
                        "process_created_at",
                        "process_executable",
                        "lifecycle_task_id",
                        "start_claimed_at",
                        "timeout_task_id",
                        "lifecycle_state",
                        "shutdown_at",
                        "pid",
                        "is_running",
                    ]
                )
                queued = (binding, instance.pk, str(run_id), arma3_dir)
            if start_log_journal is not None:
                cleanup_pending = start_log_journal.finalize()
        except APIException as error:
            if error is authorization_exception:
                raise
            rollback_response = _close_start_file_stack(file_stack)
            if rollback_response is not None:
                return rollback_response
            return _operation_error("Nie udało się przygotować pliku logu.")
        except Http404:
            rollback_response = _close_start_file_stack(file_stack)
            if rollback_response is not None:
                return rollback_response
            return Response({"message": "Instancja nie została znaleziona. Skontaktuj się z administratorem"}, status=404)
        except UnsafeManagedPathError:
            rollback_response = _close_start_file_stack(file_stack)
            if rollback_response is not None:
                return rollback_response
            return _managed_path_error("Katalog logu wymaga weryfikacji przez administratora.")
        except ManagedFileRollbackError:
            rollback_response = _close_start_file_stack(file_stack)
            if rollback_response is not None:
                return rollback_response
            return _operation_error("Nie udało się bezpiecznie wycofać zmian pliku logu.")
        except (ManagedFileError, DatabaseError, OSError, ValueError):
            rollback_response = _close_start_file_stack(file_stack)
            if rollback_response is not None:
                return rollback_response
            return _operation_error("Nie udało się przygotować pliku logu.")
        except Exception:
            rollback_response = _close_start_file_stack(file_stack)
            if rollback_response is not None:
                return rollback_response
            return _operation_error("Nie udało się przygotować pliku logu.")
        finally:
            file_stack.close()
        if queued is None:
            return Response(
                {"message": "Nie udało się rozpocząć zadania uruchamiania."},
                status=500,
            )

        binding, instance_id, run_id, arma3_dir = queued
        try:
            task_id = dispatch_instance_task(
                binding,
                start_server_task,
                kwargs={
                    "instance_id": instance_id,
                    "arma3_dir": arma3_dir,
                    "run_id": run_id,
                },
            )
        except Exception:
            delivery_confirmed = False
            with lifecycle_transaction():
                current = Instances.objects.select_for_update().filter(
                    pk=instance_id,
                    run_id=run_id,
                    lifecycle_task_id=binding.task_id,
                ).first()
                current_binding = InstanceTask.objects.filter(pk=binding.task_id).first()
                if current is not None and (
                    current.start_claimed_at is not None
                    or (
                        current_binding is not None
                        and current_binding.claimed_at is not None
                    )
                ):
                    delivery_confirmed = True
                elif current is not None:
                    if current_binding is not None:
                        current_binding.delete()
                    current.lifecycle_state = Instances.LifecycleState.STOPPED
                    current.is_running = False
                    current.pid = None
                    current.run_id = None
                    current.process_created_at = None
                    current.process_executable = None
                    current.lifecycle_task_id = None
                    current.start_claimed_at = None
                    current.timeout_task_id = None
                    current.shutdown_at = None
                    current.save()
            if delivery_confirmed:
                return _task_response(binding.task_id, cleanup_pending)
            failure_payload = {
                "message": "Nie udało się rozpocząć zadania uruchamiania."
            }
            if cleanup_pending is not None:
                failure_payload["cleanup_pending"] = {
                    "pending": True,
                    "reason": cleanup_pending.reason,
                    "pending_count": cleanup_pending.pending_count,
                }
            return Response(
                failure_payload,
                status=500,
            )
        return _task_response(task_id, cleanup_pending)

    @action(detail=True, methods=["post"], url_path="stop")
    def stop(self, request, pk=None):
        """Reserve and enqueue a verified stop for the current process run."""
        try:
            require_lifecycle_autocommit()
        except RuntimeError:
            return Response({"message": "Nie udało się rozpocząć zadania zatrzymania."}, status=500)
        try:
            instance = self._get_authorized_instance(pk)
        except Http404:
            return Response({"message": "Instancja nie została znaleziona. Skontaktuj się z administratorem"}, status=404)
        try:
            reservation = reserve_stop(instance.pk)
        except StopReservationRejected as exc:
            if str(exc) == "missing":
                return Response({"message": "Instancja nie została znaleziona. Skontaktuj się z administratorem"}, status=404)
            if str(exc) == "not_running" and instance.lifecycle_state == Instances.LifecycleState.STOPPED:
                return Response({"message": "Instancja jest już zatrzymana"}, status=400)
            return Response(
                {"message": "Nie można bezpiecznie zatrzymać instancji w nieznanym stanie."},
                status=400,
            )
        try:
            task_id = dispatch_stop_reservation(reservation, stop_server_task)
        except Exception:
            return Response({"message": "Nie udało się rozpocząć zadania zatrzymania."}, status=500)
        return Response({"task_id": task_id}, status=202)

    @action(detail=True, methods=["post"], url_path="admin_instance/change_preset", permission_classes=[permissions.IsAdminUser])
    def change_preset(self, request, pk=None):
        try:
            instance = self._get_authorized_instance(pk)
        except Http404:
            return Response({"message": "Instancja nie została znaleziona."}, status=404)

        user = get_super_user()
        if not user:
            return Response({"message": "Nie znaleziono administratora."}, status=404)
        
        if not instance.is_admin_instance:
            return Response({"message": "To nie jest instancja administracyjna."}, status=400)

        serializer = self.serializer_class(instance, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        if "preset" not in serializer.validated_data:
            return Response({"preset": ["To pole jest wymagane."]}, status=400)

        try:
            require_lifecycle_autocommit()
            config_snapshot = config.snapshot()
            preflight_managed_root_layout(config_snapshot)
            log_directory = _snapshot_value(
                config_snapshot,
                "paths.logs_directory",
                "/logs",
            )
            with operation_loggers(log_directory, user.username, ("change_preset",)) as loggers:
                result = replace_admin_instance_preset(
                    instance_id=instance.pk,
                    validated_data=serializer.validated_data,
                    config_snapshot=config_snapshot,
                    log_callback=loggers["change_preset"].info,
                )
        except InstanceBusyError:
            message = (
                "Nie można zmienić presetu działającej instancji."
                if instance.is_running
                else "Nie można zmienić presetu podczas aktywnej operacji instancji."
            )
            return Response({"message": message}, status=400)
        except MissingPresetError:
            return Response({"preset": ["To pole jest wymagane."]}, status=400)
        except UnsafeManagedPathError:
            return _managed_path_error()
        except ManagedFileError:
            return _operation_error("Nie udało się bezpiecznie zmienić presetu.")
        except InstanceManagementError:
            return _operation_error("Nie udało się zmienić presetu.")
        except Exception:
            return _operation_error("Nie udało się zmienić presetu.")

        return _mutation_response(
            result,
            self.serializer_class(result.instance).data,
            "Preset instancji głównej został zmieniony",
        )

    @action(detail=True, methods=['post'], url_path='download_mods')
    def download_mods(self, request, pk=None):
        """Reserve and enqueue one durable Steam Workshop download."""
        try:
            instance = self._get_authorized_instance(pk)
        except Http404:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)

        try:
            require_lifecycle_autocommit()
        except RuntimeError:
            return Response(
                {"message": "Nie udało się rozpocząć zadania pobierania."},
                status=500,
            )

        class _DownloadPreflightFailure(Exception):
            def __init__(self, code):
                self.code = code

        def new_admission_preflight():
            if instance.is_admin_instance and not get_super_user():
                raise _DownloadPreflightFailure("admin_missing")

            try:
                config_snapshot = config.snapshot()
                validate_instance_preset(instance, config_snapshot)
                _snapshot_value(config_snapshot, "paths.mods_directory")
            except (UnsafeManagedPathError, ManagedFileError):
                raise
            except Exception as exc:
                raise _DownloadPreflightFailure("preset_verification") from exc

            disk = psutil.disk_usage("/")
            if disk.free < 20 * 1024 * 1024 * 1024:
                raise _DownloadPreflightFailure("disk_full")

        try:
            binding, reused = reserve_download(
                instance.pk,
                preflight=new_admission_preflight,
            )
        except DownloadAdmissionError as exc:
            return Response({"message": str(exc)}, status=exc.status_code)
        except UnsafeManagedPathError:
            return _managed_path_error(
                "Plik preset wymaga weryfikacji przez administratora."
            )
        except ManagedFileError:
            return Response(
                {"message": "Plik preset instancji nie istnieje."},
                status=400,
            )
        except _DownloadPreflightFailure as exc:
            if exc.code == "admin_missing":
                return Response({"message": "Nie znaleziono administratora."}, status=404)
            if exc.code == "disk_full":
                return Response(
                    {"message": "Brak miejsca na dysku. Skontaktuj sie z administratorem."},
                    status=400,
                )
            return _operation_error("Nie udało się zweryfikować pliku preset.")
        except Exception:
            return Response(
                {"message": "Nie udało się rozpocząć zadania pobierania."},
                status=500,
            )

        if reused:
            return Response({"task_id": binding.task_id}, status=202)

        try:
            task_id = dispatch_instance_task(
                binding,
                download_mods_task,
                kwargs={"instance_id": instance.pk},
            )
        except Exception:
            return Response(
                {"message": "Nie udało się rozpocząć zadania pobierania."},
                status=500,
            )
        return Response({'task_id': task_id}, status=202)

    @action(detail=False, methods=['get'], url_path='task_status/(?P<task_id>[^/.]+)')
    def task_status(self, request, task_id=None):
        """Return the current Celery state and result metadata for a task ID."""
        bindings = InstanceTask.objects.filter(pk=task_id)
        if not request.user.is_staff:
            bindings = bindings.filter(
                instance__user=request.user,
                instance__is_admin_instance=False,
            )
        binding = bindings.first()
        if binding is None:
            return Response({"message": "Zadanie nie zostało znalezione"}, status=404)

        if binding.superseded_at is not None:
            return Response(
                {
                    "id": task_id,
                    "state": "REVOKED",
                    "result": {"status": "Zadanie zostało zastąpione przez nowszą próbę zatrzymania."},
                },
                status=200,
            )

        attention_status = "Stan zadania wymaga ręcznej weryfikacji przez administratora."

        def attention_response():
            return Response(
                {
                    "id": task_id,
                    "state": "REQUIRES_ATTENTION",
                    "result": {"status": attention_status},
                },
                status=200,
            )

        def superseded_response():
            return Response(
                {
                    "id": task_id,
                    "state": "REVOKED",
                    "result": {"status": "Zadanie zostało zastąpione przez nowszą próbę zatrzymania."},
                },
                status=200,
            )

        if binding.requires_attention:
            return attention_response()

        task_result = AsyncResult(task_id)
        backend_state = task_result.status if task_result else "PENDING"

        if backend_state == "PENDING" or not task_result:
            with transaction.atomic():
                current_binding = InstanceTask.objects.select_for_update().filter(
                    pk=task_id
                ).first()
                if current_binding is None:
                    return Response({"message": "Zadanie nie zostało znalezione"}, status=404)
                if current_binding.superseded_at is not None:
                    return superseded_response()
                if current_binding.requires_attention:
                    return attention_response()
                if current_binding.finished_at is not None:
                    InstanceTask.objects.filter(
                        pk=task_id,
                        finished_at__isnull=False,
                        superseded_at__isnull=True,
                        requires_attention=False,
                    ).delete()
                    return Response({"message": "Zadanie nie zostało znalezione"}, status=404)

            return Response(
                {"id": task_id, "state": backend_state, "result": None},
                status=200,
            )

        terminal_states = {"SUCCESS", "FAILURE", "REVOKED"}
        if (
            binding.kind == InstanceTask.Kind.DOWNLOAD
            and binding.finished_at is None
            and backend_state in terminal_states
        ):
            with lifecycle_transaction():
                current_binding = InstanceTask.objects.select_for_update().filter(
                    pk=task_id
                ).first()
                if current_binding is None:
                    return Response({"message": "Zadanie nie zostało znalezione"}, status=404)
                if current_binding.superseded_at is not None:
                    return superseded_response()
                if current_binding.requires_attention:
                    return attention_response()
                if (
                    current_binding.kind == InstanceTask.Kind.DOWNLOAD
                    and current_binding.finished_at is None
                ):
                    current_binding.requires_attention = True
                    current_binding.attention_reason = (
                        InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED
                    )
                    current_binding.save(
                        update_fields=("requires_attention", "attention_reason")
                    )
                    return attention_response()
                binding = current_binding

        res = task_result.result
        if isinstance(res, Exception) or backend_state == "FAILURE":
            # Older failures and generic exceptions may contain command arguments.
            # Keep the existing shape with an allowlisted message independent of
            # the exception or result value provided by the backend.
            res = {"status": "Nie udało się wykonać zadania."}

        result = {
            "id": task_id,
            "state": backend_state,
            "result": res
        }
        return Response(result, status=200)

    @action(detail=True, methods=['get'], url_path='logs')
    def logs(self, request, pk=None):
        """Read an instance log, optionally returning only its last lines."""
        try:
            instance = self._get_authorized_instance(pk)
        except Http404:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)
        
        tail = request.query_params.get('tail')
        try:
            tail = int(tail) if tail else None
        except ValueError:
            return Response({"message": "Nieprawidłowa wartość parametru tail"}, status=400)

        try:
            log_path = validate_instance_log_file(instance)
        except UnsafeManagedPathError:
            return _managed_path_error("Plik logu wymaga weryfikacji przez administratora.")
        except ManagedFileError:
            return _operation_error("Nie udało się zweryfikować pliku logu.")
        if not log_path:
            return Response({"message": "Brak logów lub plik nie istnieje."}, status=404)
        try:
            with open_validated_file(log_path) as log_file:
                if tail:
                    log_file.seek(0, os.SEEK_END)
                    buffer_size = 4096
                    file_size = log_file.tell()
                    buffer = b""
                    lines_found = 0
                    while lines_found < tail and file_size > 0:
                        read_pos = max(0, file_size - buffer_size)
                        log_file.seek(read_pos)
                        chunk = log_file.read(min(buffer_size, file_size))
                        buffer = chunk + buffer
                        lines_found = buffer.count(b"\n")
                        file_size -= buffer_size
                    lines = buffer.splitlines()
                    content = b"\n".join(lines[-tail:]).decode(errors="replace")
                else:
                    content = log_file.read().decode(errors="replace")
        except ManagedFileMissingError:
            return Response({"message": "Brak logów lub plik nie istnieje."}, status=404)
        except UnsafeManagedPathError:
            return _managed_path_error("Plik logu wymaga weryfikacji przez administratora.")
        except ManagedFileError:
            return _operation_error("Nie udało się odczytać pliku logu.")
        return Response(content, content_type="text/plain", status=200)

    @action(detail=True, methods=['get'], url_path='logs/download')
    def download_logs(self, request, pk=None):
        try:
            instance = self._get_authorized_instance(pk)
        except Http404:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)

        try:
            log_path = validate_instance_log_file(instance)
        except UnsafeManagedPathError:
            return _managed_path_error("Plik logu wymaga weryfikacji przez administratora.")
        except ManagedFileError:
            return _operation_error("Nie udało się zweryfikować pliku logu.")
        if not log_path:
            return Response({"message": "Brak logów do pobrania"}, status=404)
        try:
            with open_validated_file(log_path) as log_file:
                logs = log_file.read().decode(errors="replace")
        except ManagedFileMissingError:
            return Response({"message": "Brak logów do pobrania"}, status=404)
        except UnsafeManagedPathError:
            return _managed_path_error("Plik logu wymaga weryfikacji przez administratora.")
        except ManagedFileError:
            return _operation_error("Nie udało się odczytać pliku logu.")

        response = HttpResponse(logs, content_type="text/plain")
        response['Content-Disposition'] = f'attachment; filename="logs_{pk}.txt"'
        return response
    
    @action(detail=True, methods=['delete'], url_path='logs/delete')
    def delete_logs(self, request, pk=None):
        try:
            instance = self._get_authorized_instance(pk)
        except Http404:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)
        
        try:
            require_lifecycle_autocommit()
            result = delete_instance_logs(
                instance_id=instance.pk,
                config_snapshot=config.snapshot(),
            )
        except InstanceBusyError:
            message = (
                "Nie można usunąć logów podczas działania instancji"
                if instance.is_running
                else "Nie można usunąć logów podczas aktywnej operacji instancji"
            )
            return Response({"message": message}, status=400)
        except MissingLogFileError:
            return Response({"message": "Brak logów do usunięcia"}, status=404)
        except InstanceNotFoundError:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)
        except UnsafeManagedPathError:
            return _managed_path_error("Plik logu wymaga weryfikacji przez administratora.")
        except ManagedFileError:
            return _operation_error("Nie udało się bezpiecznie usunąć pliku logu.")
        except InstanceManagementError:
            return _operation_error("Nie udało się usunąć logów.")
        except Exception:
            return _operation_error("Nie udało się usunąć logów.")
        return _delete_response(result, "Logi zostały usunięte")
    
class MissionsViewset(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    queryset = Missions.objects.all()
    serializer_class = MissionSerializer
    
    def create(self, request):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            
            mission = serializer.save()
            return message_response(self.serializer_class(mission).data, "Misja została wgrana")
        else:
            return Response(serializer.errors, status=400)
