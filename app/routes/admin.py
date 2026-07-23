from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, make_response
from flask_login import login_required, current_user
from app import db
from app.models.sms import SMDRange, SMSNumber, SMSCDR
from app.models.user import User, Role
from app.models.activity import ActivityLog, News
from datetime import datetime, timedelta
from functools import wraps

admin_bp = Blueprint('admin', __name__)

# Admin required decorator
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('auth.login'))
        # Check both is_admin column and is_admin() method
        is_admin = current_user.is_admin()
        if not is_admin:
            flash('Admin access required.', 'danger')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated

# ============ ADMIN DASHBOARD ============

@admin_bp.route('/')
@admin_required
def index():
    total_users = User.query.count()
    active_users = User.query.filter_by(is_active=True).count()
    total_numbers = SMSNumber.query.count()
    total_ranges = SMDRange.query.count()
    total_cdr = SMSCDR.query.count()

    # Recent activity
    recent_activity = ActivityLog.query.order_by(
        ActivityLog.created_at.desc()
    ).limit(10).all()

    # Stats
    today = datetime.utcnow().date()
    today_sms = SMSCDR.query.filter(
        db.func.date(SMSCDR.created_at) == today
    ).count()

    # Recent news
    recent_news = News.query.filter_by(is_active=True).order_by(
        News.created_at.desc()
    ).limit(5).all()

    return render_template('admin/index.html',
        stats={
            'total_users': total_users,
            'active_users': active_users,
            'total_numbers': total_numbers,
            'total_ranges': total_ranges,
            'total_cdr': total_cdr,
            'today_sms': today_sms
        },
        recent_news=recent_news
    )

# ============ USER MANAGEMENT ============

@admin_bp.route('/users/view/<int:user_id>')
@admin_required
def view_user(user_id):
    user = User.query.get_or_404(user_id)
    return render_template('admin/user_view.html', user=user)

@admin_bp.route('/users')
@admin_required
def users():
    page = request.args.get('page', 1, type=int)
    per_page = 25
    search = request.args.get('search', '')
    role_filter = request.args.get('role', '')

    query = User.query

    if search:
        query = query.filter(
            db.or_(
                User.username.like(f'%{search}%'),
                User.email.like(f'%{search}%'),
                User.name.like(f'%{search}%')
            )
        )

    if role_filter:
        query = query.filter_by(role_id=Role.query.filter_by(name=role_filter).first().id if Role.query.filter_by(name=role_filter).first() else None)

    users_list = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    roles = Role.query.all()
    agents = User.query.filter(User.role.has(name='agent')).all()

    return render_template('admin/users.html',
        users=users_list,
        roles=roles,
        agents=agents
    )

@admin_bp.route('/users/create', methods=['GET', 'POST'])
@admin_required
def create_user():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role_id = request.form.get('role_id', type=int)
        agent_id = request.form.get('agent_id', type=int)
        name = request.form.get('name')
        company = request.form.get('company')
        country = request.form.get('country')
        sms_limit = request.form.get('sms_limit', 0, type=int)

        if not username or not email or not password:
            flash('Username, email, and password are required.', 'danger')
            return redirect(url_for('admin.create_user'))

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('admin.create_user'))

        role = Role.query.get(role_id)
        if not role:
            flash('Invalid role selected.', 'danger')
            return redirect(url_for('admin.create_user'))

        user = User(
            username=username,
            email=email,
            role=role,
            name=name,
            company=company,
            country=country,
            agent_id=agent_id if agent_id else None,
            sms_limit=sms_limit,
            is_active=True
        )
        user.set_password(password)
        user.generate_api_token()

        db.session.add(user)
        db.session.commit()

        ActivityLog.log(
            current_user.id,
            'admin_create_user',
            f'Created user {username} with role {role.display_name}',
            ip_address=request.remote_addr
        )

        flash(f'User {username} created successfully.', 'success')
        return redirect(url_for('admin.users'))

    roles = Role.query.all()
    agents = User.query.filter(User.role.has(name='agent')).all()
    return render_template('admin/user_form.html', roles=roles, agents=agents, user=None)

@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == 'POST':
        user.email = request.form.get('email')
        user.name = request.form.get('name')
        user.company = request.form.get('company')
        user.country = request.form.get('country')
        user.skype = request.form.get('skype')
        user.contact = request.form.get('contact')
        user.sms_limit = request.form.get('sms_limit', 0, type=int)
        user.agent_id = request.form.get('agent_id', type=int)
        if not user.agent_id:
            user.agent_id = None

        role_id = request.form.get('role_id', type=int)
        if role_id:
            role = Role.query.get(role_id)
            if role:
                user.role_id = role.id

        is_active = request.form.get('is_active')
        user.is_active = bool(is_active)

        new_password = request.form.get('password')
        if new_password and len(new_password) >= 6:
            user.set_password(new_password)

        db.session.commit()

        ActivityLog.log(
            current_user.id,
            'admin_edit_user',
            f'Edited user {user.username}',
            ip_address=request.remote_addr
        )

        flash(f'User {user.username} updated successfully.', 'success')
        return redirect(url_for('admin.users'))

    roles = Role.query.all()
    agents = User.query.filter(User.role.has(name='agent')).all()
    return render_template('admin/user_form.html', roles=roles, agents=agents, user=user)

@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('Cannot delete your own account.', 'danger')
        return redirect(url_for('admin.users'))

    username = user.username
    db.session.delete(user)
    db.session.commit()

    ActivityLog.log(
        current_user.id,
        'admin_delete_user',
        f'Deleted user {username}',
        ip_address=request.remote_addr
    )

    flash(f'User {username} deleted.', 'success')
    return redirect(url_for('admin.users'))

@admin_bp.route('/users/<int:user_id>/toggle-status', methods=['POST'])
@admin_required
def toggle_user_status(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        return jsonify({'error': 'Cannot toggle own status'}), 400

    user.is_active = not user.is_active
    db.session.commit()

    return jsonify({
        'success': True,
        'is_active': user.is_active
    })

@admin_bp.route('/users/<int:user_id>/reset-payout', methods=['POST'])
@admin_required
def reset_user_payout(user_id):
    """Reset user's sms_count to 0"""
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('Cannot reset your own payout.', 'danger')
        return redirect(url_for('admin.users'))

    old_count = user.sms_count
    user.sms_count = 0
    db.session.commit()

    ActivityLog.log(
        current_user.id,
        'admin_reset_payout',
        f'Reset payout for user {user.username} from {old_count} to 0',
        ip_address=request.remote_addr
    )

    flash(f'Payout reset for {user.username} (was: {old_count})', 'success')
    return redirect(url_for('admin.users'))

# ============ SMS RANGES MANAGEMENT ============

@admin_bp.route('/ranges')
@admin_required
def sms_ranges():
    page = request.args.get('page', 1, type=int)
    per_page = 25
    search = request.args.get('search', '')

    query = SMDRange.query

    if search:
        query = query.filter(
            db.or_(
                SMDRange.prefix.like(f'%{search}%'),
                SMDRange.country.like(f'%{search}%'),
                SMDRange.name.like(f'%{search}%')
            )
        )

    ranges_list = query.order_by(SMDRange.country).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template('admin/sms_ranges.html', ranges=ranges_list)

@admin_bp.route('/ranges/create', methods=['GET', 'POST'])
@admin_required
def create_sms_range():
    if request.method == 'POST':
        name = request.form.get('name')
        prefix = request.form.get('prefix')
        country = request.form.get('country')
        test_number = request.form.get('test_number')
        application = request.form.get('application', '')

        # التحقق من ملف TXT أو CSV
        csv_file = request.files.get('csv_file')
        csv_numbers = []
        if csv_file and csv_file.filename:
            try:
                raw = csv_file.read()
                try:
                    content = raw.decode('utf-8')
                except UnicodeDecodeError:
                    content = raw.decode('latin-1')
                lines = content.strip().split('\n')
                for line in lines:
                    # دعم CSV بعدة أعمدة - خذ أول عمود فقط
                    cell = line.split(',')[0].strip()
                    if cell:
                        csv_numbers.append(cell)
            except Exception as e:
                flash(f'Error reading file: {str(e)}', 'danger')
                return redirect(url_for('admin.create_sms_range'))

        # إنشاء الـ range
        sms_range = SMDRange(
            name=name,
            prefix=prefix,
            country=country,
            test_number=test_number,
            application=application if application else None,
            cost_per_sms=0.005,
            is_active=True
        )
        db.session.add(sms_range)
        db.session.commit()

        # إضافة أرقام من CSV فقط
        created_count = 0
        skip_count = 0
        if csv_numbers:
            existing_numbers = set(
                num[0] for num in db.session.query(SMSNumber.number).all()
            )

            for num_str in csv_numbers:
                num_clean = num_str.strip()
                if not num_clean:
                    continue
                # إذا الرقم لا يبدأ بالـ prefix، نضيفه
                if not num_clean.startswith(prefix):
                    num_clean = f"{prefix}{num_clean}"

                if num_clean in existing_numbers:
                    skip_count += 1
                    continue

                num = SMSNumber(
                    range_id=sms_range.id,
                    number=num_clean,
                    prefix=prefix,
                    status='available',
                    is_active=True
                )
                db.session.add(num)
                created_count += 1
                existing_numbers.add(num_clean)

            db.session.commit()

        ActivityLog.log(
            current_user.id,
            'admin_create_range',
            f'Created range {prefix} and added {created_count} numbers',
            ip_address=request.remote_addr
        )

        # رسالة النتيجة
        result_msg = f'Range {prefix} created with {created_count} numbers.'
        if skip_count > 0:
            result_msg += f' ({skip_count} numbers skipped - already exist)'
        flash(result_msg, 'success')
        return redirect(url_for('admin.sms_ranges'))

    return render_template('admin/range_form.html', range_obj=None)

@admin_bp.route('/ranges/<int:range_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_sms_range(range_id):
    range_obj = SMDRange.query.get_or_404(range_id)

    if request.method == 'POST':
        range_obj.name = request.form.get('name')
        range_obj.prefix = request.form.get('prefix')
        range_obj.country = request.form.get('country')
        range_obj.application = request.form.get('application') or None
        range_obj.operator = request.form.get('operator')
        range_obj.network_type = request.form.get('network_type')
        range_obj.mcc = request.form.get('mcc')
        range_obj.mnc = request.form.get('mnc')
        range_obj.hlr_lookup = bool(request.form.get('hlr_lookup'))
        range_obj.cost_per_sms = request.form.get('cost_per_sms', 0.005, type=float)
        range_obj.currency = request.form.get('currency', 'USD')
        range_obj.rate = request.form.get('rate', 0.0, type=float)
        range_obj.payout = request.form.get('payout', 0.0, type=float)
        range_obj.test_number = request.form.get('test_number')
        range_obj.memo = request.form.get('memo')
        
        is_active = request.form.get('is_active')
        range_obj.is_active = bool(is_active)

        db.session.commit()

        ActivityLog.log(
            current_user.id,
            'admin_edit_range',
            f'Edited range {range_obj.prefix}',
            ip_address=request.remote_addr
        )

        flash(f'Range {range_obj.prefix} updated.', 'success')
        return redirect(url_for('admin.sms_ranges'))

    return render_template('admin/range_form.html', range_obj=range_obj)

@admin_bp.route('/ranges/<int:range_id>/delete', methods=['GET', 'POST'])
@admin_required
def delete_sms_range(range_id):
    range_obj = SMDRange.query.get_or_404(range_id)
    
    # Delete all numbers associated with this range (including assigned ones)
    SMSNumber.query.filter_by(range_id=range_id).delete()
    
    range_info = f'{range_obj.name or range_obj.prefix} - {range_obj.country}'
    db.session.delete(range_obj)
    db.session.commit()

    ActivityLog.log(
        current_user.id,
        'admin_delete_range',
        f'Deleted range {range_info}',
        ip_address=request.remote_addr
    )

    flash(f'Range {range_info} deleted.', 'success')
    return redirect(url_for('admin.sms_ranges'))

# ============ SMS MANAGEMENT ============

@admin_bp.route('/sms/numbers')
@admin_required
def sms_numbers():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search = request.args.get('search', '')
    agent_filter = request.args.get('agent', '')

    query = SMSNumber.query

    if search:
        query = query.filter(SMSNumber.number.like(f'%{search}%'))

    if agent_filter:
        query = query.filter_by(agent_id=agent_filter)

    numbers = query.order_by(SMSNumber.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    agents = User.query.filter(User.role.has(name='agent')).all()

    return render_template('admin/sms_numbers.html', numbers=numbers, agents=agents)

@admin_bp.route('/sms/send', methods=['GET', 'POST'])
@admin_required
def sms_send():
    if request.method == 'POST':
        number = request.form.get('number')
        cli = request.form.get('cli')
        message = request.form.get('message')

        if not number or not cli or not message:
            flash('Number, CLI, and message are required.', 'danger')
            return redirect(url_for('admin.sms_send'))

        # Find the SMS number
        sms_number = SMSNumber.query.filter_by(number=number).first()
        if not sms_number:
            flash('SMS number not found.', 'danger')
            return redirect(url_for('admin.sms_send'))

        # Check if user has this number in their account
        # For admin, we allow sending from any number

        # Create CDR record
        cdr = SMSCDR(
            number_id=sms_number.id,
            range_id=sms_number.range_id,
            user_id=sms_number.agent_id,
            client_id=sms_number.client_id,
            cli=cli,
            destination=number,
            message=message,
            sms_type='sent',
            status='completed',
            profit=0.005, agent_payout=0.005  # Admin profit per SMS
        )

        db.session.add(cdr)
        db.session.commit()

        ActivityLog.log(
            current_user.id,
            'admin_send_sms',
            f'Sent SMS from {cli} to {number}',
            ip_address=request.remote_addr
        )

        flash('SMS sent successfully.', 'success')
        return redirect(url_for('admin.sms_send'))

    # Get all SMS numbers for the dropdown
    sms_numbers = SMSNumber.query.filter_by(is_active=True).all()
    return render_template('admin/sms_send.html', sms_numbers=sms_numbers)

@admin_bp.route('/sms/cdr')
@admin_required
def sms_cdr():
    page = request.args.get('page', 1, type=int)
    per_page = 50

    # Date range - try to parse date with time first, then without time
    fdate1 = request.args.get('fdate1', datetime.utcnow().strftime('%Y-%m-%d'))
    fdate2 = request.args.get('fdate2', datetime.utcnow().strftime('%Y-%m-%d'))

    def parse_date(date_str):
        try:
            return datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            try:
                return datetime.strptime(date_str, '%Y-%m-%d')
            except (ValueError, TypeError):
                return datetime.utcnow()

    date1 = parse_date(fdate1)
    date2 = parse_date(fdate2)
    # Add time to end date to include the whole day
    date2 = date2.replace(hour=23, minute=59, second=59)

    query = SMSCDR.query.filter(
        SMSCDR.created_at >= date1,
        SMSCDR.created_at <= date2
    )

    cdr_records = query.order_by(SMSCDR.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Totals
    totals = db.session.query(
        db.func.count(SMSCDR.id).label('total'),
        db.func.sum(SMSCDR.profit).label('total_profit')
    ).filter(
        SMSCDR.created_at >= date1,
        SMSCDR.created_at <= date2
    ).first()

    return render_template('admin/sms_cdr.html',
        cdr_records=cdr_records,
        totals=totals,
        fdate1=fdate1,
        fdate2=fdate2
    )

# ============ ACTIVITY LOGS ============

@admin_bp.route('/activity')
@admin_required
def activity_logs():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    user_filter = request.args.get('user', '')
    action_filter = request.args.get('action', '')

    query = ActivityLog.query

    if user_filter:
        query = query.filter_by(user_id=user_filter)

    if action_filter:
        query = query.filter_by(action=action_filter)

    activities = query.order_by(ActivityLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    users = User.query.all()

    return render_template('admin/activity.html', activities=activities, users=users)

# ============ NEWS MANAGEMENT ============

@admin_bp.route('/news')
@admin_required
def news():
    page = request.args.get('page', 1, type=int)
    per_page = 20

    news_list = News.query.order_by(News.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template('admin/news.html', news_list=news_list)

@admin_bp.route('/news/create', methods=['GET', 'POST'])
@admin_required
def create_news():
    if request.method == 'POST':
        headline = request.form.get('headline')
        content = request.form.get('content')

        if not headline:
            flash('Headline is required.', 'danger')
            return redirect(url_for('admin.create_news'))

        news = News(
            headline=headline,
            content=content,
            created_by=current_user.id,
            is_active=True
        )

        db.session.add(news)
        db.session.commit()

        flash('News created successfully.', 'success')
        return redirect(url_for('admin.news'))

    return render_template('admin/news_form.html', news=None)

@admin_bp.route('/news/<int:news_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_news(news_id):
    news = News.query.get_or_404(news_id)

    if request.method == 'POST':
        news.headline = request.form.get('headline')
        news.content = request.form.get('content')

        is_active = request.form.get('is_active')
        news.is_active = bool(is_active)

        db.session.commit()

        flash('News updated successfully.', 'success')
        return redirect(url_for('admin.news'))

    return render_template('admin/news_form.html', news=news)

@admin_bp.route('/news/<int:news_id>/delete', methods=['POST'])
@admin_required
def delete_news(news_id):
    news = News.query.get_or_404(news_id)
    db.session.delete(news)
    db.session.commit()

    flash('News deleted.', 'success')
    return redirect(url_for('admin.news'))

# ============ SETTINGS ============

@admin_bp.route('/settings')
@admin_required
def settings():
    return render_template('admin/settings.html')

@admin_bp.route('/settings/sms-limit', methods=['POST'])
@admin_required
def update_sms_limit():
    user_id = request.form.get('user_id', type=int)
    sms_limit = request.form.get('sms_limit', 0, type=int)

    if not user_id:
        return jsonify({'error': 'User ID required'}), 400

    user = User.query.get_or_404(user_id)
    user.sms_limit = sms_limit
    db.session.commit()

    return jsonify({'success': True})

# ============ AGENT MANAGEMENT ============
# هذه الـ routes خاصة بالـ Agent لإدارة أرقامه و clients الخاصة به

@admin_bp.route('/agent/add-numbers', methods=['GET', 'POST'])
@login_required
def agent_add_numbers():
    """Agent يضيف أرقام لنفسه"""
    # التحقق أن المستخدم agent
    if not (current_user.is_agent() or current_user.is_admin()):
        flash('Access denied. Agent account required.', 'danger')
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        range_id = request.form.get('range_id', type=int)
        numbers_count = request.form.get('numbers_count', 0, type=int)

        if not range_id:
            flash('Please select a range.', 'danger')
            return redirect(url_for('admin.agent_add_numbers'))

        # حساب عدد الأرقام اللي عند agent حالياً والتحقق من الحد المخصص له
        current_count = SMSNumber.query.filter_by(agent_id=current_user.id).count()
        max_total = current_user.sms_limit if current_user.sms_limit > 0 else 1
        remaining = max_total - current_count

        if remaining <= 0:
            flash(f'You have reached the maximum limit of {max_total} numbers.', 'warning')
            return redirect(url_for('admin.agent_add_numbers'))

        if numbers_count > remaining:
            flash(f'You can only add {remaining} more numbers. Adjusting to {remaining}.', 'warning')
            numbers_count = remaining

        # الحصول على الـ range
        sms_range = SMDRange.query.get(range_id)
        if not sms_range:
            flash('Invalid range selected.', 'danger')
            return redirect(url_for('admin.agent_add_numbers'))

        # الحصول على أرقام متاحة من الـ range
        available_numbers = SMSNumber.query.filter_by(
            range_id=range_id,
            agent_id=None,
            is_active=True
        ).limit(numbers_count).all()

        if not available_numbers:
            flash('No available numbers in this range.', 'warning')
            return redirect(url_for('admin.agent_add_numbers'))

        # حجز الأرقام للagent
        numbers_added = 0
        for num in available_numbers:
            num.agent_id = current_user.id
            num.status = 'reserved'
            num.assigned_at = datetime.utcnow()
            numbers_added += 1

        db.session.commit()

        # تسجيل النشاط
        ActivityLog.log(
            current_user.id,
            'agent_add_numbers',
            f'Added {numbers_added} numbers from range {sms_range.prefix}',
            ip_address=request.remote_addr
        )

        flash(f'{numbers_added} numbers added to your account successfully!', 'success')
        return redirect(url_for('admin.sms_numbers'))

    # الحصول على الـ ranges المتاحة
    ranges = SMDRange.query.filter_by(is_active=True).all()

    # حساب عدد الأرقام الحالية
    current_numbers = SMSNumber.query.filter_by(agent_id=current_user.id).count()

    return render_template('admin/agent_add_numbers.html',
        ranges=ranges,
        current_numbers=current_numbers,
        max_numbers=current_user.sms_limit if current_user.sms_limit > 0 else 1
    )

@admin_bp.route('/agent/create-client', methods=['GET', 'POST'])
@login_required
def agent_create_client():
    """Agent يضيف client جديد"""
    # التحقق أن المستخدم agent
    if not (current_user.is_agent() or current_user.is_admin()):
        flash('Access denied. Agent account required.', 'danger')
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        company = request.form.get('company')
        country = request.form.get('country')
        numbers_count = request.form.get('numbers_count', 0, type=int)

        if not username or not email or not password:
            flash('Username, email, and password are required.', 'danger')
            return redirect(url_for('admin.agent_create_client'))

        # التحقق من عدم وجود username أو email
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('admin.agent_create_client'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('admin.agent_create_client'))

        # الحصول على role client
        client_role = Role.query.filter_by(name='client').first()
        if not client_role:
            flash('Client role not found. Please contact admin.', 'danger')
            return redirect(url_for('admin.agent_create_client'))

        # إنشاء الـ client
        client = User(
            username=username,
            email=email,
            role_id=client_role.id,
            name=name,
            company=company,
            country=country,
            agent_id=current_user.id,  # ربط الـ client بالـ agent
            is_active=True
        )
        client.set_password(password)
        client.generate_api_token()

        db.session.add(client)
        db.session.commit()

        # إضافة أرقام للـ client إذا طُلب
        if numbers_count > 0:
            # الحصول على أرقام الـ agent
            agent_numbers = SMSNumber.query.filter_by(
                agent_id=current_user.id,
                client_id=None,
                is_active=True
            ).limit(numbers_count).all()

            for num in agent_numbers:
                num.client_id = client.id
                num.status = 'activated'

            db.session.commit()
            flash(f'{len(agent_numbers)} numbers assigned to client.', 'success')

        # تسجيل النشاط
        ActivityLog.log(
            current_user.id,
            'agent_create_client',
            f'Created client {username}',
            ip_address=request.remote_addr
        )

        flash(f'Client {username} created successfully!', 'success')
        return redirect(url_for('main.clients'))

    return render_template('admin/agent_create_client.html')

@admin_bp.route('/agent/clients')
@login_required
def agent_clients():
    """عرض clients الخاصة بالـ agent"""
    # التحقق أن المستخدم agent
    if not (current_user.is_agent() or current_user.is_admin()):
        flash('Access denied. Agent account required.', 'danger')
        return redirect(url_for('main.dashboard'))

    page = request.args.get('page', 1, type=int)
    per_page = 25
    search = request.args.get('search', '')

    query = User.query.filter_by(agent_id=current_user.id)

    if search:
        query = query.filter(
            db.or_(
                User.username.like(f'%{search}%'),
                User.email.like(f'%{search}%'),
                User.name.like(f'%{search}%')
            )
        )

    clients = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template('admin/agent_clients.html', clients=clients)

@admin_bp.route('/agent/my-numbers')
@login_required
def agent_my_numbers():
    """عرض أرقام الـ agent"""
    # التحقق أن المستخدم agent
    if not (current_user.is_agent() or current_user.is_admin()):
        flash('Access denied. Agent account required.', 'danger')
        return redirect(url_for('main.dashboard'))

    page = request.args.get('page', 1, type=int)
    per_page = 50
    search = request.args.get('search', '')

    query = SMSNumber.query.filter_by(agent_id=current_user.id)

    if search:
        query = query.filter(SMSNumber.number.like(f'%{search}%'))

    numbers = query.order_by(SMSNumber.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # حساب الإحصائيات
    total_numbers = SMSNumber.query.filter_by(agent_id=current_user.id).count()
    assigned_to_clients = SMSNumber.query.filter(
        SMSNumber.agent_id == current_user.id,
        SMSNumber.client_id.isnot(None)
    ).count()
    available = total_numbers - assigned_to_clients

    return render_template('admin/agent_my_numbers.html',
        numbers=numbers,
        total_numbers=total_numbers,
        assigned_to_clients=assigned_to_clients,
        available=available
    )

@admin_bp.route('/sms/numbers/<int:number_id>/unassign', methods=['POST'])
@admin_required
def unassign_number(number_id):
    """Unassign a number from its agent and client without deleting it from the system."""
    number = SMSNumber.query.get_or_404(number_id)
    
    number.agent_id = None
    number.client_id = None
    number.status = 'available'
    number.assigned_at = None
    
    db.session.commit()
    
    ActivityLog.log(
        current_user.id,
        'admin_unassign_number',
        f'Unassigned number {number.number}',
        ip_address=request.remote_addr
    )
    
    flash(f'Number {number.number} has been unassigned and is now available.', 'success')
    return redirect(url_for('admin.sms_numbers'))

@admin_bp.route('/sms/numbers/<int:number_id>/delete', methods=['POST'])
@admin_required
def delete_number(number_id):
    """Delete a number from the system even if it's assigned to a user."""
    number = SMSNumber.query.get_or_404(number_id)
    
    num_str = number.number
    db.session.delete(number)
    db.session.commit()
    
    ActivityLog.log(
        current_user.id,
        'admin_delete_number',
        f'Deleted number {num_str}',
        ip_address=request.remote_addr
    )
    
    flash(f'Number {num_str} has been deleted from the system.', 'success')
    return redirect(url_for('admin.sms_numbers'))

# ============ ADMIN ADD NUMBERS TO AGENT ============

@admin_bp.route('/admin/add-numbers-to-agent', methods=['GET', 'POST'])
@admin_required
def admin_add_numbers_to_agent():
    """Admin adds numbers to a specific agent"""
    if request.method == 'POST':
        # البحث عن agent باليوزرنيم
        agent_username = request.form.get('agent_username', '').strip()
        range_id = request.form.get('range_id', type=int)
        numbers_count = request.form.get('numbers_count', 0, type=int)

        if not agent_username:
            flash('Please enter agent username.', 'danger')
            return redirect(url_for('admin.admin_add_numbers_to_agent'))

        if not range_id:
            flash('Please select a range.', 'danger')
            return redirect(url_for('admin.admin_add_numbers_to_agent'))

        if numbers_count <= 0:
            flash('Please enter a valid number of numbers.', 'danger')
            return redirect(url_for('admin.admin_add_numbers_to_agent'))

        # البحث عن الـ agent باليوزرنيم
        agent = User.query.filter_by(username=agent_username).first()
        if not agent:
            flash(f'Agent "{agent_username}" not found.', 'danger')
            return redirect(url_for('admin.admin_add_numbers_to_agent'))

        # Check if user is an agent
        if not agent.is_agent():
            flash('Selected user is not an agent.', 'danger')
            return redirect(url_for('admin.admin_add_numbers_to_agent'))

        # Get the range
        sms_range = SMDRange.query.get(range_id)
        if not sms_range:
            flash('Invalid range selected.', 'danger')
            return redirect(url_for('admin.admin_add_numbers_to_agent'))

        # Get available numbers from the range
        available_numbers = SMSNumber.query.filter_by(
            range_id=range_id,
            agent_id=None,
            is_active=True
        ).limit(numbers_count).all()

        if not available_numbers:
            flash('No available numbers in this range.', 'warning')
            return redirect(url_for('admin.admin_add_numbers_to_agent'))

        # Assign numbers to agent
        numbers_added = 0
        added_numbers = []
        for num in available_numbers:
            num.agent_id = agent.id
            num.status = 'reserved'
            num.assigned_at = datetime.utcnow()
            added_numbers.append(num.number)
            numbers_added += 1

        db.session.commit()

        # Log activity
        ActivityLog.log(
            current_user.id,
            'admin_add_numbers_to_agent',
            f'Added {numbers_added} numbers from range {sms_range.prefix} to agent {agent.username}',
            ip_address=request.remote_addr
        )

        flash(f'{numbers_added} numbers added to agent {agent.username} successfully!', 'success')

        # تنزيل ملف الأرقام المضافة
        return download_added_numbers(added_numbers, agent.username)

    # Get all active ranges
    ranges = SMDRange.query.filter_by(is_active=True).all()

    return render_template('admin/admin_add_numbers_to_agent.html',
        ranges=ranges
    )


def download_added_numbers(numbers_list, agent_username):
    """تنزيل ملف نصي بالأرقام المضافة"""
    content = "\n".join(numbers_list)

    response = make_response(content)
    response.headers['Content-Type'] = 'text/plain'
    response.headers['Content-Disposition'] = f'attachment; filename=numbers_added_to_{agent_username}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.txt'

    return response

# ============ AGENT DOWNLOAD NUMBERS ============

@admin_bp.route('/agent/download-numbers', methods=['GET', 'POST'])
@login_required
def agent_download_numbers():
    """Download agent's numbers as text file"""
    # Check if user is agent
    if not (current_user.is_agent() or current_user.is_admin()):
        flash('Access denied. Agent account required.', 'danger')
        return redirect(url_for('main.dashboard'))

    numbers_query = SMSNumber.query.filter_by(agent_id=current_user.id)

    # Handle selected IDs from POST
    if request.method == 'POST':
        selected_ids = request.form.get('selected_ids', '')
        if selected_ids:
            ids_list = [int(x.strip()) for x in selected_ids.split(',') if x.strip().isdigit()]
            numbers_query = numbers_query.filter(SMSNumber.id.in_(ids_list))

    numbers = numbers_query.all()

    if not numbers:
        flash('No numbers found to download.', 'warning')
        return redirect(url_for('admin.agent_my_numbers'))

    # Generate text content
    content = ""
    for num in numbers:
        content += f"{num.number}\n"

    # Create response
    response = make_response(content)
    response.headers['Content-Type'] = 'text/plain'
    response.headers['Content-Disposition'] = f'attachment; filename=numbers_{current_user.username}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.txt'

    return response

# ============ ADMIN DELETE NUMBERS FROM AGENT ============

@admin_bp.route('/admin/delete-numbers-from-agent', methods=['GET', 'POST'])
@admin_required
def admin_delete_numbers_from_agent():
    """Admin deletes numbers from a specific agent"""
    if request.method == 'POST':
        agent_username = request.form.get('agent_username', '').strip()
        range_id = request.form.get('range_id', type=int)

        if not agent_username:
            flash('Please enter agent username.', 'danger')
            return redirect(url_for('admin.admin_delete_numbers_from_agent'))

        if not range_id:
            flash('Please select a range.', 'danger')
            return redirect(url_for('admin.admin_delete_numbers_from_agent'))

        # Get the agent by username
        agent = User.query.filter_by(username=agent_username).first()
        if not agent:
            flash(f'Agent "{agent_username}" not found.', 'danger')
            return redirect(url_for('admin.admin_delete_numbers_from_agent'))

        # Get the range
        sms_range = SMDRange.query.get(range_id)
        if not sms_range:
            flash('Range not found.', 'danger')
            return redirect(url_for('admin.admin_delete_numbers_from_agent'))

        # Get numbers to delete (only those assigned to this agent)
        numbers_to_delete = SMSNumber.query.filter_by(
            agent_id=agent.id,
            range_id=range_id
        ).all()

        if not numbers_to_delete:
            flash('No numbers found for this agent in the selected range.', 'warning')
            return redirect(url_for('admin.admin_delete_numbers_from_agent'))

        # Get count before deletion for logging
        numbers_count = len(numbers_to_delete)

        # Permanently delete the numbers
        for num in numbers_to_delete:
            db.session.delete(num)

        db.session.commit()

        # Log activity
        ActivityLog.log(
            current_user.id,
            'admin_delete_numbers_from_agent',
            f'Deleted {numbers_count} numbers from range {sms_range.prefix} of agent {agent.username}',
            ip_address=request.remote_addr
        )

        flash(f'{numbers_count} numbers permanently deleted from agent {agent.username}!', 'success')
        return redirect(url_for('admin.admin_delete_numbers_from_agent'))

    return render_template('admin/admin_delete_numbers_from_agent.html')

# ============ GET AGENT RANGES (API) ============

@admin_bp.route('/admin/get-agent-ranges/<int:agent_id>')
@admin_required
def get_agent_ranges(agent_id):
    """Get ranges that have numbers assigned to a specific agent"""
    # Get distinct ranges with numbers for this agent
    ranges_data = db.session.query(
        SMDRange.id,
        SMDRange.prefix,
        SMDRange.country,
        db.func.count(SMSNumber.id).label('count')
    ).join(SMSNumber, SMSNumber.range_id == SMDRange.id
    ).filter(
        SMSNumber.agent_id == agent_id
    ).group_by(SMDRange.id).all()

    ranges = [{'id': r.id, 'prefix': r.prefix, 'country': r.country, 'count': r.count} for r in ranges_data]

    return jsonify({'ranges': ranges})

@admin_bp.route('/admin/get-agent-range-count/<int:agent_id>/<int:range_id>')
@admin_required
def get_agent_range_count(agent_id, range_id):
    """Get count of numbers for a specific agent and range"""
    count = SMSNumber.query.filter_by(
        agent_id=agent_id,
        range_id=range_id
    ).count()

    return jsonify({'count': count})

# ============ SEARCH USER API ============

@admin_bp.route('/admin/search-user')
@admin_required
def search_user():
    """Search for user by username"""
    username = request.args.get('username', '').strip()

    if not username:
        return jsonify({'found': False, 'message': 'Username is required'})

    user = User.query.filter_by(username=username).first()

    if not user:
        return jsonify({'found': False})

    return jsonify({
        'found': True,
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'role': user.role.name if user.role else 'unknown',
        'is_active': user.is_active
    })

# ============ CLIENT MANAGEMENT (ADMIN) ============

@admin_bp.route('/admin/clients')
@admin_required
def admin_clients():
    """Admin manages all clients and their numbers"""
    page = request.args.get('page', 1, type=int)
    per_page = 25
    search = request.args.get('search', '')
    agent_filter = request.args.get('agent', '')

    query = User.query.filter(User.role.has(name='client'))

    if search:
        query = query.filter(
            db.or_(
                User.username.like(f'%{search}%'),
                User.email.like(f'%{search}%'),
                User.name.like(f'%{search}%')
            )
        )

    if agent_filter:
        query = query.filter_by(agent_id=agent_filter)

    clients = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    agents = User.query.filter(User.role.has(name='agent')).all()

    return render_template('admin/admin_clients.html',
        clients=clients,
        agents=agents
    )

@admin_bp.route('/admin/add-numbers-to-client', methods=['GET', 'POST'])
@admin_required
def admin_add_numbers_to_client():
    """Admin adds numbers to a specific client"""
    if request.method == 'POST':
        client_username = request.form.get('client_username', '').strip()
        numbers_count = request.form.get('numbers_count', 0, type=int)

        if not client_username:
            flash('Please enter client username.', 'danger')
            return redirect(url_for('admin.admin_add_numbers_to_client'))

        if numbers_count <= 0:
            flash('Please enter a valid number of numbers.', 'danger')
            return redirect(url_for('admin.admin_add_numbers_to_client'))

        client = User.query.filter_by(username=client_username).first()
        if not client:
            flash(f'Client "{client_username}" not found.', 'danger')
            return redirect(url_for('admin.admin_add_numbers_to_client'))

        if not client.is_client():
            flash('Selected user is not a client.', 'danger')
            return redirect(url_for('admin.admin_add_numbers_to_client'))

        # Get available numbers from the parent agent
        if client.agent_id:
            available_numbers = SMSNumber.query.filter_by(
                agent_id=client.agent_id,
                client_id=None,
                is_active=True
            ).limit(numbers_count).all()
        else:
            flash('Client has no agent assigned.', 'danger')
            return redirect(url_for('admin.admin_add_numbers_to_client'))

        if not available_numbers:
            flash('No available numbers for this client.', 'warning')
            return redirect(url_for('admin.admin_add_numbers_to_client'))

        # Assign numbers to client
        numbers_added = []
        for num in available_numbers:
            num.client_id = client.id
            num.status = 'activated'
            numbers_added.append(num.number)

        db.session.commit()

        # Log activity
        ActivityLog.log(
            current_user.id,
            'admin_add_numbers_to_client',
            f'Added {len(numbers_added)} numbers to client {client.username}',
            ip_address=request.remote_addr
        )

        flash(f'{len(numbers_added)} numbers added to client {client.username}!', 'success')

        # Download file
        return download_client_numbers(numbers_added, client.username)

    return render_template('admin/admin_add_numbers_to_client.html')

@admin_bp.route('/admin/remove-numbers-from-client', methods=['GET', 'POST'])
@admin_required
def admin_remove_numbers_from_client():
    """Admin removes numbers from a specific client"""
    if request.method == 'POST':
        client_username = request.form.get('client_username', '').strip()
        numbers_count = request.form.get('numbers_count', 0, type=int)

        if not client_username:
            flash('Please enter client username.', 'danger')
            return redirect(url_for('admin.admin_remove_numbers_from_client'))

        if numbers_count <= 0:
            flash('Please enter a valid number of numbers.', 'danger')
            return redirect(url_for('admin.admin_remove_numbers_from_client'))

        client = User.query.filter_by(username=client_username).first()
        if not client:
            flash(f'Client "{client_username}" not found.', 'danger')
            return redirect(url_for('admin.admin_remove_numbers_from_client'))

        # Get client's numbers
        client_numbers = SMSNumber.query.filter_by(
            client_id=client.id
        ).limit(numbers_count).all()

        if not client_numbers:
            flash('No numbers found for this client.', 'warning')
            return redirect(url_for('admin.admin_remove_numbers_from_client'))

        removed_count = 0
        for num in client_numbers:
            num.client_id = None
            num.status = 'reserved'
            removed_count += 1

        db.session.commit()

        ActivityLog.log(
            current_user.id,
            'admin_remove_numbers_from_client',
            f'Removed {removed_count} numbers from client {client.username}',
            ip_address=request.remote_addr
        )

        flash(f'{removed_count} numbers removed from client {client.username}!', 'success')
        return redirect(url_for('admin.admin_clients'))

    return render_template('admin/admin_remove_numbers_from_client.html')


def download_client_numbers(numbers_list, client_username):
    """تنزيل ملف نصي بأرقام العميل"""
    content = "\n".join(numbers_list)

    response = make_response(content)
    response.headers['Content-Type'] = 'text/plain'
    response.headers['Content-Disposition'] = f'attachment; filename=numbers_for_client_{client_username}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.txt'

    return response


# ============ MESSAGE DELETION SETTINGS ============

@admin_bp.route('/admin/message-settings', methods=['GET', 'POST'])
@admin_required
def message_settings():
    """Manage message deletion settings per client"""
    if request.method == 'POST':
        client_username = request.form.get('client_username', '').strip()
        delete_mode = request.form.get('delete_mode', 'never')  # never, minutes, hours
        delete_value = request.form.get('delete_value', 0, type=int)

        client = User.query.filter_by(username=client_username).first()
        if not client:
            flash(f'Client "{client_username}" not found.', 'danger')
            return redirect(url_for('admin.message_settings'))

        if not client.is_client():
            flash('Selected user is not a client.', 'danger')
            return redirect(url_for('admin.message_settings'))

        # Update client's message deletion settings
        if delete_mode == 'never':
            client.delete_messages_after = 0
        elif delete_mode == 'minutes':
            client.delete_messages_after = delete_value
        elif delete_mode == 'hours':
            client.delete_messages_after = delete_value * 60

        db.session.commit()

        flash(f'Message settings updated for client {client.username}.', 'success')
        return redirect(url_for('admin.message_settings'))

    # Get all clients
    clients = User.query.filter(User.role.has(name='client')).all()

    return render_template('admin/message_settings.html', clients=clients)

# ============ GET CLIENT NUMBERS API ============

@admin_bp.route('/admin/get-client-numbers')
@admin_required
def get_client_numbers():
    """Get numbers for a specific client"""
    username = request.args.get('username', '').strip()

    if not username:
        return jsonify({'error': 'Username is required', 'numbers': []})

    client = User.query.filter_by(username=username).first()

    if not client:
        return jsonify({'error': 'Client not found', 'numbers': []})

    numbers = SMSNumber.query.filter_by(client_id=client.id).all()

    return jsonify({
        'count': len(numbers),
        'numbers': [{
            'number': num.number,
            'range': num.sms_range.country if num.sms_range else '-',
            'status': num.status
        } for num in numbers]
    })
