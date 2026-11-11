import os
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.base_user import BaseUserManager


def _single_delete_result(instance, result):
    cleanup_pending = result.cleanup_pending
    instance._asm_cleanup_pending = (
        None
        if cleanup_pending is None
        else {
            "pending": True,
            "reason": cleanup_pending.reason,
            "pending_count": cleanup_pending.pending_count,
        }
    )
    instance.pk = None
    return result.delete_result


class InstanceLifecycleLock(models.Model):
    """One database row serializes lifecycle admission across all instances."""

    id = models.PositiveSmallIntegerField(primary_key=True)



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
    
    def delete(self, using=None, keep_parents=False):
        """Delete one profile through the lifecycle-locked managed-file service."""
        from .instance_management import delete_profile
        from .instance_lifecycle import require_lifecycle_autocommit
        from .utils.config import config

        require_lifecycle_autocommit()
        result = delete_profile(
            profile_id=self.pk,
            config_snapshot=config.snapshot(),
            using=using,
            keep_parents=keep_parents,
        )
        return _single_delete_result(self, result)
        

def user_presets_directory_path(instance, filename):
    return 'user_{0}/presets/{1}'.format(instance.user.id, filename)

def user_logs_directory_path(instance, filename):
    return 'user_{0}/logs/instance_{1}_{2}'.format(instance.user.id, instance.id, filename)

class Instances(models.Model):
    class LifecycleState(models.TextChoices):
        STOPPED = "stopped", "Stopped"
        STARTING = "starting", "Starting"
        RUNNING = "running", "Running"
        STOPPING = "stopping", "Stopping"
        UNKNOWN = "unknown", "Unknown"

    name = models.CharField(max_length=255, unique=True)
    user = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='instances')
    preset = models.FileField(upload_to=user_presets_directory_path, unique=True)
    log_file = models.FileField(upload_to=user_logs_directory_path, blank=True, null=True)
    start_file_path = models.CharField(max_length=255, blank=True, null=True)
    port = models.OneToOneField('Ports', on_delete=models.CASCADE, related_name='instance')
    pid = models.IntegerField(null=True, blank=True)
    is_ready = models.BooleanField(default=False)
    is_running = models.BooleanField(default=False)
    is_admin_instance = models.BooleanField(default=False)
    run_id = models.UUIDField(null=True, blank=True)
    process_created_at = models.FloatField(null=True, blank=True)
    process_executable = models.CharField(max_length=1024, null=True, blank=True)
    lifecycle_task_id = models.CharField(max_length=255, null=True, blank=True)
    start_claimed_at = models.DateTimeField(null=True, blank=True)
    timeout_task_id = models.CharField(max_length=255, null=True, blank=True)
    lifecycle_state = models.CharField(
        max_length=16,
        choices=LifecycleState.choices,
        default=LifecycleState.STOPPED,
    )
    shutdown_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
    
    def delete(self, using=None, keep_parents=False):
        """Delete one instance through the lifecycle-locked managed-file service."""
        from .instance_management import delete_instance
        from .instance_lifecycle import require_lifecycle_autocommit
        from .utils.config import config

        require_lifecycle_autocommit()
        result = delete_instance(
            instance_id=self.pk,
            config_snapshot=config.snapshot(),
            using=using,
            keep_parents=keep_parents,
        )
        return _single_delete_result(self, result)


class InstanceTask(models.Model):
    """Persist the instance that owns a task before it is sent to Celery."""

    class Kind(models.TextChoices):
        START = "start", "Start"
        STOP = "stop", "Stop"
        DOWNLOAD = "download", "Download"

    class AttentionReason(models.TextChoices):
        PROCESS_CLEANUP_UNCONFIRMED = (
            "process_cleanup_unconfirmed",
            "Process cleanup unconfirmed",
        )
        WORKER_STATE_UNVERIFIED = "worker_state_unverified", "Worker state unverified"

    task_id = models.CharField(primary_key=True, max_length=255)
    instance = models.ForeignKey(
        Instances,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    requires_attention = models.BooleanField(default=False)
    attention_reason = models.CharField(
        max_length=64,
        choices=AttentionReason.choices,
        blank=True,
        default="",
    )
    attention_workspace = models.CharField(max_length=2048, blank=True, default="")

    def __str__(self):
        return f"{self.kind}:{self.task_id}"
    
class Ports(models.Model):
    port_number = models.IntegerField(unique=True)
    is_available = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.port_number}"
    
class PreparedPreset(models.Model):
    name = models.CharField(max_length=255)
    name_key = models.CharField(max_length=255, unique=True, editable=False)
    preset_file = models.FileField(upload_to='prepared_presets/', max_length=255, unique=True)


class Missions(models.Model):
    mission_file = models.FileField(upload_to='missions/')
    
    def __str__(self):
        return os.path.basename(self.mission_file.name)

    def delete(self, *args, **kwargs):
        """Delete the stored mission file when the model instance is deleted."""
        if self.mission_file and os.path.exists(self.mission_file.path):
            os.remove(self.mission_file.path)
        super().delete(*args, **kwargs)
