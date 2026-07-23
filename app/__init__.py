import os
import secrets
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from config import config

db = SQLAlchemy()
login_manager = LoginManager()
bcrypt = Bcrypt()

# Security headers added to every response
SECURITY_HEADERS = {
    'X-Content-Type-Options':  'nosniff',
    'X-Frame-Options':         'SAMEORIGIN',
    'X-XSS-Protection':        '1; mode=block',
    'Referrer-Policy':         'strict-origin-when-cross-origin',
    'Permissions-Policy':      'geolocation=(), microphone=()',
    'Content-Security-Policy': (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "frame-ancestors 'self';"
    ),
}


def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)

    # ── CORS: only allow explicitly configured origins ──────────────────────
    allowed_origins = app.config.get('CORS_ORIGINS') or []
    if allowed_origins:
        CORS(app, origins=allowed_origins, supports_credentials=True)
    # If CORS_ORIGINS is empty, CORS is NOT applied (no wildcard).

    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.session_protection = 'strong'

    @login_manager.user_loader
    def load_user(user_id):
        from app.models.user import User
        return User.query.get(int(user_id))

    # ── Security headers on every response ──────────────────────────────────
    @app.after_request
    def add_security_headers(response):
        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value
        return response

    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.api import api_bp
    from app.routes.admin import admin_bp
    from app.routes.sms_monitor import monitor_bp
    from app.routes.developer import dev_bp
    from app.routes.honeypot import honeypot_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(monitor_bp)
    app.register_blueprint(dev_bp)
    app.register_blueprint(honeypot_bp)

    from app.routes.sms_monitor import start_background_worker
    start_background_worker(app)

    with app.app_context():
        db.create_all()

        # ── Auto-migrate ────────────────────────────────────────────────────
        try:
            from sqlalchemy import text, inspect as sa_inspect
            inspector = sa_inspect(db.engine)
            tables = inspector.get_table_names()

            if 'sms_ranges' in tables:
                range_cols = [c['name'] for c in inspector.get_columns('sms_ranges')]
                if 'application' not in range_cols:
                    db.session.execute(text("ALTER TABLE sms_ranges ADD COLUMN application VARCHAR(50)"))
                    db.session.commit()

            if 'sms_cdr' in tables:
                cdr_cols = [c['name'] for c in inspector.get_columns('sms_cdr')]
                if 'caller_id' not in cdr_cols:
                    db.session.execute(text("ALTER TABLE sms_cdr ADD COLUMN caller_id VARCHAR(50)"))
                    db.session.commit()

            # ── Honeypot log table ──────────────────────────────────────────
            if 'honeypot_logs' not in tables:
                db.session.execute(text("""
                    CREATE TABLE honeypot_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ip TEXT NOT NULL,
                        path TEXT NOT NULL,
                        method TEXT NOT NULL,
                        user_agent TEXT,
                        body TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                db.session.commit()
        except Exception:
            pass

        from app.models.user import User, Role
        from app.models.sms import SMDRange
        from app.models.developer import StaticAsset

        for role_name, display in [('admin', 'Administrator'), ('agent', 'Agent'),
                                    ('client', 'Client'), ('developer', 'Developer')]:
            if not Role.query.filter_by(name=role_name).first():
                db.session.add(Role(name=role_name, display_name=display))
        db.session.commit()

        admin_role = Role.query.filter_by(name='admin').first()
        agent_role = Role.query.filter_by(name='agent').first()

        # ── Admin account ────────────────────────────────────────────────────
        # Default password is read from ADMIN_PASSWORD env var.
        # If not set, a random password is generated and printed ONCE to stdout.
        # CHANGE IT IMMEDIATELY after first login.
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin_password = os.environ.get("ADMIN_PASSWORD", secrets.token_urlsafe(16))
            admin = User(
                username='admin',
                email='admin@abyss.sms',
                role=admin_role,
                is_active=True,
            )
            admin.set_password(admin_password)
            admin.generate_api_token()
            db.session.add(admin)
            db.session.commit()
            print("=" * 60)
            print("[SECURITY] Default admin account created.")
            print(f"  Username: admin")
            print(f"  Password: {admin_password}")
            print("  *** CHANGE THIS PASSWORD IMMEDIATELY ***")
            print("=" * 60)

        if SMDRange.query.count() == 0:
            sample_ranges = [
                SMDRange(prefix='1',  country='United States', operator='AT&T',
                         network_type='GSM', mcc='310', mnc='410',
                         currency='USD', rate=0.005, cost_per_sms=0.0050,
                         memo='United States SMS', test_number='12025551234', is_active=True),
                SMDRange(prefix='44', country='United Kingdom', operator='Vodafone',
                         network_type='GSM', mcc='234', mnc='15',
                         currency='GBP', rate=0.004, cost_per_sms=0.0045,
                         memo='UK SMS', test_number='447911123456', is_active=True),
                SMDRange(prefix='49', country='Germany', operator='Deutsche Telekom',
                         network_type='GSM', mcc='262', mnc='1',
                         currency='EUR', rate=0.004, cost_per_sms=0.0048,
                         memo='Germany SMS', test_number='4915112345678', is_active=True),
            ]
            for r in sample_ranges:
                db.session.add(r)
            db.session.commit()

    return app
