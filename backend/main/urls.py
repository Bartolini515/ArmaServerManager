from rest_framework.routers import DefaultRouter
from .views import *
from django.conf import settings

router = DefaultRouter(trailing_slash=True)
router.register('timetable', TimetableViewset, basename='timetable')
router.register('login', LoginViewset, basename='login')
router.register('moderator_panel', ModeratorPanelViewset, basename='moderator_panel')
router.register('account', AccountViewset, basename='account')
router.register('tasks', TasksViewset, basename='tasks')
router.register('schedule', ScheduleViewset, basename='schedule')
urlpatterns = router.urls
if not settings.DEBUG:
    urlpatterns = [url for url in urlpatterns if url.name != 'api-root']