from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from knox.models import AuthToken

User = get_user_model()

class TestLoginViewset(TestCase):

    def setUp(self):
        self.client = Client()
        self.login_url = reverse('login-list')
        self.user = User.objects.create_user(username='testuser', password='testpassword')

    def test_login_successful(self):
        response = self.client.post(self.login_url, {'username': 'testuser', 'password': 'testpassword'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('token', response.data)

    def test_login_unsuccessful_wrong_password(self):
        response = self.client.post(self.login_url, {'username': 'testuser', 'password': 'wrongpassword'}, format='json')
        self.assertEqual(response.status_code, 401)

    def test_login_unsuccessful_wrong_username(self):
        response = self.client.post(self.login_url, {'username': 'wronguser', 'password': 'testpassword'}, format='json')
        self.assertEqual(response.status_code, 401)

class TestAccountViewset(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='testpassword')
        _, self.token = AuthToken.objects.create(self.user)
        self.auth_headers = {'HTTP_AUTHORIZATION': f'Token {self.token}'}

        self.get_user_url = reverse('account-get-user')
        self.change_password_url = reverse('account-change-password')

    def test_get_user_authenticated(self):
        response = self.client.get(self.get_user_url, **self.auth_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['user']['username'], 'testuser')

    def test_get_user_unauthenticated(self):
        response = self.client.get(self.get_user_url)
        self.assertEqual(response.status_code, 401)

    def test_change_password_authenticated(self):
        new_password = 'newpassword123'
        response = self.client.post(self.change_password_url, {'password': new_password}, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))

    def test_change_password_unauthenticated(self):
        response = self.client.post(self.change_password_url, {'password': 'newpassword123'}, content_type='application/json')
        self.assertEqual(response.status_code, 401)
