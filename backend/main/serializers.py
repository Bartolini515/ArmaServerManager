from datetime import datetime
from rest_framework import serializers
from .models import *
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
import re
import base64
User = get_user_model()

# ProfilesSerializers
class ProfileSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = Profile
        fields = ('id', 'username', 'last_login')

class LoginSerializer(serializers.ModelSerializer):
    username = serializers.CharField()
    password = serializers.CharField()
    
    class Meta:
        model = Profile
        fields = ('username', 'password')
    
class Password_changeSerializer(serializers.Serializer):
    password = serializers.CharField()
    
    def validate_password(self, value):
        try:
            validate_password(value)
        except ValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value
    
    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret.pop('password', None)
        return ret

# moderatorPanelSerializers
class Profiles_moderatorPanelSerializer(serializers.ModelSerializer):
    
    def validate_password(self, value):
        if value:
            try:
                validate_password(value)
            except ValidationError as e:
                raise serializers.ValidationError(e.messages)
        return value
    
    def create(self, validated_data):
        return User.objects.create_user(**validated_data)
    
    def update(self, instance, validated_data):
        instance.username = validated_data.get('username', instance.username)
        password = validated_data.get('password', None)
        if password:
            instance.set_password(password)
        instance.save()
        return instance
    
    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret.pop('password', None)
        return ret
    
    class Meta:
        model = Profile
        fields = ("id" , "username", "last_login", "password")
        
class ConfigSerializer(serializers.Serializer):
    paths = serializers.DictField(child=serializers.CharField(), required=True)
    steam_auth = serializers.DictField(child=serializers.CharField(allow_blank=True), required=True)
    
    def validate_paths(self, value):
        # Place for validation logic in the future
        return value
    
    def validate_steam_auth(self, value):
        # Validate shared_secret if provided
        shared_secret = value.get('shared_secret', '')
        if shared_secret and not self._is_valid_base64(shared_secret):
            raise serializers.ValidationError({
                'shared_secret': ["Nieprawidłowy format. Oczekiwano ciągu base64"]
            })
        
        return value
    
    def _is_valid_base64(self, value):
        """Check if the value is valid base64 encoded string."""
        try:
            # Base64 strings should only contain A-Z, a-z, 0-9, +, /, and = for padding
            if not re.match(r'^[A-Za-z0-9+/]*={0,2}$', value):
                return False
            # Try to decode to verify it's valid base64
            base64.b64decode(value, validate=True)
            return True
        except Exception:
            return False
    
    def to_representation(self, instance):
        """Hide sensitive information in the response."""
        ret = super().to_representation(instance)
        
        # Mask sensitive fields
        if 'steam_auth' in ret and isinstance(ret['steam_auth'], dict):
            if 'password' in ret['steam_auth'] and ret['steam_auth']['password']:
                ret['steam_auth']['password'] = '*' * 8
            if 'shared_secret' in ret['steam_auth'] and ret['steam_auth']['shared_secret']:
                ret['steam_auth']['shared_secret'] = '*' * 8
        
        return ret

# InstancesSerializers
class InstanceSerializer(serializers.ModelSerializer):
    user = ProfileSerializer(read_only=True)
    port = serializers.StringRelatedField()
    
    def validate_name(self, value):
        if len(value) < 3:
            raise serializers.ValidationError("Nazwa musi mieć co najmniej 3 znaki.")
        if not re.match(r'^[A-Za-z0-9_-]+$', value):
            raise serializers.ValidationError(
                "Nazwa może zawierać tylko litery ASCII, cyfry, podkreślenia i myślniki."
            )
        return value

    def validate_preset(self, value):
        filename = value.name if hasattr(value, 'name') else str(value)
        if len(filename) < 3:
            raise serializers.ValidationError("Nazwa pliku preset musi mieć co najmniej 3 znaki.")
        if not re.match(r'^[A-Za-z0-9_.-]+$', filename):
            raise serializers.ValidationError(
                "Nazwa pliku preset może zawierać tylko litery ASCII, cyfry, podkreślenia, kropki i myślniki."
            )
        return value
    
    class Meta:
        model = Instances
        fields = ('id', 'name', 'user', 'preset', 'log_file', 'start_file_path', 'port', 
                  'pid', 'is_ready', 'is_running', 'is_admin_instance', 'created_at')