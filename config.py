import os
import secrets
from datetime import timedelta


class Config:
    # ── Secret key ──────────────────────────────────────────────────────────
    # MUST be set via environment variable in production.
    SECRET_KEY = os.environ.get('SECRET_KEY') or secrets.token_hex(32)

    # ── Database ─────────────────────────────────────────────────────────────
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get('DATABASE_URL') or 'sqlite:///abyss_sms.db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── Session & Cookies ────────────────────────────────────────────────────
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_SECURE   = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # ── CSRF ────────────────────────────────────────────────────────────────
    WTF_CSRF_ENABLED    = True
    WTF_CSRF_TIME_LIMIT = 3600

    # ── Rate limiting ────────────────────────────────────────────────────────
    LOGIN_ATTEMPT_LIMIT  = 5
    LOGIN_ATTEMPT_WINDOW = 300

    # ── Registration ─────────────────────────────────────────────────────────
    # Set REGISTRATION_ENABLED=true in env to allow public self-registration.
    REGISTRATION_ENABLED = os.environ.get('REGISTRATION_ENABLED', 'false').lower() == 'true'

    # ── Webhook secret ───────────────────────────────────────────────────────
    # The SMS gateway must send this as X-Webhook-Secret header.
    WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', '')

    # ── Bulk SMS cap ─────────────────────────────────────────────────────────
    BULK_SMS_MAX_DESTINATIONS = int(os.environ.get('BULK_SMS_MAX_DESTINATIONS', '500'))

    # ── CORS whitelist ───────────────────────────────────────────────────────
    CORS_ORIGINS = [
        o.strip()
        for o in os.environ.get('CORS_ORIGINS', '').split(',')
        if o.strip()
    ]


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SESSION_COOKIE_SECURE = False


config = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'testing':     TestingConfig,
    'default':     DevelopmentConfig,
}
