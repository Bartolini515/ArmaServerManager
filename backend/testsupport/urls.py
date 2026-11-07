"""Root URL configuration for isolated backend tests."""

from django.urls import include, path


urlpatterns = [path("api/", include("main.urls"))]
