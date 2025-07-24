from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from .serializers import *
from rest_framework.response import Response
from .models import *
from django.contrib.auth import get_user_model, authenticate
from django.utils import timezone
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
from .tasks import download_mods_task
User = get_user_model()

def message_response(data, message="Operacja się powiodła"):
    return Response({
        "result": data,
        "message": message
    })

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

class AccountViewset(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProfileSerializer
    queryset = Profile.objects.all()
    
    def list(self, request):
        queryset = Profile.objects.all()
        serializer = self.serializer_class(queryset, many=True)
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

class ServicesViewset(viewsets.ModelViewSet):
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
                return Response({"message": "Przekroczono limit 5 instancji"}, status=400)
            with transaction.atomic():
                available_port = Ports.objects.select_for_update().filter(is_available=True).first()
                if not available_port:
                    return Response({"message": "Brak dostępnych portów"}, status=400)

                instance = serializer.save(user=user, port=available_port)
                
                available_port.is_available = False
                available_port.save()
            
            create_logger = Logger(name="create", user=user.username)
            mod_paths = preset_parser(instance.preset.path, log_callback=create_logger.log)
            generate_server_config(user.username, user.password, config.get("paths.arma3"), log_callback=create_logger.log)
            generate_sh_file(instance.name, instance.port.port_number, user.username, mod_paths, config.get("paths.mods_directory"), config.get("paths.arma3"), log_callback=create_logger.log)
            create_logger.write_log_to_file()

            return message_response(self.serializer_class(instance).data, "Instancja została utworzona")
        else:
            return Response(serializer.errors, status=400)
    @action(detail=True, methods=["delete"], url_path="delete")
    def delete(self, request, pk=None):
        instance = Instances.objects.get(pk=pk)
        if instance.is_running:
            return Response({"message": "Nie można usunąć działającej instancji"}, status=400)
        port = instance.port
        if port:
            port.is_available = True
            port.save()
        instance.preset.delete()
        instance.delete()
        return Response(status=204)
    
    @action(detail=True, methods=["post"], url_path="start")
    def start(self, request, pk=None):
        user = self.request.user
        start_logger = Logger(name="start", user=user.username)
        
        try:
            instance = Instances.objects.get(pk=pk)
        except Instances.DoesNotExist:
            return Response({"message": "Instancja nie została znaleziona. Skontaktuj się z administratorem"}, status=404)

        if instance.is_running:
            return Response({"message": "Instancja jest już uruchomiona"}, status=400)

        workshop_ids = preset_parser(instance.preset.path, log_callback=start_logger.log)
        mods_dir = config.get("paths.mods_directory")
        if check_installed(wids=workshop_ids, mods_dir=mods_dir, log_callback=start_logger.log)[1]:
            start_logger.write_log_to_file()
            instance.is_ready = False
            return Response({"message": "Niektóre mody są nieinstalowane. Proszę je najpierw pobrać."}, status=400)
        if not check_sh_file_exists(instance.name, log_callback=start_logger.log):
            start_logger.write_log_to_file()
            return Response({"message": "Plik startowy instancji nie istnieje. Skontaktuj się z administratorem"}, status=400)

        # instance.start() # Placeholder
        start_logger.write_log_to_file()
        return Response({"message": "Instancja została uruchomiona"})
    
    @action(detail=True, methods=['post'], url_path='download_mods')
    def download_mods(self, request, pk=None):
        instance = Instances.objects.get(pk=pk)
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