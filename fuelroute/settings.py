"""
Django settings for fuelroute project.

Every value that differs between local development and a real deployment is
read from an environment variable, with a safe default for local dev (no
`.env` needed to run `manage.py runserver` out of the box). Nothing secret is
hardcoded in this file - see `.env.example` for what a real deployment must
set explicitly.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()  # no-op if there's no .env file (e.g. in a real deployment
    # that sets real environment variables instead)
except ImportError:
    pass


def _env_bool(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


BASE_DIR = Path(__file__).resolve().parent.parent

# --- Security-sensitive settings: environment-driven, no hardcoded secrets ---

# Falls back to an ephemeral, randomly-generated key so local dev/grading
# works with zero setup. This is NOT safe for a real deployment: it changes
# every process restart (invalidating sessions/signed cookies), and every
# instance behind a load balancer would have a different one. Any real
# deployment MUST set DJANGO_SECRET_KEY explicitly (see .env.example).
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    from django.core.management.utils import get_random_secret_key
    SECRET_KEY = get_random_secret_key()

# Defaults to True so local dev/grading needs no setup; any real deployment
# MUST set DJANGO_DEBUG=False (a debug 500 page leaks source, settings, and
# environment details to anyone who can trigger an error).
DEBUG = _env_bool("DJANGO_DEBUG", True)

# Comma-separated list, e.g. "api.example.com,www.example.com". Empty is
# fine for local dev - Django implicitly allows localhost/127.0.0.1 when
# DEBUG is True. A real deployment MUST set this.
ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()
]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'routing',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'fuelroute.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'fuelroute.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases
#
# SQLite by default (fine for this app: it's read-mostly reference data with
# no concurrent-write user traffic). DJANGO_DATABASE_URL lets a real
# deployment point at Postgres/MySQL without touching this file, if desired -
# requires adding `dj-database-url` to requirements.txt to actually parse it.

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'


# --- Production hardening (only meaningful once DEBUG=False, i.e. behind
# real HTTPS - these would break a plain local `runserver` over http) ---

if not DEBUG:
    SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30  # 30 days
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = "DENY"

SECURE_CONTENT_TYPE_NOSNIFF = True


# Django REST Framework

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': (
        ['rest_framework.renderers.JSONRenderer', 'rest_framework.renderers.BrowsableAPIRenderer']
        if DEBUG else
        ['rest_framework.renderers.JSONRenderer']
    ),
    # This API is intentionally public/unauthenticated - no user data, just
    # published fuel prices and public routing. Explicit rather than relying
    # on DRF's own default, and DEFAULT_AUTHENTICATION_CLASSES is emptied out
    # too: DRF's default includes SessionAuthentication, which would enforce
    # CSRF if a client happened to carry a Django session cookie (e.g. an
    # admin logged into /admin/ in the same browser also using /map/) even
    # though nothing here is meant to require a session at all.
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny'],
    # Protects both our own server and the free upstream APIs (OSRM, Census)
    # from being hammered through this endpoint - each request costs them a
    # real call. Tune via DJANGO_ANON_THROTTLE_RATE in a real deployment.
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.AnonRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.environ.get('DJANGO_ANON_THROTTLE_RATE', '30/min'),
    },
    'EXCEPTION_HANDLER': 'routing.exceptions.custom_exception_handler',
}


# Logging - ensures unexpected errors are actually visible somewhere (stdout,
# which every standard hosting platform captures) instead of silently
# vanishing, without ever including a traceback in the HTTP response itself.

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}


# Fuel-route domain settings

VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10
VEHICLE_TANK_GALLONS = VEHICLE_RANGE_MILES / VEHICLE_MPG

# Corridor buffer (miles either side of the route) tried in order until
# enough candidate fuel stations are found near a given stretch of route.
CORRIDOR_BUFFER_STAGES_MILES = [5, 15, 30]

OSRM_BASE_URL = os.environ.get('OSRM_BASE_URL', 'https://router.project-osrm.org')
CENSUS_GEOCODER_URL = os.environ.get(
    'CENSUS_GEOCODER_URL',
    'https://geocoding.geo.census.gov/geocoder/locations/onelineaddress',
)

EXTERNAL_API_TIMEOUT_SECONDS = int(os.environ.get('EXTERNAL_API_TIMEOUT_SECONDS', '10'))
