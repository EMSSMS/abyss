"""
developer.py — Developer role routes
=====================================
Allows 'developer' users to manage static assets:
  img / css / js / html
Each HTML asset gets its own live page at /dev/pages/<slug>
"""
import os, re
from functools import wraps
from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, flash, abort, Response)
from flask_login import login_required, current_user
from app import db
from app.models.developer import StaticAsset
from app.models.activity import ActivityLog

dev_bp = Blueprint('dev', __name__, url_prefix='/dev')

ALLOWED_TYPES = {'img', 'css', 'js', 'html'}

# ─── Guard ───────────────────────────────────────────────────────────────────

def dev_required(f):
    @wraps(f)
    def d(*a, **kw):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if not (current_user.is_admin() or current_user.is_developer()):
            flash('Developer or Admin access required.', 'danger')
            return redirect(url_for('main.dashboard'))
        return f(*a, **kw)
    return d


def _slug(name):
    s = os.path.splitext(name)[0].lower()
    return re.sub(r'[^a-z0-9-]', '-', s).strip('-')


# ─── Dashboard ───────────────────────────────────────────────────────────────

@dev_bp.route('/')
@login_required
@dev_required
def index():
    imgs   = StaticAsset.query.filter_by(asset_type='img',  is_active=True).order_by(StaticAsset.created_at.desc()).all()
    csss   = StaticAsset.query.filter_by(asset_type='css',  is_active=True).order_by(StaticAsset.created_at.desc()).all()
    jss    = StaticAsset.query.filter_by(asset_type='js',   is_active=True).order_by(StaticAsset.created_at.desc()).all()
    htmls  = StaticAsset.query.filter_by(asset_type='html', is_active=True).order_by(StaticAsset.created_at.desc()).all()
    return render_template('developer/index.html',
                           imgs=imgs, csss=csss, jss=jss, htmls=htmls)


# ─── Upload / Create ─────────────────────────────────────────────────────────

@dev_bp.route('/upload', methods=['GET', 'POST'])
@login_required
@dev_required
def upload():
    if request.method == 'POST':
        asset_type  = request.form.get('asset_type', '').strip().lower()
        filename    = request.form.get('filename', '').strip()
        title       = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        content     = request.form.get('content', '')

        if asset_type not in ALLOWED_TYPES:
            flash('Invalid asset type.', 'danger')
            return redirect(url_for('dev.upload'))
        if not filename:
            flash('Filename is required.', 'danger')
            return redirect(url_for('dev.upload'))

        # Add extension if missing
        ext_map = {'img': '.png', 'css': '.css', 'js': '.js', 'html': '.html'}
        if '.' not in filename:
            filename += ext_map.get(asset_type, '')

        # Duplicate slug check for HTML pages
        if asset_type == 'html':
            slug = _slug(filename)
            existing = StaticAsset.query.filter_by(asset_type='html').all()
            if any(_slug(a.filename) == slug for a in existing):
                flash(f'An HTML page with slug "{slug}" already exists.', 'danger')
                return redirect(url_for('dev.upload'))

        asset = StaticAsset(
            uploader_id = current_user.id,
            asset_type  = asset_type,
            filename    = filename,
            title       = title or filename,
            description = description,
            content     = content,
            is_active   = True,
        )
        db.session.add(asset)
        db.session.commit()

        ActivityLog.log(current_user.id, 'dev_upload',
                        f'Uploaded {asset_type}: {filename}',
                        ip_address=request.remote_addr)

        flash(f'✅ {filename} uploaded successfully!', 'success')
        return redirect(url_for('dev.index'))

    asset_type = request.args.get('type', 'html')
    return render_template('developer/upload.html', default_type=asset_type)


# ─── Edit ────────────────────────────────────────────────────────────────────

@dev_bp.route('/edit/<int:asset_id>', methods=['GET', 'POST'])
@login_required
@dev_required
def edit(asset_id):
    asset = StaticAsset.query.get_or_404(asset_id)

    if request.method == 'POST':
        asset.title       = request.form.get('title', asset.title).strip()
        asset.description = request.form.get('description', '').strip()
        asset.content     = request.form.get('content', '')
        asset.is_active   = bool(request.form.get('is_active'))
        db.session.commit()

        ActivityLog.log(current_user.id, 'dev_edit',
                        f'Edited {asset.asset_type}: {asset.filename}',
                        ip_address=request.remote_addr)
        flash(f'✅ {asset.filename} updated.', 'success')
        return redirect(url_for('dev.index'))

    return render_template('developer/edit.html', asset=asset)


# ─── Delete ──────────────────────────────────────────────────────────────────

@dev_bp.route('/delete/<int:asset_id>', methods=['POST'])
@login_required
@dev_required
def delete(asset_id):
    asset = StaticAsset.query.get_or_404(asset_id)
    name = asset.filename
    db.session.delete(asset)
    db.session.commit()
    ActivityLog.log(current_user.id, 'dev_delete',
                    f'Deleted {name}', ip_address=request.remote_addr)
    flash(f'Deleted {name}.', 'success')
    return redirect(url_for('dev.index'))


# ─── Serve CSS/JS inline ─────────────────────────────────────────────────────

@dev_bp.route('/serve/css/<int:asset_id>')
def serve_css(asset_id):
    asset = StaticAsset.query.filter_by(id=asset_id, asset_type='css', is_active=True).first_or_404()
    return Response(asset.content or '', mimetype='text/css')


@dev_bp.route('/serve/js/<int:asset_id>')
def serve_js(asset_id):
    asset = StaticAsset.query.filter_by(id=asset_id, asset_type='js', is_active=True).first_or_404()
    return Response(asset.content or '', mimetype='application/javascript')


# ─── HTML Pages — each file gets its own live page ───────────────────────────

@dev_bp.route('/pages/')
@login_required
@dev_required
def pages_list():
    pages = StaticAsset.query.filter_by(asset_type='html', is_active=True)\
                             .order_by(StaticAsset.created_at.desc()).all()
    return render_template('developer/pages_list.html', pages=pages)


@dev_bp.route('/pages/<slug>')
def page_view(slug):
    """Public-facing HTML page rendered from DB content."""
    pages = StaticAsset.query.filter_by(asset_type='html', is_active=True).all()
    asset = next((a for a in pages if _slug(a.filename) == slug), None)
    if not asset:
        abort(404)
    return render_template('developer/page_view.html', asset=asset)


# ─── AJAX: list assets as JSON ───────────────────────────────────────────────

@dev_bp.route('/api/assets')
@login_required
@dev_required
def api_assets():
    asset_type = request.args.get('type', 'html')
    assets = StaticAsset.query.filter_by(asset_type=asset_type, is_active=True)\
                              .order_by(StaticAsset.created_at.desc()).all()
    return jsonify({'assets': [a.to_dict() for a in assets]})
