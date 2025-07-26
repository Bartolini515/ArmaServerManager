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
    server_config = models.CharField(max_length=1024, blank=True, null=True)

    objects = ProfileManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = []


    def __str__(self):
        return self.username
    
    def delete(self, *args, **kwargs):
        if self.server_config and os.path.exists(self.server_config):
            os.remove(self.server_config)
        super().delete(*args, **kwargs)
        

def user_presets_directory_path(instance, filename):
    return 'user_{0}/presets/{1}'.format(instance.user.id, filename)

def user_logs_directory_path(instance, filename):
    return 'user_{0}/logs/instance_{1}_{2}'.format(instance.user.id, instance.id, filename)

class Instances(models.Model):
    name = models.CharField(max_length=255, unique=True)
    user = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='instances', null=True, blank=True)
    preset = models.FileField(upload_to=user_presets_directory_path, unique=True)
    log_file = models.FileField(upload_to=user_logs_directory_path, blank=True, null=True)
    start_file_path = models.CharField(max_length=255, blank=True, null=True)
    port = models.OneToOneField('Ports', on_delete=models.CASCADE, related_name='instance')
    pid = models.IntegerField(null=True, blank=True)
    is_ready = models.BooleanField(default=False)
    is_running = models.BooleanField(default=False)
    is_admin_instance = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
    
    def delete(self, *args, **kwargs):
        if self.preset and os.path.exists(self.preset.path):
            os.remove(self.preset.path)
        if self.log_file and os.path.exists(self.log_file.path):
            os.remove(self.log_file.path)
        if self.start_file_path and os.path.exists(self.start_file_path):
            os.remove(self.start_file_path)
        if self.port:
            self.port.is_available = True
            self.port.save()
        super().delete(*args, **kwargs)
    
class Ports(models.Model):
    port_number = models.IntegerField(unique=True)
    is_available = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.port_number}"