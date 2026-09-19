import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = env("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [h for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "copier",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

if os.environ.get("DB_NAME"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME"), "USER": env("DB_USER", "copytrader"), "PASSWORD": env("DB_PASSWORD", ""),
        "HOST": env("DB_HOST", "localhost"), "PORT": env("DB_PORT", "5432"),
    }}
else:  # local dev fallback
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---- copy-trading settings -------------------------------------------------
ENCRYPTION_KEYS = env("ENCRYPTION_KEYS", "")            # comma-separated Fernet keys (first encrypts)
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", "")
MASTER_API_KEY = env("MASTER_API_KEY", "")              # brother's READ-ONLY key
MASTER_API_SECRET = env("MASTER_API_SECRET", "")
BINGX_BASE_URL = env("BINGX_BASE_URL", "https://open-api-vst.bingx.com")
DRY_RUN = env("DRY_RUN", "0") == "1"                    # 1 = read from exchanges but NEVER place orders / set leverage
LOG_RAW_STREAM = env("LOG_RAW_STREAM", "0") == "1"      # log every master stream message (use while verifying)
MAX_CONCURRENCY = int(env("MAX_CONCURRENCY", "20"))     # simultaneous follower orders
MAX_OPEN_EVENT_AGE_SECONDS = int(env("MAX_OPEN_EVENT_AGE_SECONDS", "30"))  # older opens are NOT copied

LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
SERVER_IP = env("SERVER_IP", "<your server IP>")           # shown to followers for API-key IP whitelisting