"""
developer.py — Static Asset model for Developer role
"""
import os
from app import db
from datetime import datetime


class StaticAsset(db.Model):
    __tablename__ = 'static_assets'

    id          = db.Column(db.Integer, primary_key=True)
    uploader_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    asset_type  = db.Column(db.String(10), nullable=False)   # img | css | js | html
    filename    = db.Column(db.String(200), nullable=False)
    title       = db.Column(db.String(200))                  # html: page title / slug
    content     = db.Column(db.Text)                         # html/css/js inline content
    file_path   = db.Column(db.String(500))                  # server path for binary (img)
    description = db.Column(db.Text)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    uploader = db.relationship('User', backref='static_assets')

    @property
    def slug(self):
        """URL-safe slug from filename."""
        name = os.path.splitext(self.filename)[0]
        return name.lower().replace(' ', '-')

    def to_dict(self):
        return {
            'id':          self.id,
            'type':        self.asset_type,
            'filename':    self.filename,
            'title':       self.title,
            'slug':        self.slug,
            'description': self.description,
            'is_active':   self.is_active,
            'uploader':    self.uploader.username if self.uploader else '—',
            'created_at':  self.created_at.isoformat() if self.created_at else None,
        }
