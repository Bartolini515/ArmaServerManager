"""Django settings for tests that must not use production services or paths."""

from __future__ import annotations

import os
from pathlib import Path


def _required_test_path(variable: str) -> Path:
    value = os.environ.get(variable)
    if not value:
        raise RuntimeError(
            f"{variable} must be set by scripts/test_backend.py before Django starts"
        )
    return Path(value).resolve()


TEST_ROOT = _required_test_path("ASM_TEST_ROOT")
if not TEST_ROOT.is_dir():
    raise RuntimeError("ASM_TEST_ROOT must name an existing temporary directory")
if os.environ.get("ASM_TEST_ISOLATION_GUARDS") != "installed":
    raise RuntimeError("scripts/test_backend.py must install isolation guards first")


def _path_under_test_root(variable: str) -> Path:
    path = _required_test_path(variable)
    try:
        path.relative_to(TEST_ROOT)
    except ValueError as exc:
        raise RuntimeError(f"{variable} must be inside ASM_TEST_ROOT") from exc
    return path


CONFIG_PATH = _path_under_test_root("ASM_CONFIG_PATH")
MEDIA_ROOT = TEST_ROOT / "media"
STATIC_ROOT = TEST_ROOT / "static"
LOG_ROOT = TEST_ROOT / "logs"

for _directory in (MEDIA_ROOT, STATIC_ROOT, LOG_ROOT):
    _directory.mkdir(parents=True, exist_ok=True)


SECRET_KEY = "isolated-backend-tests-only"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "knox",
    "main",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "testsupport.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
WSGI_APPLICATION = "servermanager.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "arma-server-manager-isolated-tests",
    }
}

AUTH_USER_MODEL = "main.Profile"
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "knox.auth.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    )
}

CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_STORE_EAGER_RESULT = False
CELERY_BEAT_SCHEDULE = {}

LANGUAGE_CODE = "pl-pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
MEDIA_URL = "/media/"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
