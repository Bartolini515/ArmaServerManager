from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.db.models import Q
import os
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from .serializers import *
from rest_framework.response import Response
from .models import *
from django.contrib.auth import get_user_model, authenticate
from django.utils import timezone
from django.core.files.base import ContentFile
from knox.models import AuthToken
import psutil
import platform
from django.db import transaction
from uuid import uuid4
from .modpreset.modpathing import check_installed
from .modpreset.preset_extraction import preset_parser
from .modpreset.start_files import generate_sh_file, check_sh_file_exists, generate_server_config
from .utils.config import config
from .utils.operation_logging import operation_loggers
from celery.result import AsyncResult
from .tasks import *
from .instance_tasks import create_instance_task_binding, dispatch_instance_task
from .instance_lifecycle import (
    RESERVED_LIFECYCLE_STATES,
    StopReservationRejected,
    dispatch_stop_reservation,
    is_instance_lifecycle_busy,
    lifecycle_transaction,
    require_lifecycle_autocommit,
    reserve_stop,
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
            user = Profile.objects.get(pk=pk)
        except Profile.DoesNotExist:
            return Response({"message": "Użytkownik nie istnieje"}, status=404)
        if any(is_instance_lifecycle_busy(instance) for instance in user.instances.all()):
            return Response(
                {"message": "Nie można usunąć użytkownika podczas aktywnej operacji instancji"},
                status=400,
            )
        user.delete()
        return Response({"message": "Użytkownik usunięty"})
    
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
        """Reserve a port, persist an instance, and generate its host files."""
        user = self.request.user
        
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            if user.instances.count() >= 5 and not user.is_staff:
                return Response({"message": "Przekroczono limit 5 instancji"}, status=403)
            with transaction.atomic():
                available_port = Ports.objects.select_for_update().filter(is_available=True).first()
                if not available_port:
                    return Response({"message": "Brak dostępnych portów"}, status=403)

                instance = serializer.save(user=user, port=available_port)
                
                available_port.is_available = False
                available_port.save()
            
            with operation_loggers(
                config.get("paths.logs_directory", "/logs"),
                user.username,
                ("create",),
            ) as loggers:
                create_logger = loggers["create"]
                mod_paths = preset_parser(instance.preset.path, log_callback=create_logger.info)
                if not user.server_config:
                    server_config_file_path = generate_server_config(
                        user.username,
                        "test",
                        config.get("paths.arma3"),
                        log_callback=create_logger.info,
                    )
                    user.server_config = server_config_file_path
                    user.save()
                start_file_path = generate_sh_file(
                    instance.name,
                    instance.port.port_number,
                    user.username,
                    mod_paths,
                    config.get("paths.mods_directory"),
                    config.get("paths.arma3"),
                    log_callback=create_logger.info,
                )
                instance.start_file_path = start_file_path
                instance.save()

            return message_response(self.serializer_class(instance).data, "Instancja została utworzona")
        else:
            return Response(serializer.errors, status=400)
    
    def destroy(self, request, pk=None):
        """Delete a stopped instance and release model-owned files and port."""
        try:
            instance = self.get_object()
        except Http404:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)
        
        if is_instance_lifecycle_busy(instance):
            message = (
                "Nie można usunąć działającej instancji"
                if instance.is_running
                else "Nie można usunąć instancji podczas aktywnej operacji"
            )
            return Response({"message": message}, status=400)
        
        with transaction.atomic():
            instance.delete()
            
        return Response({"message": "Instancja została usunięta"}, status=200)
    
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
                self.check_object_permissions(request, instance)
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

                arma3_dir = config.get("paths.arma3")
                expected_executable = os.path.realpath(
                    os.path.join(arma3_dir, "arma3server_x64")
                )
                with operation_loggers(
                    config.get("paths.logs_directory", "/logs"),
                    operator.username,
                    ("start",),
                ) as loggers:
                    start_logger = loggers["start"]
                    workshop_ids = preset_parser(
                        instance.preset.path,
                        log_callback=start_logger.info,
                    )
                    mods_dir = config.get("paths.mods_directory")
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
                    if not check_sh_file_exists(
                        instance.name,
                        log_callback=start_logger.info,
                    ):
                        return Response(
                            {"message": "Plik startowy instancji nie istnieje. Skontaktuj się z administratorem"},
                            status=400,
                        )
                    if not instance.start_file_path or not instance.start_file_path.strip():
                        return Response(
                            {"message": "Plik startowy instancji nie istnieje. Skontaktuj się z administratorem"},
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
                        instance.log_file.save(log_filename, content_file, save=True)
                    try:
                        if not os.fspath(instance.log_file.path).strip():
                            raise ValueError("The log file path is empty.")
                    except Exception:
                        return Response(
                            {"message": "Plik startowy instancji nie istnieje. Skontaktuj się z administratorem"},
                            status=400,
                        )

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
        except Http404:
            return Response({"message": "Instancja nie została znaleziona. Skontaktuj się z administratorem"}, status=404)
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
                return Response({"task_id": binding.task_id}, status=202)
            return Response(
                {"message": "Nie udało się rozpocząć zadania uruchamiania."},
                status=500,
            )
        return Response({"task_id": task_id}, status=202)

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
        
        if is_instance_lifecycle_busy(instance):
            message = (
                "Nie można zmienić presetu działającej instancji."
                if instance.is_running
                else "Nie można zmienić presetu podczas aktywnej operacji instancji."
            )
            return Response(
                {"message": message},
                status=400,
            )
        
        

        serializer = self.serializer_class(instance, data=request.data, partial=True)
        if serializer.is_valid():
            if instance.preset and os.path.exists(instance.preset.path):
                os.remove(instance.preset.path)
            instance.preset = serializer.validated_data["preset"]
            instance.save()
        else:
            return Response(serializer.errors, status=400)
        
        with operation_loggers(
            config.get("paths.logs_directory", "/logs"),
            user.username,
            ("change_preset",),
        ) as loggers:
            change_preset_logger = loggers["change_preset"]
            mod_paths = preset_parser(instance.preset.path, log_callback=change_preset_logger.info)

            if instance.start_file_path and os.path.exists(instance.start_file_path):
                os.remove(instance.start_file_path)
            start_file_path = generate_sh_file(
                instance.name,
                instance.port.port_number,
                user.username,
                mod_paths,
                config.get("paths.mods_directory"),
                config.get("paths.arma3"),
                log_callback=change_preset_logger.info,
                is_admin_instance=True,
            )
            instance.start_file_path = start_file_path
            instance.is_ready = False
            instance.save()

        return message_response(self.serializer_class(instance).data, "Preset instancji głównej został zmieniony")

    @action(detail=True, methods=['post'], url_path='download_mods')
    def download_mods(self, request, pk=None):
        """Enqueue Steam Workshop downloads for the instance preset."""
        try:
            require_lifecycle_autocommit()
        except RuntimeError:
            return Response(
                {"message": "Nie udało się rozpocząć zadania pobierania."},
                status=500,
            )
        try:
            instance = self._get_authorized_instance(pk)
        except Http404:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)
        if instance.is_admin_instance:
            user = get_super_user()
            if not user:
                return Response({"message": "Nie znaleziono administratora."}, status=404)
        else:
            user = self.request.user
            
        disk = psutil.disk_usage("/")
        if disk.free < 20 * 1024 * 1024 * 1024:
            return Response({"message": "Brak miejsca na dysku. Skontaktuj sie z administratorem."}, status=400)
        
        try:
            task_id = enqueue_instance_task(
                instance,
                InstanceTask.Kind.DOWNLOAD,
                download_mods_task,
                kwargs={
                    "instance_id": instance.id,
                    "user": user.username,
                    "name": instance.name,
                    "file_path": instance.preset.path,
                    "mods_directory": config.get("paths.mods_directory"),
                },
            )
        except InstanceLifecycleBusy:
            return Response(
                {"message": "Nie można pobierać modów podczas aktywnej operacji instancji."},
                status=400,
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

        task_result = AsyncResult(task_id)
        if not task_result:
            return Response({"message": "Zadanie nie zostało znalezione"}, status=404)

        res = task_result.result
        if isinstance(res, Exception) or task_result.status == "FAILURE":
            # Older failures and generic exceptions may contain command arguments.
            # Keep the existing shape with an allowlisted message independent of
            # the exception or result value provided by the backend.
            res = {"status": "Nie udało się wykonać zadania."}
            
        result = {
            "id": task_id,
            "state": task_result.status,
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

        log_path = instance.log_file.path if instance.log_file else None
        if log_path and os.path.exists(log_path):
            if tail:
                with open(log_path, 'rb') as f:
                    f.seek(0, os.SEEK_END)
                    buffer_size = 4096
                    file_size = f.tell()
                    buffer = b''
                    lines_found = 0
                    
                    # Read file in chunks from the end
                    while lines_found < tail and file_size > 0:
                        read_pos = max(0, file_size - buffer_size)
                        f.seek(read_pos)
                        chunk = f.read(min(buffer_size, file_size))
                        buffer = chunk + buffer
                        lines_found = buffer.count(b'\n')
                        file_size -= buffer_size
                    
                    lines = buffer.splitlines()
                    content = b'\n'.join(lines[-tail:]).decode(errors='replace')
            else:
                with open(log_path, 'r', errors='replace') as f:
                    content = f.read()
            return Response(content, content_type="text/plain", status=200)
        else:
            return Response({"message": "Brak logów lub plik nie istnieje."}, status=404)

    @action(detail=True, methods=['get'], url_path='logs/download')
    def download_logs(self, request, pk=None):
        try:
            instance = self._get_authorized_instance(pk)
        except Http404:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)

        log_path = instance.log_file.path if instance.log_file else None
        if log_path and os.path.exists(log_path):
            with open(log_path, 'r', errors='replace') as f:
                        logs = f.read()
        else:
            return Response({"message": "Brak logów do pobrania"}, status=404)

        response = HttpResponse(logs, content_type="text/plain")
        response['Content-Disposition'] = f'attachment; filename="logs_{pk}.txt"'
        return response
    
    @action(detail=True, methods=['delete'], url_path='logs/delete')
    def delete_logs(self, request, pk=None):
        try:
            instance = self._get_authorized_instance(pk)
        except Http404:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)
        
        if is_instance_lifecycle_busy(instance):
            message = (
                "Nie można usunąć logów podczas działania instancji"
                if instance.is_running
                else "Nie można usunąć logów podczas aktywnej operacji instancji"
            )
            return Response(
                {"message": message},
                status=400,
            )

        if not instance.log_file:
            return Response({"message": "Brak logów do usunięcia"}, status=404)

        instance.log_file.delete()
        instance.log_file = None
        instance.save()
        return Response({"message": "Logi zostały usunięte"}, status=200)
    
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
