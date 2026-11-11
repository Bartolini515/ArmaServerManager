from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from main.models import Instances, Missions, Ports


class MissionDeletionTests(TransactionTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ('media', 'arma', '.asm-private', 'static'):
            (root / name).mkdir()
        self.settings = override_settings(MEDIA_ROOT=str(root / 'media'), STATIC_ROOT=str(root / 'static'))
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        snapshot = {'paths': {'arma3': str(root / 'arma'), 'logs_directory': str(root / 'logs')}}
        patcher = patch('main.views.config.snapshot', return_value=snapshot)
        patcher.start()
        self.addCleanup(patcher.stop)
        users = get_user_model()
        self.admin = users.objects.create_user(username='admin', password='fixture', is_staff=True)
        self.user = users.objects.create_user(username='user', password='fixture')
        self.client = APIClient()
        self.mission = Missions.objects.create(mission_file=SimpleUploadedFile('mission.pbo', b'fixture'))

    def test_non_admin_cannot_delete(self):
        self.client.force_authenticate(self.user)
        response = self.client.delete(f'/api/missions/{self.mission.pk}/')
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Missions.objects.filter(pk=self.mission.pk).exists())

    def test_running_instance_blocks_deletion(self):
        port = Ports.objects.create(port_number=2302)
        Instances.objects.create(name='fixture', user=self.user, preset='fixture.html', port=port, is_running=True)
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f'/api/missions/{self.mission.pk}/')
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Missions.objects.filter(pk=self.mission.pk).exists())

    def test_admin_deletes_file_and_record(self):
        path = Path(self.mission.mission_file.path)
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f'/api/missions/{self.mission.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(path.exists())

    def test_missing_file_does_not_prevent_deletion(self):
        Path(self.mission.mission_file.path).unlink()
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f'/api/missions/{self.mission.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Missions.objects.exists())

    def test_unsafe_reference_is_rejected(self):
        self.mission.mission_file.name = '../outside.pbo'
        self.mission.save(update_fields=['mission_file'])
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f'/api/missions/{self.mission.pk}/')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Missions.objects.exists())

    def test_replacing_existing_mission_requires_staff(self):
        path = Path(self.mission.mission_file.path)
        self.client.force_authenticate(self.user)
        response = self.client.post('/api/missions/', {'mission_file': SimpleUploadedFile('mission.pbo', b'new')}, format='multipart')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(path.read_bytes(), b'fixture')

    def test_replacement_blocked_while_instance_running(self):
        port = Ports.objects.create(port_number=2302)
        Instances.objects.create(name='fixture', user=self.user, preset='fixture.html', port=port, is_running=True)
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/missions/', {'mission_file': SimpleUploadedFile('mission.pbo', b'new')}, format='multipart')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Path(self.mission.mission_file.path).read_bytes(), b'fixture')

    def test_staff_can_replace_mission_when_stopped(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/missions/', {'mission_file': SimpleUploadedFile('mission.pbo', b'new')}, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        mission = Missions.objects.get()
        self.assertEqual(Path(mission.mission_file.path).read_bytes(), b'new')

    def test_direct_mission_updates_are_disabled(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(f'/api/missions/{self.mission.pk}/', {'mission_file': SimpleUploadedFile('other.pbo', b'new')}, format='multipart')
        self.assertEqual(response.status_code, 405)
        self.assertEqual(Path(self.mission.mission_file.path).read_bytes(), b'fixture')
