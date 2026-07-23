from app import db
from flask_login import UserMixin
from datetime import datetime, timedelta
import secrets

class Role(db.Model):
    __tablename__ = 'roles'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    permissions = db.Column(db.Text)  # JSON string of permissions

    users = db.relationship('User', backref='role', lazy='dynamic')

    def __repr__(self):
        return f'<Role {self.name}>'

    def has_permission(self, permission):
        if not self.permissions:
            return False
        import json
        perms = json.loads(self.permissions)
        return permission in perms

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'))
    is_active = db.Column(db.Boolean, default=True)
    api_token = db.Column(db.String(64), unique=True)

    # Profile fields
    name = db.Column(db.String(100))
    company = db.Column(db.String(100))
    address = db.Column(db.Text)
    country = db.Column(db.String(100))
    skype = db.Column(db.String(100))
    contact = db.Column(db.String(50))

    # Agent specific
    agent_id = db.Column(db.Integer, db.ForeignKey('users.id'))  # Parent agent for clients
    clients = db.relationship('User', backref=db.backref('agent', remote_side=[id]), lazy='dynamic')

    # Stats
    sms_limit = db.Column(db.BigInteger, default=0)
    sms_count = db.Column(db.Integer, default=0)

    # Balance/Earnings
    balance = db.Column(db.Float, default=0.0)  # User's current balance in USD
    total_earned = db.Column(db.Float, default=0.0)  # Total amount earned

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime)

    # Message deletion settings (in minutes, 0 = never delete)
    delete_messages_after = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<User {self.username}>'

    def set_password(self, password):
        from app import bcrypt
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        from app import bcrypt
        return bcrypt.check_password_hash(self.password_hash, password)

    def generate_api_token(self):
        self.api_token = secrets.token_urlsafe(32)
        return self.api_token

    def is_admin(self):
        return self.role and self.role.name == 'admin'

    def is_agent(self):
        return self.role and self.role.name == 'agent'

    def is_client(self):
        return self.role and self.role.name == 'client'

    def is_developer(self):
        return self.role and self.role.name in ('developer', 'admin')

    def get_sms_stats(self):
        from app.models.sms import SMSCDR
        today = datetime.utcnow().date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        stats = {
            'today': SMSCDR.query.filter(
                SMSCDR.user_id == self.id,
                db.func.date(SMSCDR.created_at) == today
            ).count(),
            'week': SMSCDR.query.filter(
                SMSCDR.user_id == self.id,
                SMSCDR.created_at >= week_ago
            ).count(),
            'month': SMSCDR.query.filter(
                SMSCDR.user_id == self.id,
                SMSCDR.created_at >= month_ago
            ).count(),
            'total': SMSCDR.query.filter_by(user_id=self.id).count()
        }
        return stats

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'name': self.name,
            'company': self.company,
            'country': self.country,
            'role': self.role.name if self.role else None,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
