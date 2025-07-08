from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from .serializers import *
from rest_framework.response import Response
from .models import *
from django.contrib.auth import get_user_model, authenticate
from django.utils import timezone
from knox.models import AuthToken
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
        serializer = ProfileSerializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=["post"], url_path="change_password")
    def change_password(self, request, token=None):
        try:
            token = request.headers['Authorization'][6:21]
            auth_token = AuthToken.objects.get(token_key=token[:15])
            user = auth_token.user
        except AuthToken.DoesNotExist:
            return Response({"message": "Invalid token"}, status=400)
        
        serializer = Password_changeSerializer(data=request.data)
        if serializer.is_valid():
            password = serializer.validated_data['password']   
            user.set_password(password)
            user.last_login = timezone.now()
            user.save()
            return Response({"message": "Hasło zmienione"})
        else:
            return Response(serializer.errors, status=400)
    
    @action(detail=False, methods=["get"], url_path="getuser")
    def getUser(self, request):
        try:
            token = request.headers['Authorization'][6:21]
            auth_token = AuthToken.objects.get(token_key=token[:15])
            user = auth_token.user
        except AuthToken.DoesNotExist:
            return Response({"message": "Invalid token"}, status=400)
        serializer = ProfileSerializer(self.queryset.get(pk=user.id))
        data = serializer.data
            
        return Response({'user': data, 'isAdmin': user.is_staff})
