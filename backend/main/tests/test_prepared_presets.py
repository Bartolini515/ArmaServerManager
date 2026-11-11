from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.db import IntegrityError
from rest_framework.test import APIClient


class PreparedPresetTests(TransactionTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ('media', 'arma', '.asm-private', 'static'):
            (root / name).mkdir()
        override = override_settings(MEDIA_ROOT=str(root / 'media'), STATIC_ROOT=str(root / 'static'))
        override.enable()
        self.addCleanup(override.disable)
        patcher = patch('main.views.config.snapshot', return_value={'paths': {'arma3': str(root / 'arma'), 'logs_directory': str(root / 'logs')}})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = get_user_model().objects.create_user('admin', 'fixture', is_staff=True)
        self.user = get_user_model().objects.create_user('user', 'fixture')
        self.client = APIClient()

    def upload(self, name='Paczka', filename='mods.html'):
        return self.client.post('/api/prepared-presets/', {'name': name, 'preset_file': SimpleUploadedFile(filename, b'<html></html>')}, format='multipart')

    def test_admin_upload_and_authenticated_listing(self):
        self.client.force_authenticate(self.admin)
        created = self.upload()
        self.assertEqual(created.status_code, 201, created.data)
        self.client.force_authenticate(self.user)
        response = self.client.get('/api/prepared-presets/')
        self.assertEqual(response.data[0]['name'], 'Paczka')
        self.assertEqual(self.upload('Inna').status_code, 403)

    def test_duplicate_display_name_ignores_case_and_file_names_do_not_collide(self):
        self.client.force_authenticate(self.admin)
        first = self.upload()
        self.assertEqual(first.status_code, 201, first.data)
        duplicate = self.upload('pACZKA')
        self.assertEqual(duplicate.status_code, 400)
        other = self.upload('Inna')
        self.assertEqual(other.status_code, 201, other.data)
        self.assertNotEqual(first.data['preset_file'], other.data['preset_file'])

    def test_admin_deletion_removes_file(self):
        from main.models import PreparedPreset
        self.client.force_authenticate(self.admin)
        created = self.upload()
        self.assertEqual(created.status_code, 201, created.data)
        record = PreparedPreset.objects.get(pk=created.data['id'])
        path = Path(record.preset_file.path)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.delete(f'/api/prepared-presets/{record.pk}/').status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(f'/api/prepared-presets/{record.pk}/').status_code, 204)
        self.assertFalse(path.exists())

    def test_failed_database_write_rolls_back_published_file(self):
        from main.models import PreparedPreset
        self.client.force_authenticate(self.admin)
        with patch.object(PreparedPreset.objects, 'create', side_effect=IntegrityError('fixture')):
            response = self.upload()
        self.assertEqual(response.status_code, 400)
        self.assertFalse((Path(self.temp.name) / 'media' / 'prepared_presets' / 'mods.html').exists())
