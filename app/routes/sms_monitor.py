"""
sms_monitor.py  —  ABYSS SMS Monitor
======================================
يجيب الرسائل من المصادر الخارجية (5 مصادر)
ويعمل forward للأرقام المحجوزة تلقائياً.

FIX LIST:
  1. Background thread — يجيب كل 30 ثانية بدون ما الصفحة تكون مفتوحة
  2. ext_id — مش بيحتوي spaces بعد الآن (hashlib)
  3. session login — يتحقق من URL + text بعد redirect
  4. /debug route — لتشخيص كل مصدر بشكل مستقل
"""
import re, json, time, requests, threading, hashlib
from datetime import datetime, timedelta, date
from urllib.parse import quote_plus
from functools import wraps
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from app import db
from app.models.sms import SMSNumber, SMSCDR
from app.models.activity import ActivityLog

monitor_bp = Blueprint('monitor', __name__, url_prefix='/monitor')

# ─── helpers ────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10)",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

def _clean_html(t):
    return re.sub(r'<[^>]+>', '', str(t or '')).strip()

def _clean_num(n):
    return re.sub(r'\D', '', str(n or ''))

def _make_ext_id(prefix, number, date_str, text):
    """
    ext_id خالي من spaces، وبيحتوي hash قصير لتفادي تصادم
    رسالتين بنفس الرقم والثانية.
    """
    raw = f"{number}|{date_str}|{text[:40]}"
    h   = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"{prefix}_{number}_{h}"

def _mask_token(token):
    if not token or len(token) < 10:
        return '****'
    return token[:6] + '****' + token[-4:]

def admin_required(f):
    @wraps(f)
    def d(*a, **kw):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('Admin access required.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*a, **kw)
    return d


# ════════════════════════════════════════════════════════════
# SOURCE A  (Panel 4 — session/captcha)
# ════════════════════════════════════════════════════════════

CFG_P4 = {
    "name":       "Source A",
    "base":       "http://145.239.130.45",
    "ajax_path":  "/ints/agent/res/data_smscdr.php",
    "login_page": "/ints/login",
    "login_post": "/ints/signin",
    "username":   "Commando4",
    "password":   "Commando4",
    "timeout":    10,
    "idx_date": 0, "idx_number": 2, "idx_cli": 3, "idx_sms": 5,
}

_p4_session   = None
_p4_logged_in = False


def _p4_login():
    global _p4_session, _p4_logged_in
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        r = s.get(CFG_P4["base"] + CFG_P4["login_page"],
                  timeout=CFG_P4["timeout"])
        # إذا الجلسة ما زالت فعّالة
        if any(k in r.text.lower() for k in ("logout", "dashboard", "agent")):
            _p4_session, _p4_logged_in = s, True
            return True
        m = re.search(r'What is (\d+) \+ (\d+)', r.text)
        if not m:
            _p4_logged_in = False
            return False
        payload = {
            "username": CFG_P4["username"],
            "password": CFG_P4["password"],
            "capt":     str(int(m.group(1)) + int(m.group(2))),
        }
        r2 = s.post(CFG_P4["base"] + CFG_P4["login_post"],
                    data=payload, timeout=CFG_P4["timeout"],
                    allow_redirects=True)
        # FIX: تحقق من URL والنص معاً
        ok = any(k in r2.url.lower()  for k in ("dashboard", "agent")) or \
             any(k in r2.text.lower() for k in ("logout", "dashboard", "agent"))
        _p4_session, _p4_logged_in = s, ok
        return ok
    except Exception as e:
        print(f"[SourceA] login error: {e}")
        _p4_logged_in = False
        return False


def _fetch_panel_session(cfg, sess, is_logged, src_key):
    """Generic session-panel fetcher. Returns (msgs, status_str)."""
    if not is_logged:
        return [], "not_logged_in"

    today = date.today()
    td  = f"{today.strftime('%Y-%m-%d')} 00:00:00"
    td2 = f"{(today + timedelta(days=1)).strftime('%Y-%m-%d')} 23:59:59"
    ts  = int(time.time() * 1000)
    q = (
        f"fdate1={quote_plus(td)}&fdate2={quote_plus(td2)}"
        f"&frange=&fclient=&fnum=&fcli=&fgdate=&fgmonth=&fgrange=&fgclient=&fgnumber=&fgcli="
        f"&fg=0&sEcho=1&iColumns=9&sColumns=%2C%2C%2C%2C%2C%2C%2C%2C"
        f"&iDisplayStart=0&iDisplayLength=5000"
        f"&mDataProp_0=0&mDataProp_1=1&mDataProp_2=2&mDataProp_3=3&mDataProp_4=4"
        f"&mDataProp_5=5&mDataProp_6=6&mDataProp_7=7&mDataProp_8=8"
        f"&sSearch=&bRegex=false&iSortCol_0=0&sSortDir_0=desc&iSortingCols=1&_={ts}"
    )
    url = cfg["base"] + cfg["ajax_path"] + "?" + q
    try:
        r = sess.get(url, timeout=12)
        # FIX: أيضاً تحقق من r.url مش بس status_code
        if r.status_code == 403 or \
           any(k in r.url.lower() for k in ("login", "signin")):
            return [], "session_expired"
        data = r.json()
    except Exception as e:
        return [], str(e)

    rows = []
    for k in ("data", "aaData", "rows"):
        if isinstance(data, dict) and k in data:
            rows = data[k]
            break
    if not rows and isinstance(data, list):
        rows = data

    ix_d = cfg["idx_date"]
    ix_n = cfg["idx_number"]
    ix_c = cfg.get("idx_cli", 3)
    ix_s = cfg["idx_sms"]
    msgs = []

    for row in rows:
        if isinstance(row, (list, tuple)):
            d   = _clean_html(row[ix_d] if len(row) > ix_d else "")
            n   = _clean_num(row[ix_n]  if len(row) > ix_n else "")
            cli = _clean_html(row[ix_c] if len(row) > ix_c else "")
            s   = _clean_html(row[ix_s] if len(row) > ix_s else "")
        elif isinstance(row, dict):
            d   = _clean_html(row.get("date", row.get("dt", "")))
            n   = _clean_num(row.get("number", row.get("msisdn", row.get("num", ""))))
            cli = _clean_html(row.get("cli", ""))
            s   = _clean_html(row.get("sms", row.get("message", row.get("msg", ""))))
        else:
            continue

        if d and n and len(n) >= 8 and s and len(s) > 3:
            msgs.append({
                "id":     _make_ext_id(src_key, n, d, s),
                "number": n, "text": s, "date": d,
                "source": src_key, "cli": cli,
            })

    return msgs, "ok"


def fetch_panel4():
    global _p4_session, _p4_logged_in
    if not _p4_logged_in:
        if not _p4_login():
            return [], "Login failed"
    msgs, st = _fetch_panel_session(CFG_P4, _p4_session, _p4_logged_in, "panel4")
    if st == "session_expired":
        _p4_logged_in = False
        if _p4_login():
            msgs, st = _fetch_panel_session(CFG_P4, _p4_session, _p4_logged_in, "panel4")
        else:
            return [], "Session expired, re-login failed"
    return msgs, st


# ════════════════════════════════════════════════════════════
# SOURCE B  (Flynn Panel — session/captcha)
# ════════════════════════════════════════════════════════════

CFG_FLYNN = {
    "name":       "Source B",
    "base":       "http://91.232.105.47",
    "ajax_path":  "/ints/agent/res/data_smscdr.php",
    "login_page": "/ints/login",
    "login_post": "/ints/signin",
    "username":   "Youssef123X",
    "password":   "Youssef123",
    "timeout":    10,
    "idx_date": 0, "idx_number": 2, "idx_cli": 3, "idx_sms": 5,
}

_flynn_session   = None
_flynn_logged_in = False


def _flynn_login():
    global _flynn_session, _flynn_logged_in
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        r = s.get(CFG_FLYNN["base"] + CFG_FLYNN["login_page"],
                  timeout=CFG_FLYNN["timeout"])
        if any(k in r.text.lower() for k in ("logout", "dashboard", "agent")):
            _flynn_session, _flynn_logged_in = s, True
            return True
        m = re.search(r'What is (\d+) \+ (\d+)', r.text)
        if not m:
            _flynn_logged_in = False
            return False
        payload = {
            "username": CFG_FLYNN["username"],
            "password": CFG_FLYNN["password"],
            "capt":     str(int(m.group(1)) + int(m.group(2))),
        }
        r2 = s.post(CFG_FLYNN["base"] + CFG_FLYNN["login_post"],
                    data=payload, timeout=CFG_FLYNN["timeout"],
                    allow_redirects=True)
        ok = any(k in r2.url.lower()  for k in ("dashboard", "agent")) or \
             any(k in r2.text.lower() for k in ("logout", "dashboard", "agent"))
        _flynn_session, _flynn_logged_in = s, ok
        return ok
    except Exception as e:
        print(f"[SourceB] login error: {e}")
        _flynn_logged_in = False
        return False


def fetch_flynn():
    global _flynn_session, _flynn_logged_in
    if not _flynn_logged_in:
        if not _flynn_login():
            return [], "Login failed"
    msgs, st = _fetch_panel_session(CFG_FLYNN, _flynn_session, _flynn_logged_in, "flynn")
    if st == "session_expired":
        _flynn_logged_in = False
        if _flynn_login():
            msgs, st = _fetch_panel_session(CFG_FLYNN, _flynn_session, _flynn_logged_in, "flynn")
        else:
            return [], "Session expired, re-login failed"
    return msgs, st


# ════════════════════════════════════════════════════════════
# SOURCE C  (TimeSMS — REST API)
# ════════════════════════════════════════════════════════════

CFG_TS = {
    "name":      "Source C",
    "api_url":   "http://147.135.212.197/crapi/time/viewstats",
    "api_token": "RVRVNEVBmIGEiZZbeIyOZXWFg1l5UYJIeGdpa2d2bmKDZmNcXlU=",
    "timeout":   15,
    "records":   500,
}


def fetch_timesms(days_back=1):
    now = datetime.now()
    params = {
        "token":   CFG_TS["api_token"],
        "dt1":     (now - timedelta(days=days_back)).strftime("%Y-%m-%d %H:%M:%S"),
        "dt2":     now.strftime("%Y-%m-%d %H:%M:%S"),
        "records": CFG_TS["records"],
    }
    try:
        r = requests.get(CFG_TS["api_url"], params=params, timeout=CFG_TS["timeout"])
        data = r.json()
        if data.get("status") != "success":
            return [], data.get("msg", f"API error (HTTP {r.status_code})")
        msgs = []
        for item in data.get("data", []):
            n   = _clean_num(item.get("num", ""))
            s   = str(item.get("message", "")).strip()
            d   = str(item.get("dt", ""))
            cli = str(item.get("cli", "")).strip()
            if n and s:
                msgs.append({
                    "id":     _make_ext_id("ts", n, d, s),
                    "number": n, "text": s, "date": d,
                    "source": "timesms", "cli": cli,
                })
        return msgs, "ok"
    except Exception as e:
        return [], str(e)


# ════════════════════════════════════════════════════════════
# SOURCE D  (Hadi — REST API)
# ════════════════════════════════════════════════════════════

CFG_HADI = {
    "name":      "Source D",
    "api_url":   "http://147.135.212.197/crapi/had/viewstats",
    "api_token": "SFZURzRSQl1mb2FZg2GFfUSVmYFyi3JoimqTfX9hg3xZYI9HVINg",
    "timeout":   15,
    "records":   200,
}


def fetch_hadi(days_back=1):
    now = datetime.now()
    params = {
        "token":   CFG_HADI["api_token"],
        "dt1":     (now - timedelta(days=days_back)).strftime("%Y-%m-%d %H:%M:%S"),
        "dt2":     now.strftime("%Y-%m-%d %H:%M:%S"),
        "records": CFG_HADI["records"],
    }
    try:
        r = requests.get(CFG_HADI["api_url"], params=params, timeout=CFG_HADI["timeout"])
        data = r.json()
        if data.get("status") != "success":
            return [], data.get("msg", f"API error (HTTP {r.status_code})")
        msgs = []
        for item in data.get("data", []):
            n   = _clean_num(item.get("num", ""))
            s   = str(item.get("message", "")).strip()
            d   = str(item.get("dt", ""))
            cli = str(item.get("cli", "")).strip()
            if n and s and len(n) >= 8:
                msgs.append({
                    "id":     _make_ext_id("hd", n, d, s),
                    "number": n, "text": s, "date": d,
                    "source": "hadi", "cli": cli,
                })
        return msgs, "ok"
    except Exception as e:
        return [], str(e)


# ════════════════════════════════════════════════════════════
# SOURCE E  (Numper — REST API)
# ════════════════════════════════════════════════════════════

CFG_NUMPER = {
    "name":      "Source E",
    "api_url":   "http://147.135.212.197/crapi/st/viewstats",
    "api_token": "R1FPQUVBUzR9ZldHUoyKX3NUl1V1f2pzeml3X1iEg1d3UYp6RFJ2dw==",
    "timeout":   15,
    "records":   500,
}


def fetch_numper(days_back=1):
    now = datetime.now()
    params = {
        "token":   CFG_NUMPER["api_token"],
        "dt1":     (now - timedelta(days=days_back)).strftime("%Y-%m-%d %H:%M:%S"),
        "dt2":     now.strftime("%Y-%m-%d %H:%M:%S"),
        "records": CFG_NUMPER["records"],
    }
    try:
        r = requests.get(CFG_NUMPER["api_url"], params=params, timeout=CFG_NUMPER["timeout"])
        data = r.json()

        # Data comes as direct array: [["Source","Number","Message","Date"], ...]
        # NOT as {"data": [...]}
        msgs = []
        for item in data:
            if isinstance(item, (list, tuple)) and len(item) >= 4:
                cli = _clean_html(item[0])  # Source: WhatsApp, Facebook, etc.
                n   = _clean_num(item[1])    # Phone number
                s   = _clean_html(item[2])  # Message text
                d   = _clean_html(item[3])  # Date/time

                if n and s and len(n) >= 8:
                    msgs.append({
                        "id":     _make_ext_id("np", n, d, s),
                        "number": n, "text": s, "date": d,
                        "source": "numper", "cli": cli,
                    })
        return msgs, "ok"
    except Exception as e:
        return [], str(e)


# ════════════════════════════════════════════════════════════
# Background Worker  (server-side, 30s interval)
# ════════════════════════════════════════════════════════════

_bg_thread      = None
_bg_running     = False
_bg_lock        = threading.Lock()
_bg_last_run    = None
_bg_last_result = {}


def _background_worker(app):
    """
    Daemon thread — يشتغل كل 30 ثانية بدون ما الصفحة تكون مفتوحة.
    """
    global _bg_running, _bg_last_run, _bg_last_result
    with app.app_context():
        while _bg_running:
            try:
                all_msgs = []
                statuses = {}
                for label, fn in [
                    ("panel4",  fetch_panel4),
                    ("flynn",   fetch_flynn),
                    ("timesms", fetch_timesms),
                    ("hadi",    fetch_hadi),
                    ("numper",  fetch_numper),
                ]:
                    try:
                        msgs, st = fn()
                        all_msgs.extend(msgs)
                        statuses[label] = {"count": len(msgs), "status": st}
                    except Exception as e:
                        statuses[label] = {"count": 0, "status": str(e)}

                result = forward_to_reserved(all_msgs)
                _bg_last_run    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _bg_last_result = {
                    "fetched":    len(all_msgs),
                    "forwarded":  result["forwarded"],
                    "skipped":    result["skipped"],
                    "duplicate":  result["duplicate"],
                    "sources":    statuses,
                    "timestamp":  _bg_last_run,
                }
            except Exception as e:
                print(f"[BG Worker] unexpected error: {e}")

            time.sleep(30)


def start_background_worker(app):
    """يُستدعى من create_app() بعد تسجيل الـ blueprints."""
    global _bg_thread, _bg_running
    with _bg_lock:
        if _bg_thread is not None and _bg_thread.is_alive():
            return
        _bg_running = True
        _bg_thread  = threading.Thread(
            target=_background_worker, args=(app,), daemon=True, name="sms-monitor-bg"
        )
        _bg_thread.start()
        print("[Monitor] Background worker started ✅")


# ════════════════════════════════════════════════════════════
# Forwarder
# ════════════════════════════════════════════════════════════

def forward_to_reserved(messages):
    """
    cli = اسم التطبيق/المرسل المستخرج من بيانات الرسالة.
    """
    if not messages:
        return {"forwarded": 0, "skipped": 0, "duplicate": 0}

    existing = set(
        r[0] for r in db.session.query(SMSCDR.caller_id)
              .filter(SMSCDR.sms_type == 'received',
                      SMSCDR.caller_id.isnot(None)).all()
    )

    forwarded = skipped = duplicate = 0
    total_earned = 0.0  # Track total payout for this cycle
    payout_per_sms = 0.005  # Fixed payout per SMS in USD

    for msg in messages:
        ext_id = msg.get("id", "")
        if ext_id in existing:
            duplicate += 1
            continue

        raw_num = msg.get("number", "")
        if not raw_num:
            skipped += 1
            continue

        sms_num = SMSNumber.query.filter_by(number=raw_num, is_active=True).first()

        if not sms_num and len(raw_num) >= 9:
            suffix  = raw_num[-9:]
            sms_num = SMSNumber.query.filter(
                SMSNumber.number.like(f"%{suffix}"),
                SMSNumber.is_active == True
            ).first()

        app_cli = (msg.get("cli") or "").strip()

        # Store message even if no matching user/number (for testing/backup)
        if sms_num and sms_num.agent_id:
            number_id_val     = sms_num.id
            range_id_val      = sms_num.range_id
            user_id_val       = sms_num.agent_id
            client_id_val     = sms_num.client_id
            # Use fixed payout of 0.005 per SMS
            agent_payout_val  = payout_per_sms
            client_payout_val = 0.0

            # Credit the user with 0.005 for this SMS
            from app.models.user import User
            user = User.query.get(user_id_val)
            if user:
                user.balance = (user.balance or 0.0) + payout_per_sms
                user.total_earned = (user.total_earned or 0.0) + payout_per_sms
                total_earned += payout_per_sms
        else:
            # Store without user association - agent/admin can still view all
            number_id_val     = sms_num.id if sms_num else None
            range_id_val      = sms_num.range_id if sms_num else None
            user_id_val       = None  # No specific user
            client_id_val     = None
            agent_payout_val  = 0.0
            client_payout_val = 0.0

        cdr = SMSCDR(
            number_id     = number_id_val,
            range_id      = range_id_val,
            user_id       = user_id_val,
            client_id     = client_id_val,
            caller_id     = ext_id,
            destination   = raw_num,
            cli           = app_cli,
            message       = msg.get("text", ""),
            sms_type      = "received",
            status        = "completed",
            profit        = payout_per_sms if sms_num and sms_num.agent_id else 0.0,
            agent_payout  = agent_payout_val,
            client_payout = client_payout_val,
            currency      = "USD",
        )
        db.session.add(cdr)
        existing.add(ext_id)
        forwarded += 1

    if forwarded:
        db.session.commit()

    return {
        "forwarded": forwarded,
        "skipped": skipped,
        "duplicate": duplicate,
        "total_earned": round(total_earned, 4)
    }


# ════════════════════════════════════════════════════════════
# Routes
# ════════════════════════════════════════════════════════════

@monitor_bp.route('/')
@login_required
@admin_required
def index():
    received = SMSCDR.query.filter_by(sms_type='received') \
                           .order_by(SMSCDR.created_at.desc()).limit(50).all()
    today       = datetime.utcnow().date()
    today_count = SMSCDR.query.filter_by(sms_type='received') \
                              .filter(db.func.date(SMSCDR.created_at) == today).count()
    total_count = SMSCDR.query.filter_by(sms_type='received').count()

    sources_info = [
        {"key": "panel4",  "label": "Source A", "type": "Session",
         "token_masked": None, "token_full": None},
        {"key": "flynn",   "label": "Source B", "type": "Session",
         "token_masked": None, "token_full": None},
        {"key": "timesms", "label": "Source C", "type": "API Token",
         "token_masked": _mask_token(CFG_TS["api_token"]),
         "token_full":   CFG_TS["api_token"]},
        {"key": "hadi",    "label": "Source D", "type": "API Token",
         "token_masked": _mask_token(CFG_HADI["api_token"]),
         "token_full":   CFG_HADI["api_token"]},
        {"key": "numper",  "label": "Source E", "type": "API Token",
         "token_masked": _mask_token(CFG_NUMPER["api_token"]),
         "token_full":   CFG_NUMPER["api_token"]},
    ]

    return render_template('admin/sms_monitor.html',
                           received=received,
                           today_count=today_count,
                           total_count=total_count,
                           sources_info=sources_info,
                           bg_last_run=_bg_last_run,
                           bg_last_result=_bg_last_result)


@monitor_bp.route('/run', methods=['POST'])
@login_required
@admin_required
def run_cycle():
    source   = request.form.get('source', 'all')
    messages = []
    statuses = {}

    if source in ('all', 'panel4'):
        msgs, st = fetch_panel4()
        messages.extend(msgs)
        statuses['panel4'] = {'count': len(msgs), 'status': st}

    if source in ('all', 'flynn'):
        msgs, st = fetch_flynn()
        messages.extend(msgs)
        statuses['flynn'] = {'count': len(msgs), 'status': st}

    if source in ('all', 'timesms'):
        msgs, st = fetch_timesms(days_back=1)
        messages.extend(msgs)
        statuses['timesms'] = {'count': len(msgs), 'status': st}

    if source in ('all', 'hadi'):
        msgs, st = fetch_hadi(days_back=1)
        messages.extend(msgs)
        statuses['hadi'] = {'count': len(msgs), 'status': st}

    if source in ('all', 'numper'):
        msgs, st = fetch_numper(days_back=1)
        messages.extend(msgs)
        statuses['numper'] = {'count': len(msgs), 'status': st}

    result           = forward_to_reserved(messages)
    result['fetched']  = len(messages)
    result['sources']  = statuses

    ActivityLog.log(
        current_user.id, 'monitor_run',
        f"fetched={len(messages)} fwd={result['forwarded']} "
        f"skip={result['skipped']} dup={result['duplicate']}",
        ip_address=request.remote_addr
    )

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'success': True, **result})

    flash(
        f"Fetched {len(messages)} msgs — "
        f"Forwarded: {result['forwarded']}, "
        f"Skipped: {result['skipped']}, "
        f"Duplicate: {result['duplicate']}",
        'success'
    )
    return redirect(url_for('monitor.index'))


@monitor_bp.route('/debug')
@login_required
@admin_required
def debug_sources():
    """
    يختبر كل مصدر بشكل مستقل ويرجع تشخيص كامل.
    مفيد لمعرفة ليش الرسائل مش بتوصل.
    """
    results = {}

    # ── test API sources ──────────────────────────────────────
    for label, cfg in [
        ("timesms", CFG_TS),
        ("hadi",    CFG_HADI),
        ("numper",  CFG_NUMPER),
    ]:
        now = datetime.now()
        params = {
            "token":   cfg["api_token"],
            "dt1":     (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"),
            "dt2":     now.strftime("%Y-%m-%d %H:%M:%S"),
            "records": 10,
        }
        try:
            r    = requests.get(cfg["api_url"], params=params, timeout=10)
            raw  = r.text[:600]
            data = r.json()

            # Handle numper's direct array format vs other APIs' object format
            if label == "numper":
                # Numper returns direct array: [["Source","Number","Msg","Date"], ...]
                record_list = data if isinstance(data, list) else data.get("data", [])
                results[label] = {
                    "http_status": r.status_code,
                    "format":      "direct_array",
                    "total":       len(record_list),
                    "sample":      record_list[:2],
                    "raw_preview": raw,
                }
            else:
                # TimeSMS/Hadi return: {"status": "...", "data": [...]}
                results[label] = {
                    "http_status": r.status_code,
                    "api_status":  data.get("status"),
                    "api_msg":     data.get("msg", ""),
                    "total":       data.get("total", "?"),
                    "sample":      data.get("data", [])[:2],
                    "raw_preview": raw,
                }
        except Exception as e:
            results[label] = {"error": str(e)}

    # ── test session sources ──────────────────────────────────
    for label, cfg, login_fn, sess_ref, logged_ref in [
        ("panel4", CFG_P4,    _p4_login,    _p4_session,    _p4_logged_in),
        ("flynn",  CFG_FLYNN, _flynn_login, _flynn_session, _flynn_logged_in),
    ]:
        try:
            if not logged_ref:
                ok = login_fn()
                results[label] = {"login_attempt": "success" if ok else "failed"}
            else:
                results[label] = {"session": "already logged in"}
            # try a small fetch
            msgs, st = (fetch_panel4 if label == "panel4" else fetch_flynn)()
            results[label]["fetch_status"] = st
            results[label]["fetched_count"] = len(msgs)
            results[label]["sample"] = msgs[:2]
        except Exception as e:
            results[label] = {"error": str(e)}

    # ── number matching check ─────────────────────────────────
    reserved_count = SMSNumber.query.filter_by(is_active=True).filter(
        SMSNumber.agent_id.isnot(None)
    ).count()
    total_numbers  = SMSNumber.query.filter_by(is_active=True).count()
    results["_db"] = {
        "reserved_numbers": reserved_count,
        "total_active":     total_numbers,
        "bg_thread_alive":  _bg_thread is not None and _bg_thread.is_alive(),
        "bg_last_run":      _bg_last_run,
        "bg_last_result":   _bg_last_result,
    }

    return jsonify(results)


@monitor_bp.route('/messages')
@login_required
@admin_required
def get_messages():
    limit   = request.args.get('limit', 50, type=int)
    records = SMSCDR.query.filter_by(sms_type='received') \
                          .order_by(SMSCDR.created_at.desc()).limit(limit).all()
    data = []
    for r in records:
        data.append({
            'id':      r.id,
            'number':  r.sms_number.number if r.sms_number else (r.destination or '—'),
            'message': r.message or '',
            'cli':     r.cli or '',
            'date':    r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else '',
            'agent':   r.sms_number.agent.username
                       if (r.sms_number and r.sms_number.agent) else '—',
        })
    today       = datetime.utcnow().date()
    today_count = SMSCDR.query.filter_by(sms_type='received') \
                              .filter(db.func.date(SMSCDR.created_at) == today).count()
    total_count = SMSCDR.query.filter_by(sms_type='received').count()
    return jsonify({'success': True, 'messages': data,
                    'today_count': today_count, 'total_count': total_count})


@monitor_bp.route('/bg-status')
@login_required
@admin_required
def bg_status():
    """AJAX — حالة الـ background worker."""
    return jsonify({
        "alive":       _bg_thread is not None and _bg_thread.is_alive(),
        "last_run":    _bg_last_run,
        "last_result": _bg_last_result,
    })


@monitor_bp.route('/status')
@login_required
@admin_required
def get_status():
    p4_ok    = bool(_p4_logged_in    and _p4_session)
    flynn_ok = bool(_flynn_logged_in and _flynn_session)
    return jsonify({
        'panel4':  {'logged_in': p4_ok,    'label': 'Source A', 'type': 'session'},
        'flynn':   {'logged_in': flynn_ok, 'label': 'Source B', 'type': 'session'},
        'timesms': {'logged_in': True,     'label': 'Source C', 'type': 'api'},
        'hadi':    {'logged_in': True,     'label': 'Source D', 'type': 'api'},
        'numper':  {'logged_in': True,     'label': 'Source E', 'type': 'api'},
    })
