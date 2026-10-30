# -*- coding: utf-8 -*-
"""RipperMods Web Site - Flask application (separate from bot)."""
import os, logging, secrets, re, random, base64, smtplib
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import (Flask, render_template, request, redirect, url_for, flash,
                   session, abort, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s %(levelname)s: %(message)s')
log = logging.getLogger('rippermods-site')

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=14)

DATABASE_URL = os.environ.get('DATABASE_URL')
CARD_NUMBER = os.getenv('CARD_NUMBER', '5022291544593963')
CARD_HOLDER = os.getenv('CARD_HOLDER', 'علیرضا عباس پور')
LINKS_FILE_PASSWORD = "t.me/rippermods"

ADMIN_USERS = {
    'dev team': {'username': 'dev team', 'password': os.getenv('ADMIN1_PASSWORD', 'Amir'),  'level': 'admin', 'display': 'Dev Team'},
    'Rippermw': {'username': 'Rippermw', 'password': os.getenv('ADMIN2_PASSWORD', 'Amir1'), 'level': 'super', 'display': 'Rippermw'},
}

@contextmanager
def db():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL is not set.')
    kwargs = dict(connect_timeout=8, cursor_factory=RealDictCursor)
    if 'sslmode' not in DATABASE_URL:
        kwargs['sslmode'] = 'require'
    conn = psycopg2.connect(DATABASE_URL, **kwargs)
    try:
        with conn:
            yield conn
    finally:
        conn.close()

_db_ready = False

def init_db():
    global _db_ready
    if _db_ready:
        return
    schema = [
        """CREATE TABLE IF NOT EXISTS web_users (
            id BIGSERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            email_verified BOOLEAN NOT NULL DEFAULT false,
            verify_token TEXT,
            telegram_id BIGINT UNIQUE,
            is_admin BOOLEAN NOT NULL DEFAULT false,
            is_super_admin BOOLEAN NOT NULL DEFAULT false,
            last_login TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS web_orders (
            id BIGSERIAL PRIMARY KEY,
            web_user_id BIGINT NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,
            product_id BIGINT NOT NULL REFERENCES products(id),
            product_name TEXT NOT NULL,
            amount NUMERIC(14,2) NOT NULL CHECK(amount >= 0),
            tracking_code TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','delivered')),
            receipt_path TEXT,
            reviewed_by TEXT,
            reviewed_at TIMESTAMPTZ,
            delivered_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    ]
    with db() as conn:
        with conn.cursor() as cur:
            for sql in schema:
                cur.execute(sql)
    _db_ready = True

def gen_tracking() -> str:
    return f"RM-{random.randint(10000, 99999)}"

def unique_tracking(cur) -> str:
    for _ in range(20):
        code = gen_tracking()
        cur.execute("SELECT 1 FROM web_orders WHERE tracking_code=%s", (code,))
        if not cur.fetchone():
            return code
    return f"RM-{int(datetime.now().timestamp()) % 90000 + 10000}"

EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email or ''))

def send_verification_email(email: str, token: str, base_url: str) -> bool:
    smtp_email = os.getenv('SMTP_EMAIL', '').strip()
    smtp_pass = os.getenv('SMTP_PASSWORD', '').strip()
    if not smtp_email or not smtp_pass:
        log.warning('SMTP not configured. Verification link: %s/verify/%s', base_url, token)
        return True
    try:
        link = f"{base_url}/verify/{token}"
        html = f"""<div dir="rtl" style="font-family:Tahoma,sans-serif;background:#0f0f1a;color:#e8e8f0;padding:30px;border-radius:12px">
        <h2 style="color:#00e5ff">RipperMods</h2>
        <p>برای فعال‌سازی حساب خود روی لینک زیر کلیک کنید:</p>
        <p><a href="{link}" style="color:#00e5ff;background:#1a1a2e;padding:12px 24px;border-radius:8px;text-decoration:none">تایید ایمیل</a></p>
        </div>"""
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'RipperMods - تایید ایمیل'
        msg['From'] = smtp_email
        msg['To'] = email
        msg.attach(MIMEText(html, 'html', 'utf-8'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15) as s:
            s.login(smtp_email, smtp_pass)
            s.send_message(msg)
        return True
    except Exception as e:
        log.exception('Failed to send verification email: %s', e)
        return False

def current_web_user():
    uid = session.get('web_user_id')
    if not uid:
        return None
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM web_users WHERE id=%s", (uid,))
            return cur.fetchone()

def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not session.get('web_user_id'):
            return redirect(url_for('login'))
        return view(*a, **kw)
    return wrapped

def admin_required(min_level='admin'):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            lvl = session.get('admin_level')
            if lvl not in ('admin', 'super'):
                return redirect(url_for('admin_login'))
            if min_level == 'super' and lvl != 'super':
                flash('این بخش فقط برای ادمین اصلی است.', 'error')
                return redirect(url_for('admin_panel'))
            return view(*a, **kw)
        return wrapped
    return deco

@app.context_processor
def inject_globals():
    return dict(current_user=current_web_user(), admin_username=session.get('admin_username'), admin_level=session.get('admin_level'), site_name='RipperMods')

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        display = (request.form.get('display_name') or '').strip()
        if not valid_email(email):
            flash('ایمیل معتبر نیست.', 'error')
            return render_template('register.html', email=email, display=display)
        if len(password) < 6:
            flash('رمز عبور باید حداقل ۶ کاراکتر باشد.', 'error')
            return render_template('register.html', email=email, display=display)
        try:
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM web_users WHERE email=%s", (email,))
                    if cur.fetchone():
                        flash('این ایمیل قبلاً ثبت شده است.', 'error')
                        return render_template('register.html', email=email, display=display)
                    token = secrets.token_urlsafe(32)
                    cur.execute("INSERT INTO web_users(email,password_hash,display_name,verify_token) VALUES(%s,%s,%s,%s) RETURNING id", (email, generate_password_hash(password), display or email.split('@')[0], token))
            base = request.host_url.rstrip('/')
            send_verification_email(email, token, base)
            return render_template('verify_sent.html', email=email)
        except Exception:
            log.exception('register failed')
            flash('خطا در ثبت‌نام. لطفاً دوباره تلاش کنید.', 'error')
    return render_template('register.html')

@app.route('/verify/<token>')
def verify(token):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE web_users SET email_verified=true, verify_token=NULL WHERE verify_token=%s RETURNING id", (token,))
            row = cur.fetchone()
    if row:
        session['web_user_id'] = row['id']
        flash('ایمیل شما با موفقیت تایید شد. خوش آمدید!', 'success')
        return redirect(url_for('dashboard'))
    flash('لینک تایید نامعتبر یا منقضی شده است.', 'error')
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM web_users WHERE email=%s", (email,))
                u = cur.fetchone()
                if u and check_password_hash(u['password_hash'], password):
                    if not u['email_verified']:
                        flash('ابتدا ایمیل خود را تایید کنید.', 'error')
                        return render_template('login.html', email=email)
                    cur.execute("UPDATE web_users SET last_login=now() WHERE id=%s", (u['id'],))
                    session['web_user_id'] = u['id']
                    return redirect(url_for('dashboard'))
        flash('ایمیل یا رمز عبور اشتباه است.', 'error')
        return render_template('login.html', email=email)
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('web_user_id', None)
    flash('با موفقیت خارج شدید.', 'success')
    return redirect(url_for('home'))

@app.route('/shop')
def shop():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, description, price, active FROM products ORDER BY id")
            products = cur.fetchall()
    return render_template('shop.html', products=products)

@app.route('/shop/<int:pid>')
def product_detail(pid):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE id=%s AND active=true", (pid,))
            p = cur.fetchone()
    if not p:
        abort(404)
    return render_template('product_detail.html', product=p, card_number=CARD_NUMBER, card_holder=CARD_HOLDER)

@app.route('/checkout/<int:pid>', methods=['GET', 'POST'])
@login_required
def checkout(pid):
    uid = session['web_user_id']
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE id=%s AND active=true", (pid,))
            p = cur.fetchone()
            if not p:
                abort(404)
            cur.execute("SELECT 1 FROM web_orders WHERE web_user_id=%s AND product_id=%s AND status IN ('approved','delivered')", (uid, pid))
            if cur.fetchone():
                flash('شما قبلاً این محصول را خریداری کرده‌اید.', 'error')
                return redirect(url_for('dashboard'))
    if request.method == 'POST':
        receipt = request.files.get('receipt')
        if not receipt or not receipt.filename:
            flash('لطفاً تصویر رسید را آپلود کنید.', 'error')
            return render_template('checkout.html', product=p, card_number=CARD_NUMBER, card_holder=CARD_HOLDER)
        data = receipt.read()
        if len(data) > 5 * 1024 * 1024:
            flash('حجم تصویر نباید بیشتر از ۵ مگابایت باشد.', 'error')
            return render_template('checkout.html', product=p, card_number=CARD_NUMBER, card_holder=CARD_HOLDER)
        b64 = base64.b64encode(data).decode('ascii')
        with db() as conn:
            with conn.cursor() as cur:
                tc = unique_tracking(cur)
                cur.execute("INSERT INTO web_orders(web_user_id,product_id,product_name,amount,tracking_code,receipt_path) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id", (uid, pid, p['name'], p['price'], tc, b64))
                oid = cur.fetchone()['id']
        flash(f'سفارش شما با کد پیگیری {tc} ثبت شد. پس از بررسی ادمین، نتیجه به شما اطلاع داده می‌شود.', 'success')
        return redirect(url_for('order_detail', oid=oid))
    return render_template('checkout.html', product=p, card_number=CARD_NUMBER, card_holder=CARD_HOLDER)

@app.route('/dashboard')
@login_required
def dashboard():
    uid = session['web_user_id']
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM web_users WHERE id=%s", (uid,))
            u = cur.fetchone()
            cur.execute("SELECT * FROM web_orders WHERE web_user_id=%s ORDER BY created_at DESC", (uid,))
            orders = cur.fetchall()
    return render_template('dashboard.html', user=u, orders=orders)

@app.route('/order/<int:oid>')
@login_required
def order_detail(oid):
    uid = session['web_user_id']
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM web_orders WHERE id=%s AND web_user_id=%s", (oid, uid))
            o = cur.fetchone()
            if not o:
                abort(404)
            links = []
            if o['status'] == 'delivered':
                cur.execute("SELECT pl.link FROM product_links pl WHERE pl.product_id=%s ORDER BY pl.id", (o['product_id'],))
                links = [r['link'] for r in cur.fetchall()]
    return render_template('order_detail.html', order=o, links=links, links_password=LINKS_FILE_PASSWORD)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        rec = ADMIN_USERS.get(username)
        if rec and rec['password'] == password:
            session['admin_username'] = username
            session['admin_level'] = rec['level']
            flash(f"خوش آمدید {rec['display']}", 'success')
            return redirect(url_for('admin_panel'))
        flash('نام کاربری یا رمز اشتباه است.', 'error')
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_username', None)
    session.pop('admin_level', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required('admin')
def admin_panel():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM web_orders WHERE status='pending' ORDER BY created_at DESC")
            pending = cur.fetchall()
            cur.execute("SELECT * FROM web_orders WHERE status IN ('approved','delivered','rejected') ORDER BY reviewed_at DESC NULLS LAST LIMIT 30")
            recent = cur.fetchall()
            cur.execute("SELECT count(*) n FROM web_users")
            users_n = cur.fetchone()['n']
    return render_template('admin/panel.html', pending=pending, recent=recent, users_n=users_n)

@app.route('/admin/review/<int:oid>/<action>', methods=['POST'])
@admin_required('super')
def admin_review(oid, action):
    if action not in ('approve', 'reject'):
        abort(400)
    new_status = 'approved' if action == 'approve' else 'rejected'
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE web_orders SET status=%s, reviewed_by=%s, reviewed_at=now() WHERE id=%s AND status='pending' RETURNING id", (new_status, session.get('admin_username'), oid))
            if not cur.fetchone():
                flash('این سفارش قبلاً بررسی شده است.', 'error')
                return redirect(url_for('admin_panel'))
            if new_status == 'approved':
                cur.execute("UPDATE web_orders SET status='delivered', delivered_at=now() WHERE id=%s", (oid,))
    flash('سفارش بررسی شد.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/receipt/<int:oid>')
@admin_required('admin')
def admin_receipt(oid):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT receipt_path FROM web_orders WHERE id=%s", (oid,))
            r = cur.fetchone()
    if not r or not r['receipt_path']:
        abort(404)
    try:
        data = base64.b64decode(r['receipt_path'])
    except Exception:
        abort(500)
    return data, 200, {'Content-Type': 'image/jpeg'}

@app.route('/admin/super')
@admin_required('super')
def admin_super():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, description, price, active FROM products ORDER BY id")
            products = cur.fetchall()
            cur.execute("SELECT * FROM web_users ORDER BY created_at DESC LIMIT 50")
            users = cur.fetchall()
    return render_template('admin/super.html', products=products, users=users)

@app.route('/admin/super/price/<int:pid>', methods=['POST'])
@admin_required('super')
def admin_update_price(pid):
    try:
        price = Decimal(request.form.get('price', '0'))
        if price < 0:
            raise InvalidOperation()
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE products SET price=%s WHERE id=%s", (price, pid))
        flash('قیمت به‌روزرسانی شد.', 'success')
    except Exception:
        flash('قیمت معتبر نیست.', 'error')
    return redirect(url_for('admin_super'))

@app.route('/admin/super/links/<int:pid>', methods=['POST'])
@admin_required('super')
def admin_update_links(pid):
    links_text = request.form.get('links', '')
    links = [l.strip() for l in links_text.splitlines() if l.strip()]
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM product_links WHERE product_id=%s", (pid,))
            for l in links:
                cur.execute("INSERT INTO product_links(product_id, link) VALUES(%s,%s)", (pid, l))
    flash(f'{len(links)} لینک برای این محصول ثبت شد.', 'success')
    return redirect(url_for('admin_super'))

@app.route('/admin/super/links/<int:pid>/view')
@admin_required('super')
def admin_view_links(pid):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT link FROM product_links WHERE product_id=%s ORDER BY id", (pid,))
            links = [r['link'] for r in cur.fetchall()]
    return jsonify(links=links)

@app.get('/health')
def health():
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
        return jsonify(status='ok', database='connected'), 200
    except Exception:
        return jsonify(status='degraded', database='unavailable'), 503

try:
    init_db()
except Exception:
    log.exception('init_db failed on import')

if __name__ == '__main__':
    port = int(os.getenv('PORT', '10000'))
    app.run(host='0.0.0.0', port=port, use_reloader=False)
