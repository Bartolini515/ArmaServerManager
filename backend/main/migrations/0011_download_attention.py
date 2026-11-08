from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0010_instance_lifecycle"),
    ]

    operations = [
        migrations.AddField(
            model_name="instancetask",
            name="requires_attention",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="instancetask",
            name="attention_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    (
                        "process_cleanup_unconfirmed",
                        "Process cleanup unconfirmed",
                    ),
                    ("worker_state_unverified", "Worker state unverified"),
                ],
                default="",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="instancetask",
            name="attention_workspace",
            field=models.CharField(blank=True, default="", max_length=2048),
        ),
    ]
