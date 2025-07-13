from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError

User = get_user_model()

class TestModels(TestCase):

    def test_create_user(self):
        user = User.objects.create_user(username='testuser', password='testpassword')
        self.assertEqual(user.username, 'testuser')
        self.assertTrue(user.check_password('testpassword'))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_superuser(self):
        superuser = User.objects.create_superuser(username='superuser', password='superpassword')
        self.assertEqual(superuser.username, 'superuser')
        self.assertTrue(superuser.check_password('superpassword'))
        self.assertTrue(superuser.is_staff)
        self.assertTrue(superuser.is_superuser)

    def test_create_user_no_username(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(username='', password='testpassword')

    def test_username_is_unique(self):
        User.objects.create_user(username='testuser', password='testpassword')
        with self.assertRaises(IntegrityError):
            User.objects.create_user(username='testuser', password='anotherpassword')

    def test_profile_str_representation(self):
        user = User.objects.create_user(username='testuser', password='testpassword')
        self.assertEqual(str(user), 'testuser')
