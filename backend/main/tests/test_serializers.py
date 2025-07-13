from django.test import TestCase
from main.serializers import ProfileSerializer, LoginSerializer, Password_changeSerializer
from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError

User = get_user_model()

class TestSerializers(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpassword')

    def test_profile_serializer(self):
        serializer = ProfileSerializer(instance=self.user)
        data = serializer.data
        self.assertEqual(set(data.keys()), set(['id', 'username', 'last_login']))
        self.assertEqual(data['username'], 'testuser')

    def test_login_serializer_valid(self):
        data = {'username': 'testuser', 'password': 'testpassword'}
        serializer = LoginSerializer(data=data)
        self.assertTrue(serializer.is_valid())

    def test_login_serializer_invalid(self):
        data = {'username': 'testuser'} # Missing password
        serializer = LoginSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_password_change_serializer_valid(self):
        data = {'password': 'new_password123'}
        serializer = Password_changeSerializer(data=data)
        self.assertTrue(serializer.is_valid())

    def test_password_change_serializer_invalid_short_password(self):
        data = {'password': 'short'}
        serializer = Password_changeSerializer(data=data)
        with self.assertRaises(ValidationError):
            serializer.is_valid(raise_exception=True)
