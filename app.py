from flask import Flask, render_template, request, redirect, url_for, flash, Response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Lead, FollowUp, Order, Group
from datetime import datetime, timedelta
import csv
import io
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-me')
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(basedir, 'data.db'))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    db.create_all()
    if not User.query.first():
        db.session.add(User(username='admin', password='123456', role='admin', group_name=None))
        for i in range(1, 9):
            group_name = f'销售{i}组'
            db.session.add(User(username=f'leader{i}', password='222', role='leader', group_name=group_name))
            db.session.add(User(username=f'sales{i}a', password='111', role='consultant', group_name=group_name))
            db.session.add(User(username=f'sales{i}b', password='111', role='consultant', group_name=group_name))
        db.session.commit()
    if not Group.query.first():
        for i in range(1, 9):
            db.session.add(Group(name=f'销售{i}组'))
        db.session.commit()

# ---------- 过期计算 ----------
def get_base_date(lead):
    last_fu = FollowUp.query.filter_by(lead_id=lead.id).order_by(FollowUp.created_at.desc()).first()
    if last_fu:
        return last_fu.created_at.date()
    if lead.assignment_date:
        return lead.assignment_date
    return lead.created_at.date() if lead.created_at else datetime.utcnow().date()

def is_lead_expired(lead):
    if lead.status == '已成交':
        return False
    deadline = get_base_date(lead) + timedelta(days=7)
    return datetime.utcnow().date() > deadline

# ---------- 登录 ----------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            login_user(user)
            next_url = request.args.get('next')
            if next_url:
                return redirect(next_url)
            return redirect(url_for('index'))
        else:
            flash('用户名或密码错误')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ---------- 修改密码 ----------
@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_pw = request.form['old_password']
        new_pw = request.form['new_password']
        if current_user.password != old_pw:
            flash('原密码错误')
        else:
            current_user.password = new_pw
            db.session.commit()
            flash('密码已修改，请重新登录')
            logout_user()
            return redirect(url_for('login'))
    return render_template('change_password.html')

# ---------- 首页 ----------
@app.route('/')
@login_required
def index():
    if current_user.role != 'admin':
        flash('只有管理员可以查看数据看板')
        return redirect(url_for('lead_list'))
    total_leads = Lead.query.count()
    leads_by_status = db.session.query(Lead.status, db.func.count(Lead.id)).group_by(Lead.status).all()
    total_orders = Order.query.count()
    total_amount = db.session.query(db.func.sum(Order.amount)).scalar() or 0
    orders_by_status = db.session.query(Order.status, db.func.count(Order.id)).group_by(Order.status).all()

    status_labels = [item[0] for item in leads_by_status]
    status_counts = [item[1] for item in leads_by_status]
    order_status_labels = [item[0] for item in orders_by_status]
    order_status_counts = [item[1] for item in orders_by_status]

    return render_template('index.html',
                           total_leads=total_leads,
                           total_orders=total_orders,
                           total_amount=total_amount,
                           status_labels=status_labels,
                           status_counts=status_counts,
                           order_status_labels=order_status_labels,
                           order_status_counts=order_status_counts)

# ---------- 客户列表 + 查询 + 导出 ----------
@app.route('/leads')
@login_required
def lead_list():
    if current_user.role == 'admin':
        query = Lead.query
    elif current_user.role == 'leader':
        query = Lead.query.filter_by(group=current_user.group_name)
    else:
        query = Lead.query.filter_by(sales_consultant=current_user.username)

    search_name = request.args.get('name', '').strip()
    search_consultant = request.args.get('sales_consultant', '').strip()
    search_group = request.args.get('group', '').strip()
    search_status = request.args.get('status', '').strip()
    search_category = request.args.get('customer_category', '').strip()
    expire_filter = request.args.get('expire_status', '').strip()

    if search_name:
        query = query.filter(Lead.name.contains(search_name))
    if search_status:
        query = query.filter(Lead.status == search_status)
    if search_category:
        query = query.filter(Lead.customer_category == search_category)
    if search_consultant and current_user.role in ['admin', 'leader']:
        query = query.filter(Lead.sales_consultant == search_consultant)
    if search_group and current_user.role == 'admin':
        query = query.filter(Lead.group == search_group)

    leads = query.order_by(Lead.created_at.desc()).all()

    # 导出权限
    if request.args.get('export') == '1':
        if current_user.role != 'admin':
            flash('无权限导出')
            return redirect(url_for('lead_list'))
        return export_leads_csv(leads)

    lead_data = []
    for lead in leads:
        expired = is_lead_expired(lead)
        if lead.status == '已成交':
            days_remain = None
        else:
            base = get_base_date(lead)
            days_remain = (base + timedelta(days=7) - datetime.utcnow().date()).days
        latest_fu = FollowUp.query.filter_by(lead_id=lead.id).order_by(FollowUp.created_at.desc()).first()
        lead_data.append({
            'lead': lead,
            'expired': expired,
            'days_remaining': days_remain,
            'latest_followup': latest_fu
        })

    if expire_filter == 'expired':
        lead_data = [item for item in lead_data if item['expired']]
    elif expire_filter == 'not_expired':
        lead_data = [item for item in lead_data if not item['expired']]

    expired_count = sum(1 for item in lead_data if item['expired'])

    all_consultants = User.query.filter_by(role='consultant').all()
    all_groups = Group.query.all()
    return render_template('leads.html',
                           lead_data=lead_data,
                           all_consultants=all_consultants,
                           all_groups=all_groups,
                           expired_count=expired_count)

def export_leads_csv(leads):
    si = io.StringIO()
    writer = csv.writer(si)
    headers = [
        '组', '销售顾问', '分线日期', '客户分类', '姓名', '电话', '是否加微信', '所属区域',
        '客户基本信息', '是否到厂', '离厂原因', '是否到期', '状态', '来源', '成交金额', '备注'
    ]
    writer.writerow(headers)
    for lead in leads:
        expired = is_lead_expired(lead)
        writer.writerow([
            lead.group or '',
            lead.sales_consultant or '',
            lead.assignment_date.strftime('%Y-%m-%d') if lead.assignment_date else '',
            lead.customer_category or '',
            lead.name,
            lead.phone,
            '是' if lead.wechat_added else '否',
            lead.region or '',
            lead.customer_info or '',
            '是' if lead.factory_visit else '否',
            lead.leave_reason or '',
            '是' if expired else '否',
            lead.status or '',
            lead.source or '',
            lead.deal_amount if lead.deal_amount else 0,
            lead.remark or ''
        ])
    output = si.getvalue()
    si.close()
    return Response(output, mimetype='text/csv',
                    headers={"Content-Disposition": "attachment;filename=客户数据.csv"})

# ---------- 导入 CSV（仅管理员） ----------
@app.route('/import_leads', methods=['POST'])
@login_required
def import_leads():
    if current_user.role != 'admin':
        flash('无权限导入')
        return redirect(url_for('lead_list'))
    if 'file' not in request.files:
        flash('请选择文件')
        return redirect(url_for('lead_list'))
    file = request.files['file']
    if file.filename == '':
        flash('未选择文件')
        return redirect(url_for('lead_list'))

    stream = io.StringIO(file.stream.read().decode("utf-8"))
    csv_reader = csv.reader(stream)
    next(csv_reader, None)
    count = 0
    for row in csv_reader:
        if len(row) < 15:   # 至少需要15列（0-14包含主要字段）
            continue
        # 管理员导入时，完全使用文件中的组和销售顾问
        group_name = row[0] if row[0] else ''
        consultant = row[1] if row[1] else ''

        lead = Lead(
            group=group_name,
            sales_consultant=consultant,
            assignment_date=datetime.strptime(row[2], '%Y-%m-%d') if row[2] else None,
            customer_category=row[3] if row[3] else '',
            name=row[4],
            phone=row[5] if row[5] else '',
            wechat_added=True if row[6] == '是' else False,
            region=row[7] if row[7] else '',
            customer_info=row[8] if row[8] else '',
            factory_visit=True if row[9] == '是' else False,
            leave_reason=row[10] if row[10] else '',
            # row[11] 是“是否到期”列，忽略不导入
            status=row[12] if len(row) > 12 and row[12] else '新线索',
            source=row[13] if len(row) > 13 and row[13] else '',
            deal_amount=float(row[14]) if len(row) > 14 and row[14] else 0.0,
            remark=row[15] if len(row) > 15 and row[15] else ''
        )
        db.session.add(lead)
        count += 1
    db.session.commit()
    flash(f'成功导入 {count} 条客户数据')
    return redirect(url_for('lead_list'))

# ---------- 新建客户 ----------
@app.route('/lead/new', methods=['GET', 'POST'])
@login_required
def lead_create():
    if request.method == 'POST':
        if current_user.role in ['consultant', 'leader']:
            group = current_user.group_name
            consultant = current_user.username
        else:
            group = request.form['group']
            consultant = request.form['sales_consultant']
        lead = Lead(
            group=group,
            sales_consultant=consultant,
            assignment_date=datetime.strptime(request.form['assignment_date'], '%Y-%m-%d') if request.form['assignment_date'] else None,
            customer_category=request.form['customer_category'],
            name=request.form['name'],
            phone=request.form['phone'],
            wechat_added='wechat_added' in request.form,
            region=request.form['region'],
            customer_info=request.form['customer_info'],
            factory_visit='factory_visit' in request.form,
            leave_reason=request.form.get('leave_reason', ''),
            status=request.form['status'],
            source=request.form['source'],
            deal_amount=float(request.form['deal_amount']) if request.form['deal_amount'] else 0.0,
            remark=request.form['remark']
        )
        db.session.add(lead)
        db.session.commit()
        return redirect(url_for('lead_list'))
    consultants = User.query.filter_by(role='consultant').all()
    groups = Group.query.all()
    return render_template('lead_form.html', lead=None, consultants=consultants, groups=groups)

# ---------- 编辑客户 ----------
@app.route('/lead/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def lead_edit(id):
    lead = Lead.query.get_or_404(id)
    if current_user.role == 'consultant' and lead.sales_consultant != current_user.username:
        flash('无权限编辑此客户')
        return redirect(url_for('lead_list'))
    if current_user.role == 'leader' and lead.sales_consultant != current_user.username:
        flash('组长只能编辑自己的客户')
        return redirect(url_for('lead_list'))
    if request.method == 'POST':
        if current_user.role == 'admin':
            lead.group = request.form['group']
            lead.sales_consultant = request.form['sales_consultant']
        else:
            lead.group = current_user.group_name if current_user.role != 'admin' else request.form['group']
            lead.sales_consultant = current_user.username if current_user.role != 'admin' else request.form['sales_consultant']
            lead.assignment_date = datetime.strptime(request.form['assignment_date'], '%Y-%m-%d') if request.form['assignment_date'] else None
            lead.customer_category = request.form['customer_category']
            lead.name = request.form['name']
            lead.phone = request.form['phone']
            lead.wechat_added = 'wechat_added' in request.form
            lead.region = request.form['region']
            lead.customer_info = request.form['customer_info']
            lead.factory_visit = 'factory_visit' in request.form
            lead.leave_reason = request.form.get('leave_reason', '')
            lead.status = request.form['status']
            lead.source = request.form['source']
            lead.deal_amount = float(request.form['deal_amount']) if request.form['deal_amount'] else 0.0
            lead.remark = request.form['remark']
        db.session.commit()
        return redirect(url_for('lead_list'))
    consultants = User.query.filter_by(role='consultant').all()
    groups = Group.query.all()
    return render_template('lead_form.html', lead=lead, consultants=consultants, groups=groups)

# ---------- 删除 ----------
@app.route('/lead/<int:id>/delete', methods=['POST'])
@login_required
def lead_delete(id):
    lead = Lead.query.get_or_404(id)
    if current_user.role != 'admin':
        flash('无权删除')
        return redirect(url_for('lead_list'))
    db.session.delete(lead)
    db.session.commit()
    return redirect(url_for('lead_list'))

# ---------- 跟进 ----------
@app.route('/lead/<int:lead_id>/followups')
@login_required
def follow_up_list(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    follow_ups = FollowUp.query.filter_by(lead_id=lead_id).order_by(FollowUp.created_at.desc()).all()
    return render_template('follow_ups.html', lead=lead, follow_ups=follow_ups)

@app.route('/lead/<int:lead_id>/followup/add', methods=['POST'])
@login_required
def follow_up_add(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    content = request.form['content']
    if content.strip():
        fu = FollowUp(lead_id=lead.id, content=content)
        db.session.add(fu)
        db.session.commit()
    return redirect(url_for('follow_up_list', lead_id=lead.id))

# ---------- 订单 ----------
@app.route('/orders')
@login_required
def order_list():
    if current_user.role == 'admin':
        orders = Order.query.order_by(Order.order_date.desc()).all()
    elif current_user.role == 'leader':
        orders = Order.query.join(Lead).filter(Lead.group == current_user.group_name).order_by(Order.order_date.desc()).all()
    else:
        orders = Order.query.join(Lead).filter(Lead.sales_consultant == current_user.username).order_by(Order.order_date.desc()).all()
    return render_template('orders.html', orders=orders)

@app.route('/order/new', methods=['GET', 'POST'])
@login_required
def order_create():
    if request.method == 'POST':
        order = Order(
            lead_id=request.form['lead_id'],
            product=request.form['product'],
            amount=float(request.form['amount']),
            status=request.form['status'],
            order_date=datetime.strptime(request.form['order_date'], '%Y-%m-%d') if request.form['order_date'] else datetime.utcnow()
        )
        db.session.add(order)
        db.session.commit()
        return redirect(url_for('order_list'))
    leads = Lead.query.all() if current_user.role == 'admin' else (
        Lead.query.filter_by(group=current_user.group_name).all() if current_user.role == 'leader' else Lead.query.filter_by(sales_consultant=current_user.username).all()
    )
    return render_template('order_form.html', order=None, leads=leads)

@app.route('/order/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def order_edit(id):
    order = Order.query.get_or_404(id)
    if request.method == 'POST':
        order.lead_id = request.form['lead_id']
        order.product = request.form['product']
        order.amount = float(request.form['amount'])
        order.status = request.form['status']
        order.order_date = datetime.strptime(request.form['order_date'], '%Y-%m-%d')
        db.session.commit()
        return redirect(url_for('order_list'))
    leads = Lead.query.all() if current_user.role == 'admin' else (
        Lead.query.filter_by(group=current_user.group_name).all() if current_user.role == 'leader' else Lead.query.filter_by(sales_consultant=current_user.username).all()
    )
    return render_template('order_form.html', order=order, leads=leads)

@app.route('/order/<int:id>/delete', methods=['POST'])
@login_required
def order_delete(id):
    order = Order.query.get_or_404(id)
    db.session.delete(order)
    db.session.commit()
    return redirect(url_for('order_list'))

# ---------- 内联编辑 ----------
@app.route('/lead/<int:id>/quick_update', methods=['POST'])
@login_required
def quick_update(id):
    lead = Lead.query.get_or_404(id)
    data = request.get_json()
    field = data.get('field')
    value = data.get('value')
    if not field or value is None:
        return {'error': '参数错误'}, 400

    if current_user.role == 'consultant':
        if lead.sales_consultant != current_user.username:
            return {'error': '无权限'}, 403
        if field not in ['status']:
            return {'error': '只能修改状态'}, 403
    elif current_user.role == 'leader':
        if lead.sales_consultant != current_user.username:
            return {'error': '只能修改自己的客户'}, 403
        if field not in ['status', 'sales_consultant']:
            return {'error': '无权限修改此字段'}, 403
    elif current_user.role == 'admin':
        if field not in ['group', 'sales_consultant']:
            return {'error': '管理员只能分配组和销售顾问'}, 400

    if field == 'status':
        lead.status = value
    elif field == 'sales_consultant':
        lead.sales_consultant = value
        consultant = User.query.filter_by(username=value, role='consultant').first()
        if consultant and consultant.group_name:
            lead.group = consultant.group_name
    elif field == 'group':
        lead.group = value

    db.session.commit()
    return {'success': True}

# ---------- 批量分配 ----------
@app.route('/admin/batch_assign', methods=['GET', 'POST'])
@login_required
def batch_assign():
    if current_user.role != 'admin':
        flash('无权限')
        return redirect(url_for('lead_list'))
    if request.method == 'POST':
        lead_ids = request.form.getlist('lead_ids')
        new_consultant = request.form.get('sales_consultant')
        new_group = request.form.get('group')
        if not lead_ids:
            flash('请至少选择一个客户')
            return redirect(url_for('batch_assign'))
        for lid in lead_ids:
            lead = Lead.query.get(int(lid))
            if lead:
                lead.sales_consultant = new_consultant
                lead.group = new_group
        db.session.commit()
        flash(f'已成功分配 {len(lead_ids)} 条客户')
        return redirect(url_for('lead_list'))
    leads = Lead.query.order_by(Lead.created_at.desc()).all()
    consultants = User.query.filter_by(role='consultant').all()
    groups = Group.query.all()
    return render_template('batch_assign.html', leads=leads, consultants=consultants, groups=groups)

# ---------- 用户管理 ----------
@app.route('/admin/users')
@login_required
def admin_users():
    if current_user.role != 'admin':
        flash('无权限')
        return redirect(url_for('lead_list'))
    users = User.query.all()
    return render_template('admin_users.html', users=users)

@app.route('/admin/user/new', methods=['GET', 'POST'])
@login_required
def admin_user_create():
    if current_user.role != 'admin':
        flash('无权限')
        return redirect(url_for('lead_list'))
    groups = Group.query.all()
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        role = request.form['role']
        group_name = request.form.get('group_name', '')
        if User.query.filter_by(username=username).first():
            flash('用户名已存在')
            return render_template('admin_user_form.html', user=None, groups=groups)
        user = User(username=username, password=password, role=role, group_name=group_name)
        db.session.add(user)
        db.session.commit()
        flash('用户添加成功')
        return redirect(url_for('admin_users'))
    return render_template('admin_user_form.html', user=None, groups=groups)

@app.route('/admin/user/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def admin_user_edit(id):
    if current_user.role != 'admin':
        flash('无权限')
        return redirect(url_for('lead_list'))
    user = User.query.get_or_404(id)
    groups = Group.query.all()
    if request.method == 'POST':
        user.username = request.form['username'].strip()
        if request.form['password'].strip():
            user.password = request.form['password'].strip()
        user.role = request.form['role']
        user.group_name = request.form.get('group_name', '')
        db.session.commit()
        flash('用户已更新')
        return redirect(url_for('admin_users'))
    return render_template('admin_user_form.html', user=user, groups=groups)

@app.route('/admin/user/<int:id>/delete', methods=['POST'])
@login_required
def admin_user_delete(id):
    if current_user.role != 'admin':
        flash('无权限')
        return redirect(url_for('lead_list'))
    user = User.query.get_or_404(id)
    db.session.delete(user)
    db.session.commit()
    flash('用户已删除')
    return redirect(url_for('admin_users'))

# ---------- 组管理 ----------
@app.route('/admin/groups')
@login_required
def admin_groups():
    if current_user.role != 'admin':
        flash('无权限')
        return redirect(url_for('lead_list'))
    groups = Group.query.all()
    return render_template('admin_groups.html', groups=groups)

@app.route('/admin/group/new', methods=['POST'])
@login_required
def admin_group_create():
    if current_user.role != 'admin':
        flash('无权限')
        return redirect(url_for('lead_list'))
    name = request.form['name'].strip()
    if name and not Group.query.filter_by(name=name).first():
        db.session.add(Group(name=name))
        db.session.commit()
        flash('组已添加')
    else:
        flash('组名不能为空或已存在')
    return redirect(url_for('admin_groups'))

@app.route('/admin/group/<int:id>/delete', methods=['POST'])
@login_required
def admin_group_delete(id):
    if current_user.role != 'admin':
        flash('无权限')
        return redirect(url_for('lead_list'))
    group = Group.query.get_or_404(id)
    db.session.delete(group)
    db.session.commit()
    flash('组已删除')
    return redirect(url_for('admin_groups'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)