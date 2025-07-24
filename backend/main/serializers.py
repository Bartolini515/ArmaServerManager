from datetime import datetime
from rest_framework import serializers
from .models import *
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
import re
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
        fields = ('id', 'name', 'user', 'preset', 'start_file_path', 'created_at', 'is_admin_instance', 'is_ready', 'is_running', 'port')