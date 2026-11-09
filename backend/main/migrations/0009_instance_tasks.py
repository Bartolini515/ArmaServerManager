# Manually authored for the persisted task-to-instance ownership binding.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0008_alter_instances_user"),
    ]

    operations = [
        migrations.CreateModel(
            name="InstanceTask",
            fields=[
                (
                    "task_id",
                    models.CharField(max_length=255, primary_key=True, serialize=False),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("start", "Start"),
                            ("stop", "Stop"),
                            ("download", "Download"),
                        ],
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "instance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tasks",
                        to="main.instances",
                    ),
                ),
            ],
        ),
    ]
