import os
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.base_user import BaseUserManager

class ProfileManager(BaseUserManager):
    def create_user(self, username, password, **extra_fields):
        if not username:
            raise ValueError('Users must have a username')
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password, **extra_fields):
        extra_fields.setdefault( 'is_staff', True )
        extra_fields.setdefault( 'is_superuser', True )
        return self.create_user(username, password, **extra_fields)


class Profile(AbstractUser):
    username = models.CharField(max_length=255, unique=True)
    server_config = models.CharField(max_length=255, blank=True, null=True)
    
    
    objects = ProfileManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = []


    def __str__(self):
        return self.username
        

def user_presets_directory_path(instance, filename):
    return 'user_{0}/presets/{1}'.format(instance.user.id, filename)

class Instances(models.Model):
    name = models.CharField(max_length=255, unique=True)
    user = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='instances', null=True, blank=True)
    preset = models.FileField(upload_to=user_presets_directory_path, unique=True)
    port = models.OneToOneField('Ports', on_delete=models.CASCADE, related_name='instance')
    is_ready = models.BooleanField(default=False)
    is_running = models.BooleanField(default=False)
    is_admin_instance = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
    
class Ports(models.Model):
    port_number = models.IntegerField(unique=True)
    is_available = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.port_number}"