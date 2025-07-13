from django.test import SimpleTestCase
from django.urls import reverse, resolve
from main.views import LoginViewset, AccountViewset, ServicesViewset

class TestUrls(SimpleTestCase):

    def test_login_url_is_resolved(self):
        url = reverse('login-list')
        self.assertEqual(resolve(url).func.cls, LoginViewset)

    def test_account_url_is_resolved(self):
        url = reverse('account-list')
        self.assertEqual(resolve(url).func.cls, AccountViewset)
        
    def test_services_url_is_resolved(self):
        url = reverse('services-list')
        self.assertEqual(resolve(url).func.cls, ServicesViewset)
