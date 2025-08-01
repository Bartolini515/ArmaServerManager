from django.http import HttpResponse
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
from .modpreset.modpathing import check_installed
from .modpreset.preset_extraction import preset_parser
from .modpreset.start_files import generate_sh_file, check_sh_file_exists, generate_server_config
from .utils.config import config
from .utils.logger import Logger
from celery.result import AsyncResult
from .tasks import *
from django.core.cache import cache
from servermanager.celery import app as celery_app
User = get_user_model()

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
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = InstanceSerializer
    queryset = Instances.objects.all()

    def list(self, request):
        user = self.request.user
        
        queryset = self.queryset.filter(user=user)
        serializer = self.serializer_class(queryset, many=True)
        if user.is_staff:
            admin_instances = self.queryset.filter(is_admin_instance=True)
            admin_serializer = self.serializer_class(admin_instances, many=True)
        return Response({
            'user_instances': serializer.data,
            'admin_instances': admin_serializer.data if user.is_staff else []
        })

    def create(self, request):
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
            
            create_logger = Logger(name="create", user=user.username)
            mod_paths = preset_parser(instance.preset.path, log_callback=create_logger.log)
            if not user.server_config:
                server_config_file_path = generate_server_config(user.username, "test", config.get("paths.arma3"), log_callback=create_logger.log)
                user.server_config = server_config_file_path
                user.save()
            start_file_path = generate_sh_file(instance.name, instance.port.port_number, user.username, mod_paths, config.get("paths.mods_directory"), config.get("paths.arma3"), log_callback=create_logger.log)
            instance.start_file_path = start_file_path
            instance.save()
            create_logger.write_log_to_file()

            return message_response(self.serializer_class(instance).data, "Instancja została utworzona")
        else:
            return Response(serializer.errors, status=400)
    
    def destroy(self, request, pk=None):
        try:
            instance = self.get_object()
        except Instances.DoesNotExist:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)
        
        if instance.is_running:
            return Response({"message": "Nie można usunąć działającej instancji"}, status=400)
        
        with transaction.atomic():
            instance.delete()
            
        return Response({"message": "Instancja została usunięta"}, status=200)
    
    @action(detail=True, methods=["post"], url_path="start")
    def start(self, request, pk=None):
        user = self.request.user
        
        try:
            instance = Instances.objects.get(pk=pk)
            if instance.is_admin_instance and user.is_staff:
                user = get_super_user()
                if not user:
                    return Response({"message": "Nie znaleziono administratora."}, status=404)
        except Instances.DoesNotExist:
            return Response({"message": "Instancja nie została znaleziona. Skontaktuj się z administratorem"}, status=404)

        if instance.is_running:
            return Response({"message": "Instancja jest już uruchomiona"}, status=400)
        
        if self.queryset.filter(user=user, is_running=True).exists():
            return Response({"message": "Nie można posiadać uruchomionej więcej niż jednej instancji"}, status=400)
        
        if self.queryset.filter(is_admin_instance=False, is_running=True).count() >= 5 and not user.is_staff:
            return Response({"message": "Przekroczono generalny limit 5 uruchomionych instancji. Spróbuj ponownie później."}, status=403)
        
        start_logger = Logger(name="start", user=user.username)

        if not instance.log_file:
            log_filename = f"log_file.txt"
            log_content = f"Log file for instance '{instance.name}' created at {timezone.now()}.\n"
            content_file = ContentFile(log_content.encode('utf-8'), name=log_filename)
            instance.log_file.save(log_filename, content_file, save=True)

        workshop_ids = preset_parser(instance.preset.path, log_callback=start_logger.log)
        mods_dir = config.get("paths.mods_directory")
        if check_installed(wids=workshop_ids, mods_dir=mods_dir, log_callback=start_logger.log)[1]:
            start_logger.write_log_to_file()
            instance.is_ready = False
            return Response({"message": "Niektóre mody są niezainstalowane. Proszę je najpierw pobrać."}, status=400)
        if not check_sh_file_exists(instance.name, log_callback=start_logger.log):
            start_logger.write_log_to_file()
            return Response({"message": "Plik startowy instancji nie istnieje. Skontaktuj się z administratorem"}, status=400)

        try:
            task = start_server_task.delay(
                instance_id=instance.id,
                arma3_dir=config.get("paths.arma3"),
            )
        except Exception as e:
            start_logger.log(f"Nie udało się rozpocząć zadania uruchamiania: {str(e)}")
            start_logger.write_log_to_file()
            return Response({"message": f"Nie udało się rozpocząć zadania uruchamiania: {str(e)}"}, status=500)
        
        if not instance.is_admin_instance:
            cache_key = f"terminate_task_{instance.id}"
            old_terminate_task = cache.get(cache_key)
            if old_terminate_task:
                celery_app.control.revoke(old_terminate_task, terminate=True)
            async_result = instance_timeout_task.apply_async(args=[instance.id], countdown=3600)
            cache.set(cache_key, async_result.id, timeout=3660) # longer than task timeout
        
        start_logger.write_log_to_file()
        return Response({"task_id": task.id}, status=202)

    @action(detail=True, methods=["post"], url_path="stop")
    def stop(self, request, pk=None):
        try:
            instance = Instances.objects.get(pk=pk)
        except Instances.DoesNotExist:
            return Response({"message": "Instancja nie została znaleziona. Skontaktuj się z administratorem"}, status=404)

        if not instance.is_running:
            return Response({"message": "Instancja jest już zatrzymana"}, status=400)

        try:
            task = stop_server_task.delay(
                instance_id=instance.id
            )
        except Exception as e:
            return Response({"message": f"Nie udało się rozpocząć zadania zatrzymania: {str(e)}"}, status=500)
        return Response({"task_id": task.id}, status=202)

    @action(detail=True, methods=["post"], url_path="admin_instance/change_preset", permission_classes=[permissions.IsAdminUser])
    def change_preset(self, request, pk=None):
        try:
            user = get_super_user()
        except Profile.DoesNotExist:
            return Response({"message": "Nie znaleziono administratora."}, status=404)
        
        try:
            instance = Instances.objects.get(pk=pk)
        except Instances.DoesNotExist:
            return Response({"message": "Instancja nie została znaleziona."}, status=404)
        
        if not instance.is_admin_instance:
            return Response({"message": "To nie jest instancja administracyjna."}, status=400)
        
        if instance.is_running:
            return Response({"message": "Nie można zmienić presetu działającej instancji."}, status=400)
        
        

        serializer = self.serializer_class(instance, data=request.data, partial=True)
        if serializer.is_valid():
            if instance.preset and os.path.exists(instance.preset.path):
                os.remove(instance.preset.path)
            instance.preset = serializer.validated_data["preset"]
            instance.save()
        else:
            return Response(serializer.errors, status=400)
        
        change_preset_logger = Logger(name="change_preset", user=user.username)
        mod_paths = preset_parser(instance.preset.path, log_callback=change_preset_logger.log)
        
        if instance.start_file_path and os.path.exists(instance.start_file_path):
            os.remove(instance.start_file_path)
        start_file_path = generate_sh_file(instance.name, instance.port.port_number, user.username, mod_paths, config.get("paths.mods_directory"), config.get("paths.arma3"), log_callback=change_preset_logger.log)
        instance.start_file_path = start_file_path
        instance.is_ready = False
        instance.save()
        
        change_preset_logger.write_log_to_file()
        return message_response(self.serializer_class(instance).data, "Preset instancji głównej został zmieniony")

    @action(detail=True, methods=['post'], url_path='download_mods')
    def download_mods(self, request, pk=None):
        instance = Instances.objects.get(pk=pk)
        if instance.is_admin_instance:
            user = get_super_user()
            if not user:
                return Response({"message": "Nie znaleziono administratora."}, status=404)
        else:
            user = self.request.user
        
        try:
            task = download_mods_task.delay(
                instance_id=instance.id,
                user=user.username,
                name=instance.name,
                file_path=instance.preset.path,
                mods_directory=config.get("paths.mods_directory")
            )
        except Exception as e:
            return Response({"message": f"Nie udało się rozpocząć zadania pobierania: {str(e)}"}, status=500)
        return Response({'task_id': task.id}, status=202)

    @action(detail=False, methods=['get'], url_path='task_status/(?P<task_id>[^/.]+)')
    def task_status(self, request, task_id=None):
        task_result = AsyncResult(task_id)
        if not task_result:
            return Response({"message": "Zadanie nie zostało znalezione"}, status=404)

        res = task_result.result
        if isinstance(res, Exception):
            res = {"status": str(res)}
            
        result = {
            "id": task_id,
            "state": task_result.status,
            "result": res
        }
        return Response(result, status=200)

    @action(detail=True, methods=['get'], url_path='logs')
    def logs(self, request, pk=None):
        try:
            instance = Instances.objects.get(pk=pk)
        except Instances.DoesNotExist:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)

        logs = instance.get_logs()
        return Response(logs, status=200)

    @action(detail=True, methods=['get'], url_path='logs/download')
    def download_logs(self, request, pk=None):
        try:
            instance = Instances.objects.get(pk=pk)
        except Instances.DoesNotExist:
            return Response({"message": "Instancja nie została znaleziona"}, status=404)

        logs = instance.get_logs()
        if not logs:
            return Response({"message": "Brak logów do pobrania"}, status=404)

        response = HttpResponse(logs, content_type="text/plain")
        response['Content-Disposition'] = f'attachment; filename="logs_{pk}.txt"'
        return response
    
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