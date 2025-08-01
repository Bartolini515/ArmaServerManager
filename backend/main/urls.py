from rest_framework.routers import DefaultRouter
from .views import *
from django.conf import settings

router = DefaultRouter(trailing_slash=True)
router.register('login', LoginViewset, basename='login')
router.register('account', AccountViewset, basename='account')
router.register('services', ServicesViewset, basename='services')
router.register('instances', InstancesViewset, basename='instances')
router.register('moderator_panel', ModeratorPanelViewset, basename='moderator_panel')
router.register('missions', MissionsViewset, basename='missions')
urlpatterns = router.urls
if not settings.DEBUG:
    urlpatterns = [url for url in urlpatterns if url.name != 'api-root']