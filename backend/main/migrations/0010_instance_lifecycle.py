from django.db import migrations, models


def seed_lifecycle_lock_and_mark_legacy_instances(apps, schema_editor):
    database = schema_editor.connection.alias
    InstanceLifecycleLock = apps.get_model("main", "InstanceLifecycleLock")
    InstanceLifecycleLock.objects.using(database).get_or_create(pk=1)

    Instances = apps.get_model("main", "Instances")
    Instances.objects.using(database).filter(
        models.Q(is_running=True) | models.Q(pid__isnull=False)
    ).update(lifecycle_state="unknown")


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0009_instance_tasks"),
    ]

    operations = [
        migrations.CreateModel(
            name="InstanceLifecycleLock",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        primary_key=True,
                        serialize=False,
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="instancetask",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="instancetask",
            name="superseded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="instances",
            name="run_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="instances",
            name="process_created_at",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="instances",
            name="process_executable",
            field=models.CharField(blank=True, max_length=1024, null=True),
        ),
        migrations.AddField(
            model_name="instances",
            name="lifecycle_task_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="instances",
            name="start_claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="instances",
            name="timeout_task_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="instances",
            name="lifecycle_state",
            field=models.CharField(
                choices=[
                    ("stopped", "Stopped"),
                    ("starting", "Starting"),
                    ("running", "Running"),
                    ("stopping", "Stopping"),
                    ("unknown", "Unknown"),
                ],
                default="stopped",
                max_length=16,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="instances",
            name="shutdown_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            seed_lifecycle_lock_and_mark_legacy_instances,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
