from flask import Blueprint, request, jsonify
from flask_login import current_user
from app import db
from app.models.sms import SMDRange, SMSNumber, SMSCDR
from app.models.user import User
from app.models.activity import ActivityLog
from datetime import datetime, timedelta
from functools import wraps
import random

api_bp = Blueprint('api', __name__)


# ── API Authentication ────────────────────────────────────────────────────────

def api_auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_token = request.headers.get('X-API-Token') or request.args.get('api_token')
        if not api_token:
            return jsonify({'error': 'API token required'}), 401
        user = User.query.filter_by(api_token=api_token).first()
        if not user:
            return jsonify({'error': 'Invalid API token'}), 401
        if not user.is_active:
            return jsonify({'error': 'Account inactive'}), 403
        return f(user, *args, **kwargs)
    return decorated


# ── SMS SEND ──────────────────────────────────────────────────────────────────

@api_bp.route('/sms/send', methods=['POST'])
@api_auth_required
def send_sms(user):
    """
    Send SMS via API
    POST /api/sms/send
    Headers: X-API-Token: <token>
    Body JSON: { number, destination, cli, message }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    number = data.get('number')
    destination = data.get('destination')
    cli = data.get('cli')
    message = data.get('message')

    if not all([number, destination, cli, message]):
        return jsonify({'error': 'number, destination, cli, and message are required'}), 400

    sms_number = SMSNumber.query.filter_by(number=number).first()
    if not sms_number:
        return jsonify({'error': 'SMS number not found'}), 404

    if sms_number.agent_id != user.id and sms_number.client_id != user.id and not user.is_admin():
        return jsonify({'error': 'You do not have access to this number'}), 403

    agent_payout  = sms_number.agent_payout  or 0.005
    client_payout = sms_number.client_payout or 0.005
    sms_range     = sms_number.sms_range
    rate          = sms_range.cost_per_sms if sms_range else 0.005
    currency      = sms_range.currency     if sms_range else 'USD'
    profit        = rate - agent_payout - client_payout

    cdr = SMSCDR(
        number_id=sms_number.id,
        range_id=sms_number.range_id,
        user_id=sms_number.agent_id,
        client_id=sms_number.client_id,
        destination=destination,
        cli=cli,
        message=message,
        sms_type='sent',
        status='completed',
        rate=rate,
        agent_payout=agent_payout,
        client_payout=client_payout,
        profit=profit,
        currency=currency
    )
    db.session.add(cdr)
    db.session.commit()

    ActivityLog.log(user.id, 'api_send_sms',
                    f'Sent SMS from {number} to {destination}',
                    ip_address=request.remote_addr)

    return jsonify({
        'success': True,
        'cdr_id': cdr.id,
        'message': 'SMS sent successfully',
        'rate': rate,
        'agent_payout': agent_payout,
        'client_payout': client_payout,
        'profit': profit
    })


@api_bp.route('/sms/send-bulk', methods=['POST'])
@api_auth_required
def send_sms_bulk(user):
    """
    Send bulk SMS via API
    POST /api/sms/send-bulk
    Body JSON: { number, destinations: [], cli, message }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    number = data.get('number')
    destinations = data.get('destinations', [])
    cli = data.get('cli')
    message = data.get('message')

    if not all([number, destinations, cli, message]):
        return jsonify({'error': 'number, destinations, cli, and message are required'}), 400

    if not isinstance(destinations, list) or len(destinations) == 0:
        return jsonify({'error': 'destinations must be a non-empty list'}), 400

    from flask import current_app
    max_dest = current_app.config.get('BULK_SMS_MAX_DESTINATIONS', 500)
    if len(destinations) > max_dest:
        return jsonify({'error': f'Too many destinations. Maximum allowed is {max_dest}.'}), 400

    sms_number = SMSNumber.query.filter_by(number=number).first()
    if not sms_number:
        return jsonify({'error': 'SMS number not found'}), 404

    if sms_number.agent_id != user.id and sms_number.client_id != user.id and not user.is_admin():
        return jsonify({'error': 'You do not have access to this number'}), 403

    agent_payout  = sms_number.agent_payout  or 0.005
    client_payout = sms_number.client_payout or 0.005
    sms_range     = sms_number.sms_range
    rate          = sms_range.cost_per_sms if sms_range else 0.005
    currency      = sms_range.currency     if sms_range else 'USD'
    profit        = rate - agent_payout - client_payout

    cdrs_created = []
    for dest in destinations:
        cdr = SMSCDR(
            number_id=sms_number.id,
            range_id=sms_number.range_id,
            user_id=sms_number.agent_id,
            client_id=sms_number.client_id,
            destination=dest,
            cli=cli,
            message=message,
            sms_type='sent',
            status='completed',
            rate=rate,
            agent_payout=agent_payout,
            client_payout=client_payout,
            profit=profit,
            currency=currency
        )
        db.session.add(cdr)
        cdrs_created.append(cdr)

    db.session.commit()

    ActivityLog.log(user.id, 'api_send_sms_bulk',
                    f'Sent bulk SMS ({len(destinations)}) from {number}',
                    ip_address=request.remote_addr)

    return jsonify({
        'success': True,
        'count': len(cdrs_created),
        'cdr_ids': [c.id for c in cdrs_created],
        'message': f'{len(destinations)} SMS sent successfully',
        'rate': rate,
        'agent_payout': agent_payout,
        'client_payout': client_payout,
        'total_profit': len(destinations) * profit
    })


# ── SMS RECEIVE (webhook) ─────────────────────────────────────────────────────

@api_bp.route('/sms/receive', methods=['POST'])
def receive_sms():
    """
    Receive SMS webhook — secured with X-Webhook-Secret header.
    POST /api/sms/receive
    Body JSON: { number, from, cli, message }
    """
    from flask import current_app
    webhook_secret = current_app.config.get('WEBHOOK_SECRET', '')
    if not webhook_secret:
        return jsonify({'error': 'Webhook endpoint is disabled (no WEBHOOK_SECRET configured)'}), 503
    provided = request.headers.get('X-Webhook-Secret', '')
    import hmac
    if not hmac.compare_digest(webhook_secret, provided):
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    number = data.get('number')
    from_num = data.get('from')
    cli = data.get('cli')
    message = data.get('message')

    if not number or not from_num:
        return jsonify({'error': 'number and from are required'}), 400

    reserved_number = SMSNumber.query.filter_by(number=number).first()
    if not reserved_number:
        return jsonify({'error': 'SMS number not found'}), 404

    if not reserved_number.agent_id:
        return jsonify({'error': 'Number has no assigned owner'}), 400

    agent_payout  = reserved_number.agent_payout  or 0.005
    client_payout = reserved_number.client_payout or 0.005
    sms_range     = reserved_number.sms_range
    rate          = sms_range.cost_per_sms if sms_range else 0.005
    currency      = sms_range.currency     if sms_range else 'USD'
    profit        = rate - agent_payout - client_payout

    cdr = SMSCDR(
        number_id=reserved_number.id,
        range_id=reserved_number.range_id,
        user_id=reserved_number.agent_id,
        client_id=reserved_number.client_id,
        caller_id=from_num,
        cli=cli or from_num,
        destination=number,
        message=message,
        sms_type='received',
        status='completed',
        rate=rate,
        agent_payout=agent_payout,
        client_payout=client_payout,
        profit=profit,
        currency=currency
    )
    db.session.add(cdr)
    db.session.commit()

    ActivityLog.log(reserved_number.agent_id, 'sms_received',
                    f'Received SMS on {number} from {from_num}',
                    ip_address=request.remote_addr)

    return jsonify({
        'success': True,
        'cdr_id': cdr.id,
        'message': 'SMS received and logged'
    })


# ── SMS SCR (Simulate Receive — testing) ─────────────────────────────────────

@api_bp.route('/sms/scr', methods=['POST'])
@api_auth_required
def sms_scr(user):
    """
    Simulate SMS received on a number (for testing)
    POST /api/sms/scr
    Body JSON: { number, from, cli, message }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    number = data.get('number')
    from_num = data.get('from', '')
    cli = data.get('cli', '')
    message = data.get('message', '')

    if not number:
        return jsonify({'error': 'number is required'}), 400

    sms_number = SMSNumber.query.filter_by(number=number).first()
    if not sms_number:
        return jsonify({'error': 'SMS number not found'}), 404

    if sms_number.agent_id != user.id and sms_number.client_id != user.id and not user.is_admin():
        return jsonify({'success': False, 'error': 'You do not own this number'}), 403

    agent_payout  = sms_number.agent_payout  or 0.005
    client_payout = sms_number.client_payout or 0.005
    sms_range     = sms_number.sms_range
    rate          = sms_range.cost_per_sms if sms_range else 0.005
    currency      = sms_range.currency     if sms_range else 'USD'
    profit        = rate - agent_payout - client_payout

    cdr = SMSCDR(
        number_id=sms_number.id,
        range_id=sms_number.range_id,
        user_id=sms_number.agent_id,
        client_id=sms_number.client_id,
        caller_id=from_num or 'SCR-TEST',
        cli=cli or 'Test',
        destination=number,
        message=message or 'Test SMS received',
        sms_type='received',
        status='completed',
        rate=rate,
        agent_payout=agent_payout,
        client_payout=client_payout,
        profit=profit,
        currency=currency
    )
    db.session.add(cdr)
    db.session.commit()

    ActivityLog.log(user.id, 'sms_scr_received',
                    f'SCR: Received SMS on {number} from {from_num}',
                    ip_address=request.remote_addr)

    return jsonify({
        'success': True,
        'cdr_id': cdr.id,
        'message': 'SMS received on your number',
        'rate': rate,
        'agent_payout': agent_payout,
        'client_payout': client_payout,
        'profit': profit,
        'number': number,
        'from': from_num,
        'cli': cli
    })


# ── SMS RANGES ────────────────────────────────────────────────────────────────

@api_bp.route('/sms/ranges')
@api_auth_required
def get_sms_ranges(user):
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 500)
    search = request.args.get('search', '')

    query = SMDRange.query.filter_by(is_active=True)
    if search:
        query = query.filter(
            db.or_(
                SMDRange.prefix.like(f'%{search}%'),
                SMDRange.country.like(f'%{search}%'),
                SMDRange.name.like(f'%{search}%')
            )
        )

    pagination = query.order_by(SMDRange.country).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        'results': [r.to_dict() for r in pagination.items],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': pagination.total,
            'pages': pagination.pages,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev
        }
    })


@api_bp.route('/sms/ranges/<int:range_id>')
@api_auth_required
def get_sms_range(user, range_id):
    range_obj = SMDRange.query.get_or_404(range_id)
    return jsonify(range_obj.to_dict())


# ── SMS NUMBERS ───────────────────────────────────────────────────────────────

@api_bp.route('/sms/numbers')
@api_auth_required
def get_sms_numbers(user):
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 1000)
    range_id = request.args.get('range_id', type=int)
    client_id = request.args.get('client_id', type=int)

    query = SMSNumber.query.filter_by(agent_id=user.id)
    if range_id:
        query = query.filter_by(range_id=range_id)
    if client_id:
        query = query.filter_by(client_id=client_id)

    pagination = query.order_by(SMSNumber.number).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        'results': [n.to_dict() for n in pagination.items],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': pagination.total,
            'pages': pagination.pages
        }
    })


@api_bp.route('/sms/numbers/request', methods=['POST'])
@api_auth_required
def request_sms_numbers(user):
    """
    Reserve numbers from a range pool
    POST /api/sms/numbers/request
    Body JSON: { range_id, quantity }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    range_id = data.get('range_id')
    quantity = data.get('quantity', 1)

    if not range_id or not quantity:
        return jsonify({'error': 'range_id and quantity required'}), 400

    try:
        quantity = int(quantity)
        if quantity < 1 or quantity > 10000:
            return jsonify({'error': 'quantity must be between 1 and 10000'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'quantity must be an integer'}), 400

    sms_range = SMDRange.query.get_or_404(range_id)
    if not sms_range.is_active:
        return jsonify({'error': 'Range not available'}), 400

    if user.sms_limit > 0:
        current_count = SMSNumber.query.filter_by(agent_id=user.id).count()
        if current_count + quantity > user.sms_limit:
            return jsonify({'error': 'SMS limit exceeded'}), 400

    available_count = sms_range.get_available_count()
    if quantity > available_count:
        return jsonify({'error': f'Only {available_count} numbers available in this range'}), 400

    numbers_created = []
    try:
        for _ in range(quantity):
            # Generate unique number
            base_ts = int(datetime.utcnow().timestamp() * 1000) % 100000000
            attempts = 0
            while True:
                rand_part = random.randint(1000, 9999)
                candidate = f"{sms_range.prefix}{base_ts}{rand_part}"[-12:]
                if not SMSNumber.query.filter_by(number=candidate).first():
                    break
                attempts += 1
                if attempts > 20:
                    return jsonify({'error': 'Could not generate unique numbers, try again'}), 500

            sms_number = SMSNumber(
                range_id=range_id,
                number=candidate,
                prefix=sms_range.prefix,
                agent_id=user.id,
                agent_payout=sms_range.payout,
                is_active=True
            )
            db.session.add(sms_number)
            numbers_created.append(candidate)

        db.session.commit()

        ActivityLog.log(user.id, 'request_numbers',
                        f'Reserved {quantity} numbers from range {sms_range.prefix}',
                        ip_address=request.remote_addr)

        return jsonify({
            'success': True,
            'numbers': numbers_created,
            'count': len(numbers_created),
            'message': f'{quantity} numbers reserved successfully'
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


# ── SMS CDR ───────────────────────────────────────────────────────────────────

@api_bp.route('/sms/cdr')
@api_auth_required
def get_sms_cdr(user):
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 500)
    range_id = request.args.get('range_id', type=int)
    client_id = request.args.get('client_id', type=int)
    sms_type = request.args.get('type')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    query = SMSCDR.query.filter_by(user_id=user.id)

    if range_id:
        query = query.filter_by(range_id=range_id)
    if client_id:
        query = query.filter_by(client_id=client_id)
    if sms_type:
        query = query.filter_by(sms_type=sms_type)
    if date_from:
        try:
            query = query.filter(SMSCDR.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(SMSCDR.created_at <= datetime.fromisoformat(date_to))
        except ValueError:
            pass

    pagination = query.order_by(SMSCDR.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        'results': [cdr.to_dict() for cdr in pagination.items],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': pagination.total,
            'pages': pagination.pages
        }
    })


@api_bp.route('/sms/cdr/stats')
@api_auth_required
def get_sms_stats(user):
    today = datetime.utcnow().date()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    stats = {
        'today': SMSCDR.query.filter(
            SMSCDR.user_id == user.id,
            db.func.date(SMSCDR.created_at) == today
        ).count(),
        'week': SMSCDR.query.filter(
            SMSCDR.user_id == user.id,
            SMSCDR.created_at >= week_ago
        ).count(),
        'month': SMSCDR.query.filter(
            SMSCDR.user_id == user.id,
            SMSCDR.created_at >= month_ago
        ).count(),
        'total': SMSCDR.query.filter_by(user_id=user.id).count()
    }

    stats['received_today'] = SMSCDR.query.filter(
        SMSCDR.user_id == user.id,
        SMSCDR.sms_type == 'received',
        db.func.date(SMSCDR.created_at) == today
    ).count()

    # Use SQLAlchemy case() — compatible with SQLite and PostgreSQL
    revenue = db.session.query(
        db.func.sum(SMSCDR.profit).label('total_profit'),
        db.func.sum(db.case((SMSCDR.currency == 'USD', SMSCDR.profit), else_=0)).label('usd'),
        db.func.sum(db.case((SMSCDR.currency == 'EUR', SMSCDR.profit), else_=0)).label('eur'),
        db.func.sum(db.case((SMSCDR.currency == 'GBP', SMSCDR.profit), else_=0)).label('gbp')
    ).filter(SMSCDR.user_id == user.id).first()

    stats['revenue'] = {
        'total': float(revenue.total_profit or 0) if revenue else 0,
        'USD': float(revenue.usd or 0) if revenue else 0,
        'EUR': float(revenue.eur or 0) if revenue else 0,
        'GBP': float(revenue.gbp or 0) if revenue else 0
    }

    return jsonify(stats)


# ── CLIENTS ───────────────────────────────────────────────────────────────────

@api_bp.route('/clients')
@api_auth_required
def get_clients(user):
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 500)
    search = request.args.get('search', '')

    query = User.query.filter_by(agent_id=user.id)
    if search:
        query = query.filter(
            db.or_(
                User.username.like(f'%{search}%'),
                User.email.like(f'%{search}%'),
                User.name.like(f'%{search}%')
            )
        )

    pagination = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        'results': [c.to_dict() for c in pagination.items],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': pagination.total,
            'pages': pagination.pages
        }
    })


@api_bp.route('/clients', methods=['POST'])
@api_auth_required
def create_client(user):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    username = data.get('username')
    password = data.get('password')
    email = data.get('email')

    if not username or not password:
        return jsonify({'error': 'username and password required'}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already exists'}), 400

    if email and User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 400

    from app.models.user import Role
    client_role = Role.query.filter_by(name='client').first()

    client = User(
        username=username,
        email=email or f'{username}@client.local',
        role=client_role,
        agent_id=user.id,
        is_active=True
    )
    client.set_password(password)

    for field in ['name', 'company', 'country', 'skype', 'sms_limit']:
        if data.get(field) is not None:
            setattr(client, field, data[field])

    db.session.add(client)
    db.session.commit()

    ActivityLog.log(user.id, 'create_client', f'Created client {username}',
                    ip_address=request.remote_addr)

    return jsonify({'success': True, 'client': client.to_dict()}), 201


@api_bp.route('/clients/<int:client_id>')
@api_auth_required
def get_client(user, client_id):
    client = User.query.filter_by(id=client_id, agent_id=user.id).first()
    if not client:
        return jsonify({'error': 'Client not found'}), 404
    return jsonify(client.to_dict())


@api_bp.route('/clients/<int:client_id>', methods=['PUT'])
@api_auth_required
def update_client(user, client_id):
    client = User.query.filter_by(id=client_id, agent_id=user.id).first()
    if not client:
        return jsonify({'error': 'Client not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    for field in ['name', 'email', 'company', 'country', 'skype',
                  'contact', 'address', 'sms_limit', 'is_active']:
        if field in data:
            setattr(client, field, data[field])
    if 'password' in data:
        client.set_password(data['password'])

    db.session.commit()

    ActivityLog.log(user.id, 'update_client', f'Updated client {client.username}',
                    ip_address=request.remote_addr)

    return jsonify({'success': True, 'client': client.to_dict()})


@api_bp.route('/clients/<int:client_id>', methods=['DELETE'])
@api_auth_required
def delete_client(user, client_id):
    client = User.query.filter_by(id=client_id, agent_id=user.id).first()
    if not client:
        return jsonify({'error': 'Client not found'}), 404

    if SMSNumber.query.filter_by(client_id=client_id).count() > 0:
        return jsonify({'error': 'Cannot delete client with assigned numbers'}), 400

    username = client.username
    db.session.delete(client)
    db.session.commit()

    ActivityLog.log(user.id, 'delete_client', f'Deleted client {username}',
                    ip_address=request.remote_addr)

    return jsonify({'success': True, 'message': f'Client {username} deleted'})


# ── NUMBER ALLOCATION ─────────────────────────────────────────────────────────

@api_bp.route('/numbers/allocate', methods=['POST'])
@api_auth_required
def allocate_number(user):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    number_id = data.get('number_id')
    client_id = data.get('client_id')

    if not number_id:
        return jsonify({'error': 'number_id required'}), 400

    number = SMSNumber.query.filter_by(id=number_id, agent_id=user.id).first()
    if not number:
        return jsonify({'error': 'Number not found'}), 404

    if client_id:
        client = User.query.filter_by(id=client_id, agent_id=user.id).first()
        if not client:
            return jsonify({'error': 'Client not found'}), 404
        number.client_id = client_id

    number.assigned_at = datetime.utcnow()
    db.session.commit()

    ActivityLog.log(user.id, 'allocate_number',
                    f'Allocated number {number.number} to client {client_id}',
                    ip_address=request.remote_addr)

    return jsonify({'success': True, 'number': number.to_dict()})


@api_bp.route('/numbers/unallocate', methods=['POST'])
@api_auth_required
def unallocate_number(user):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    number_id = data.get('number_id')
    if not number_id:
        return jsonify({'error': 'number_id required'}), 400

    number = SMSNumber.query.filter_by(id=number_id, agent_id=user.id).first()
    if not number:
        return jsonify({'error': 'Number not found'}), 404

    number.client_id = None
    number.assigned_at = None
    db.session.commit()

    ActivityLog.log(user.id, 'unallocate_number', f'Unallocated number {number.number}',
                    ip_address=request.remote_addr)

    return jsonify({'success': True, 'number': number.to_dict()})


@api_bp.route('/numbers/bulk-allocate', methods=['POST'])
@api_auth_required
def bulk_allocate(user):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    number_ids = data.get('number_ids', [])
    client_id = data.get('client_id')

    if not number_ids:
        return jsonify({'error': 'number_ids required'}), 400

    if client_id:
        client = User.query.filter_by(id=client_id, agent_id=user.id).first()
        if not client:
            return jsonify({'error': 'Client not found'}), 404

    updated = 0
    for number_id in number_ids:
        number = SMSNumber.query.filter_by(id=number_id, agent_id=user.id).first()
        if number:
            number.client_id = client_id
            number.assigned_at = datetime.utcnow()
            updated += 1

    db.session.commit()
    return jsonify({'success': True, 'updated': updated})


# ── BULK IMPORT (CSV) ─────────────────────────────────────────────────────────

@api_bp.route('/numbers/import-csv', methods=['POST'])
@api_auth_required
def import_numbers_csv(user):
    """
    Import numbers from CSV file (admin only)
    POST /api/numbers/import-csv
    Form-data: range_id, csv_file, skip_existing (optional, default true)
    CSV format: one phone number per line
    """
    if not user.is_admin():
        return jsonify({'error': 'Admin access required for bulk import'}), 403

    range_id = request.form.get('range_id', type=int)
    skip_existing = request.form.get('skip_existing', 'true').lower() == 'true'
    csv_file = request.files.get('csv_file')

    if not range_id:
        return jsonify({'error': 'range_id is required'}), 400
    if not csv_file:
        return jsonify({'error': 'csv_file is required'}), 400

    sms_range = SMDRange.query.get(range_id)
    if not sms_range:
        return jsonify({'error': 'Range not found'}), 404

    try:
        content = csv_file.read().decode('utf-8')
        lines = content.strip().split('\n')
    except Exception as e:
        return jsonify({'error': 'Failed to read CSV file. Ensure it is valid UTF-8 text.'}), 400

    imported = 0
    duplicates_skipped = 0
    errors = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        number = ''.join(c for c in line if c.isdigit())
        if not number:
            errors += 1
            continue

        try:
            if skip_existing and SMSNumber.query.filter_by(number=number).first():
                duplicates_skipped += 1
                continue

            sms_num = SMSNumber(
                range_id=range_id,
                number=number,
                prefix=sms_range.prefix,
                operator=sms_range.operator,
                network_type=sms_range.network_type,
                mcc=sms_range.mcc,
                mnc=sms_range.mnc,
                status='available',
                is_active=True,
                created_at=datetime.utcnow()
            )
            db.session.add(sms_num)
            imported += 1

            if imported % 1000 == 0:
                db.session.commit()

        except Exception as e:
            errors += 1

    db.session.commit()

    ActivityLog.log(user.id, 'bulk_import_csv',
                    f'Imported {imported} numbers to range {sms_range.prefix}',
                    ip_address=request.remote_addr)

    return jsonify({
        'success': True,
        'imported': imported,
        'duplicates_skipped': duplicates_skipped,
        'errors': errors,
        'message': f'{imported} numbers imported successfully'
    })
