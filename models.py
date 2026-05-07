from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)      # admin / leader / consultant
    group_name = db.Column(db.String(50))

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group = db.Column(db.String(50))
    sales_consultant = db.Column(db.String(50))
    assignment_date = db.Column(db.Date)
    customer_category = db.Column(db.String(50))
    name = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20), nullable=False)        # 电话必填
    wechat_added = db.Column(db.Boolean, default=False)
    region = db.Column(db.String(50))
    customer_info = db.Column(db.Text)
    factory_visit = db.Column(db.Boolean, default=False)
    leave_reason = db.Column(db.Text)
    status = db.Column(db.String(20), default='新线索')
    source = db.Column(db.String(50))
    deal_amount = db.Column(db.Float, default=0.0)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    follow_ups = db.relationship('FollowUp', backref='lead', lazy=True, cascade='all, delete-orphan')
    orders = db.relationship('Order', backref='lead', lazy=True, cascade='all, delete-orphan')

class FollowUp(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=False)
    product = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(20), default='待确认')
    order_date = db.Column(db.DateTime, default=datetime.utcnow)