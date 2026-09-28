from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY')
DEBUG = os.getenv('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = ['anganbaari.pythonanywhere.com', '127.0.0.1', 'localhost']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sitemaps',
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'shop',
    'api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Must run BEFORE corsheaders' CorsMiddleware: corsheaders short-circuits
    # *any* OPTIONS preflight globally (regardless of origin) before calling
    # down to the next middleware, so if it ran first it would swallow
    # /api/v1/ preflights before this middleware ever saw them. Placed here,
    # this middleware gets first refusal on /api/v1/ requests and defers
    # everything else (including ABMS's own preflights) to corsheaders below.
    'api.middleware.ApiV1CorsMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# CORS for the ABMS bridge only (/api/inventory/movements/, token-authed,
# POST). Untouched by the new /api/v1/ API — see api/middleware.py for why
# that gets its own separate, narrower CORS layer instead of sharing this one.
CORS_ALLOWED_ORIGINS = [
    'https://angan-baari.web.app',
]

# CORS for the new /api/v1/ read API only, used by a Next.js frontend on its
# own domain (enforced in api/middleware.py, not django-cors-headers).
# Comma-separated env var so this list can be changed without a code change;
# the default covers the Next.js dev server plus its deployed Vercel domain.
API_V1_CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        'API_V1_CORS_ALLOWED_ORIGINS',
        'http://localhost:3000,https://angan-baari-shop.vercel.app',
    ).split(',')
    if origin.strip()
]

# The Next.js frontend's own base URL — used to build links that must point
# at that app rather than this Django site's own pages, e.g. the
# password-reset email sent from api/views.py's PasswordResetRequestView.
# Single value (not a list like API_V1_CORS_ALLOWED_ORIGINS above), since a
# reset link can only point at one place. No trailing slash.
FRONTEND_BASE_URL = os.getenv('FRONTEND_BASE_URL', 'https://angan-baari-shop.vercel.app')

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
}

ROOT_URLCONF = 'farmsite.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
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

WSGI_APPLICATION = 'farmsite.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kathmandu'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Resend email (works on PythonAnywhere free tier)
RESEND_API_KEY = os.getenv('RESEND_API_KEY')
DEFAULT_FROM_EMAIL = 'Angan Baari <orders@anganbaari.com>'
ADMIN_EMAIL = 'anganbaari@gmail.com'

# Telegram notifications
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# ImageKit (image storage/CDN)
IMAGEKIT_PUBLIC_KEY = os.getenv('IMAGEKIT_PUBLIC_KEY')
IMAGEKIT_PRIVATE_KEY = os.getenv('IMAGEKIT_PRIVATE_KEY')
IMAGEKIT_URL_ENDPOINT = os.getenv('IMAGEKIT_URL_ENDPOINT')

# --- Storage backend: use ONE of the two blocks below, not both ---
# Run `python -m django --version` in a Bash console to check yours.

# OPTION A — Django 4.2 or newer:
STORAGES = {
    "default": {
        "BACKEND": "shop.imagekit_storage.ImageKitStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Auth settings
LOGIN_URL = '/account/login/'
LOGIN_REDIRECT_URL = '/shop/'
LOGOUT_REDIRECT_URL = '/'

SITE_ID = 1