from flask import Blueprint, render_template, redirect, url_for, request, flash, session, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models.user import User
from app.models.activity import ActivityLog
from datetime import datetime, timedelta
from functools import wraps
import re

auth_bp = Blueprint('auth', __name__)


# ── Rate limiting (in-memory, per IP) ────────────────────────────────────────
_rate_limit_store: dict = {}   # { key: (attempts, first_attempt) }


def _check_rate_limit(ip: str, window: int = 300, max_attempts: int = 5) -> bool:
    """Return True if the IP is within allowed attempts, False if blocked."""
    key = f'login:{ip}'
    now = datetime.utcnow()

    if key in _rate_limit_store:
        attempts, first = _rate_limit_store[key]
        if now - first < timedelta(seconds=window):
            if attempts >= max_attempts:
                return False
            _rate_limit_store[key] = (attempts + 1, first)
        else:
            _rate_limit_store[key] = (1, now)
    else:
        _rate_limit_store[key] = (1, now)
    return True


def _reset_rate_limit(ip: str):
    key = f'login:{ip}'
    _rate_limit_store.pop(key, None)


# ── Routes ────────────────────────────────────────────────────────────────────

@auth_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()

        # IP-level rate limit
        if not _check_rate_limit(ip):
            flash('Too many login attempts from your IP. Please wait 5 minutes.', 'danger')
            return render_template('auth/login.html')

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        captcha  = request.form.get('capt', '')

        if not username or not password:
            flash('Please enter username and password.', 'danger')
            return render_template('auth/login.html')

        # Verify captcha
        correct_answer = session.get('captcha_answer', -9999)
        try:
            if int(captcha) != correct_answer:
                flash('Incorrect captcha answer.', 'danger')
                _refresh_captcha()
                return render_template('auth/login.html')
        except ValueError:
            flash('Invalid captcha — please enter a number.', 'danger')
            _refresh_captcha()
            return render_template('auth/login.html')

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            if not user.is_active:
                flash('Your account has been deactivated.', 'warning')
                return render_template('auth/login.html')

            if user.locked_until and user.locked_until > datetime.utcnow():
                remaining = int((user.locked_until - datetime.utcnow()).total_seconds() // 60)
                flash(f'Account locked for {remaining} more minute(s).', 'danger')
                return render_template('auth/login.html')

            if not user.api_token:
                user.generate_api_token()

            user.login_attempts = 0
            user.locked_until   = None
            user.last_login     = datetime.utcnow()
            db.session.commit()

            _reset_rate_limit(ip)

            ActivityLog.log(
                user.id, 'login', 'User logged in',
                ip_address=ip, user_agent=request.user_agent.string
            )

            login_user(user, remember=True)
            session.permanent = True

            flash(f'Welcome {user.username}!', 'success')

            next_page = request.args.get('next')
            if next_page and next_page.startswith('/') and not next_page.startswith('//'):
                return redirect(next_page)
            return redirect(url_for('main.dashboard'))

        else:
            # Failed login — per-user lockout
            if user:
                user.login_attempts += 1
                if user.login_attempts >= 5:
                    user.locked_until = datetime.utcnow() + timedelta(minutes=15)
                    flash('Too many failed attempts. Account locked for 15 minutes.', 'danger')
                else:
                    remaining = 5 - user.login_attempts
                    flash(f'Invalid credentials. {remaining} attempt(s) remaining.', 'danger')
                db.session.commit()
            else:
                # Constant-time: same message whether user exists or not (no user enumeration)
                flash('Invalid username or password.', 'danger')

    _refresh_captcha()
    return render_template('auth/login.html')


def _refresh_captcha():
    import random
    session['captcha_num1']  = random.randint(1, 9)
    session['captcha_num2']  = random.randint(1, 9)
    session['captcha_answer'] = session['captcha_num1'] + session['captcha_num2']


@auth_bp.route('/logout')
@login_required
def logout():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
    ActivityLog.log(current_user.id, 'logout', 'User logged out', ip_address=ip)
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    # Disabled unless explicitly enabled in config
    if not current_app.config.get('REGISTRATION_ENABLED', False):
        flash('Public registration is disabled. Contact an administrator.', 'warning')
        return redirect(url_for('auth.login'))

    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        username         = request.form.get('username', '').strip()
        email            = request.form.get('email', '').strip()
        password         = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')

        errors = []

        if len(username) < 6:
            errors.append('Username must be at least 6 characters.')
        if not re.match(r'^[A-Za-z0-9_]+$', username):
            errors.append('Username can only contain letters, numbers, and underscores.')

        if not re.match(r'^[\w\.\-]+@[\w\.\-]+\.\w+$', email):
            errors.append('Please enter a valid email address.')

        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if password != password_confirm:
            errors.append('Passwords do not match.')

        if User.query.filter_by(username=username).first():
            errors.append('Username already exists.')
        if User.query.filter_by(email=email).first():
            errors.append('Email already registered.')

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('auth/register.html')

        from app.models.user import Role
        client_role = Role.query.filter_by(name='client').first()
        if not client_role:
            client_role = Role(name='client', display_name='Client', permissions='[]')
            db.session.add(client_role)
            db.session.commit()

        user = User(username=username, email=email, role_id=client_role.id, is_active=True)
        user.set_password(password)
        user.generate_api_token()

        db.session.add(user)
        db.session.commit()

        ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
        ActivityLog.log(user.id, 'register', 'New user registered', ip_address=ip)

        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html')


# ── Error handlers ────────────────────────────────────────────────────────────

@auth_bp.app_errorhandler(401)
def unauthorized(e):
    flash('Please log in to access this page.', 'warning')
    return redirect(url_for('auth.login', next=request.path))


@auth_bp.app_errorhandler(403)
def forbidden(e):
    flash('You do not have permission to access this page.', 'danger')
    return redirect(url_for('main.dashboard'))


@auth_bp.app_errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404


@auth_bp.app_errorhandler(500)
def server_error(e):
    db.session.rollback()
    flash('An internal error occurred. Please try again later.', 'danger')
    return redirect(url_for('main.dashboard'))
