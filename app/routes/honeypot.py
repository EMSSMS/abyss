"""
honeypot.py — Trap common attack paths and log intruders.

Any IP that hits one of these paths is almost certainly a scanner or attacker.
We log them silently and return a convincing fake response.
"""
import json
import logging
from datetime import datetime
from flask import Blueprint, request, current_app
from flask import jsonify, make_response, render_template_string

honeypot_bp = Blueprint('honeypot', __name__)

logger = logging.getLogger('honeypot')
logging.basicConfig(level=logging.WARNING)

# ── Common attack paths ──────────────────────────────────────────────────────
HONEYPOT_PATHS = [
    # WordPress
    '/wp-admin', '/wp-login.php', '/wp-config.php', '/wordpress/wp-login.php',
    # PHP panels
    '/phpmyadmin', '/pma', '/phpMyAdmin', '/admin.php', '/shell.php',
    '/c99.php', '/r57.php', '/b374k.php', '/webshell.php',
    # Env / config leaks
    '/.env', '/.git/config', '/.git/HEAD', '/config.php',
    '/config.yml', '/config.json', '/settings.py', '/local_settings.py',
    # Common admin panels
    '/admin/login', '/administrator', '/manager/html',
    # Sensitive files
    '/etc/passwd', '/proc/self/environ', '/backup.sql', '/dump.sql',
    '/db.sql', '/database.sql',
    # Testing
    '/test.php', '/info.php', '/phpinfo.php',
    # Other CMS
    '/joomla/administrator', '/Joomla/administrator',
    '/drupal/user/login',
]


def _log_attempt():
    """Persist the honeypot hit to the database and to the log file."""
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ip = ip.split(',')[0].strip()
    path = request.path
    method = request.method
    ua = request.user_agent.string or ''
    body = request.get_data(as_text=True)[:500]  # limit body stored

    logger.warning(
        "[HONEYPOT] %s %s from %s | UA: %s",
        method, path, ip, ua
    )

    # Write to DB (best-effort — don't crash the app if it fails)
    try:
        from app import db
        from sqlalchemy import text
        db.session.execute(text("""
            INSERT INTO honeypot_logs (ip, path, method, user_agent, body, created_at)
            VALUES (:ip, :path, :method, :ua, :body, :ts)
        """), {
            'ip': ip,
            'path': path,
            'method': method,
            'ua': ua,
            'body': body,
            'ts': datetime.utcnow(),
        })
        db.session.commit()
    except Exception:
        pass


# ── Fake responses to fool scanners ─────────────────────────────────────────

_FAKE_LOGIN_HTML = """<!DOCTYPE html>
<html><head><title>phpMyAdmin</title></head>
<body>
<h2>phpMyAdmin</h2>
<form method="post">
  Username: <input name="pma_username" /><br>
  Password: <input type="password" name="pma_password" /><br>
  <input type="submit" value="Go" />
</form>
<p style="color:red">Access denied for user 'root'@'localhost'</p>
</body></html>"""

_FAKE_ENV = """APP_ENV=production
DB_HOST=localhost
DB_PORT=5432
DB_NAME=app_db
DB_USER=db_user
DB_PASS=hunter2
SECRET_KEY=this-is-definitely-the-real-secret-key
"""


@honeypot_bp.route('/phpmyadmin', defaults={'subpath': ''})
@honeypot_bp.route('/phpmyadmin/<path:subpath>')
@honeypot_bp.route('/pma', defaults={'subpath': ''})
@honeypot_bp.route('/pma/<path:subpath>')
@honeypot_bp.route('/phpMyAdmin', defaults={'subpath': ''})
@honeypot_bp.route('/phpMyAdmin/<path:subpath>')
def fake_phpmyadmin(subpath):
    _log_attempt()
    resp = make_response(_FAKE_LOGIN_HTML, 200)
    resp.headers['Content-Type'] = 'text/html'
    return resp


@honeypot_bp.route('/.env')
@honeypot_bp.route('/config.php')
@honeypot_bp.route('/backup.sql')
@honeypot_bp.route('/dump.sql')
@honeypot_bp.route('/db.sql')
@honeypot_bp.route('/database.sql')
def fake_sensitive_file():
    _log_attempt()
    resp = make_response(_FAKE_ENV, 200)
    resp.headers['Content-Type'] = 'text/plain'
    return resp


@honeypot_bp.route('/.git/config')
@honeypot_bp.route('/.git/HEAD')
def fake_git():
    _log_attempt()
    resp = make_response(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n",
        200
    )
    resp.headers['Content-Type'] = 'text/plain'
    return resp


@honeypot_bp.route('/wp-admin', defaults={'subpath': ''}, methods=['GET', 'POST'])
@honeypot_bp.route('/wp-admin/<path:subpath>', methods=['GET', 'POST'])
@honeypot_bp.route('/wp-login.php', methods=['GET', 'POST'])
@honeypot_bp.route('/wp-config.php')
def fake_wordpress(subpath=''):
    _log_attempt()
    resp = make_response(_FAKE_LOGIN_HTML.replace('phpMyAdmin', 'WordPress'), 200)
    resp.headers['Content-Type'] = 'text/html'
    return resp


@honeypot_bp.route('/shell.php', methods=['GET', 'POST'])
@honeypot_bp.route('/c99.php', methods=['GET', 'POST'])
@honeypot_bp.route('/r57.php', methods=['GET', 'POST'])
@honeypot_bp.route('/b374k.php', methods=['GET', 'POST'])
@honeypot_bp.route('/webshell.php', methods=['GET', 'POST'])
@honeypot_bp.route('/admin.php', methods=['GET', 'POST'])
@honeypot_bp.route('/test.php')
@honeypot_bp.route('/info.php')
@honeypot_bp.route('/phpinfo.php')
def fake_shell():
    _log_attempt()
    # Return a 403 that looks real but logs the attempt
    return make_response('Access forbidden.', 403)


@honeypot_bp.route('/etc/passwd')
@honeypot_bp.route('/proc/self/environ')
def fake_lfi():
    _log_attempt()
    return make_response('root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n', 200)


@honeypot_bp.route('/administrator', defaults={'subpath': ''})
@honeypot_bp.route('/administrator/<path:subpath>')
@honeypot_bp.route('/joomla/administrator', defaults={'subpath': ''})
@honeypot_bp.route('/joomla/administrator/<path:subpath>')
def fake_joomla(subpath=''):
    _log_attempt()
    return make_response(_FAKE_LOGIN_HTML.replace('phpMyAdmin', 'Joomla! Administration'), 200)
