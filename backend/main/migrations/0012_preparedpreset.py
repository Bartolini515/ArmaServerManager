from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('main', '0011_download_attention')]
    operations = [
        migrations.CreateModel(name='PreparedPreset', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('name', models.CharField(max_length=255)),
            ('name_key', models.CharField(editable=False, max_length=255, unique=True)),
            ('preset_file', models.FileField(max_length=255, unique=True, upload_to='prepared_presets/')),
        ]),
    ]
