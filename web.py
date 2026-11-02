# web.py
import os
import secrets
import random
import base64
from datetime import datetime
from decimal import Decimal
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
import psycopg2
from psycopg2.extras import RealDictCursor
import requests

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'rippermods-super-secret-key')

DATABASE_URL = os.environ.get('DATABASE_URL')
ADMIN_USER = os.environ.get('ADMIN1_USER', 'Rippermw')
ADMIN_PASS = os.environ.get('ADMIN1_PASSWORD', '123456')

# آدرس سرویس ربات و کلید مخفی
BOT_API_URL = os.environ.get('BOT_API_URL', 'https://your-bot-service.onrender.com')
SITE_API_KEY = os.environ.get('SITE_API_KEY', 'my-super-secret-api-key')

REDOWNLOAD_PRICE = Decimal('150000')
BASE_LINK = "https://dl2.megadl.click/PS4-Hacked/GTAV/CUSA00419/BASE/"

CARD_NUMBER = os.environ.get('CARD_NUMBER', '5022291544593963')
CARD_HOLDER = os.environ.get('CARD_HOLDER', 'ریبعی نژاد')

# --- کد تست برای ورود ادمین ---
TEST_ADMIN_PHONE = "09120000000" # در ورود ادمین استفاده می‌شود

def get_db():
    kwargs = dict(connect_timeout=8, cursor_factory=RealDictCursor)
    if 'sslmode' not in DATABASE_URL:
        kwargs['sslmode'] = 'require'
    return psycopg2.connect(DATABASE_URL, **kwargs)

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            # جدول عکس‌های محصولات
            cur.execute("""CREATE TABLE IF NOT EXISTS product_images (
                id BIGSERIAL PRIMARY KEY,
                product_id BIGINT REFERENCES products(id) ON DELETE CASCADE,
                image_url TEXT NOT NULL,
                is_cover BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT now()
            )""")
            # جدول تیکت‌های سایت
            cur.execute("""CREATE TABLE IF NOT EXISTS site_tickets (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                message TEXT NOT NULL,
                reply TEXT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMPTZ DEFAULT now(),
                replied_at TIMESTAMPTZ
            )""")
            # جدول کاربران سایت
            cur.execute("""CREATE TABLE IF NOT EXISTS site_users (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL UNIQUE,
                username TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )""")
            # اضافه کردن ستون عکس رسید به جدول سفارش‌ها
            cur.execute("""ALTER TABLE orders ADD COLUMN IF NOT EXISTS receipt_image_base64 TEXT""")
            
            # اضافه کردن کاور پیش‌فرض برای محصولات (اگر خالی بود)
            cur.execute("SELECT id, name FROM products")
            prods = cur.fetchall()
            for p in prods:
                cur.execute("SELECT 1 FROM product_images WHERE product_id=%s LIMIT 1", (p['id'],))
                if not cur.fetchone():
                    if 'سوپر' in p['name'] or 'super' in p['name'].lower():
                        cur.execute("INSERT INTO product_images (product_id, image_url, is_cover) VALUES (%s, %s, true)", 
                                    (p['id'], 'https://s31.uupload.ir/files/rippermodsgtavv100/Amir/4/./IMG_20261101_145958_359.jpg'))
                    elif 'ایرانیزه' in p['name']:
                        cur.execute("INSERT INTO product_images (product_id, image_url, is_cover) VALUES (%s, %s, true)", 
                                    (p['id'], 'https://s50.uupload.ir/files/rippermodsgtavv100/Amir/3/IMG_20261030_235715_878.jpg'))
    print("DB Initialized.")

@app.route('/')
def index():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE active=true")
            products = cur.fetchall()
            for p in products:
                cur.execute("SELECT image_url FROM product_images WHERE product_id=%s AND is_cover=true LIMIT 1", (p['id'],))
                cover = cur.fetchone()
                p['cover_url'] = cover['image_url'] if cover else ''
    return render_template('index.html', products=products, base_link=BASE_LINK)

@app.route('/product/<int:pid>')
def product_detail(pid):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE id=%s", (pid,))
            product = cur.fetchone()
            if not product: return "محصول یافت نشد", 404
            
            cur.execute("SELECT * FROM product_images WHERE product_id=%s ORDER BY is_cover DESC, id ASC", (pid,))
            images = cur.fetchall()
            
            has_bought = False
            if 'site_user_id' in session:
                cur.execute("SELECT 1 FROM orders WHERE user_id=(SELECT telegram_id FROM site_users WHERE id=%s) AND product_id=%s AND status IN ('approved','delivered')", (session['site_user_id'], pid))
                has_bought = cur.fetchone() is not None
            
    video_url = None
    if 'سوپر' in product['name'] or 'super' in product['name'].lower():
        video_url = "https://s21.uupload.ir/files/rippermodsgtavv100/Amir/4/VID_20261101_150016_007.mp4?play"
        
    return render_template('product.html', product=product, images=images, base_link=BASE_LINK, video_url=video_url, has_bought=has_bought)

# --- ثبت‌نام ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        phone = request.form.get('phone', '').strip()
        tg_id = request.form.get('telegram_id', '').strip().replace('@', '')
        
        if not full_name or not phone or not tg_id:
            flash('لطفاً همه فیلدها را پر کنید.', 'error')
            return redirect(url_for('register'))
            
        try:
            headers = {'X-API-Key': SITE_API_KEY}
            payload = {'telegram_id': int(tg_id)}
            res = requests.post(f"{BOT_API_URL}/api/send_login_code", json=payload, headers=headers, timeout=10)
            data = res.json()
            
            if data.get('status') == 'ok':
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO site_users (telegram_id, full_name, phone, username) VALUES (%s, %s, %s, %s) ON CONFLICT (telegram_id) DO UPDATE SET full_name=EXCLUDED.full_name, phone=EXCLUDED.phone RETURNING id", (int(tg_id), full_name, phone, tg_id))
                        uid = cur.fetchone()['id']
                        session['temp_user_id'] = uid
                flash('کد ورود به تلگرام شما ارسال شد.', 'success')
                return redirect(url_for('verify_code'))
            elif data.get('status') == 'not_found':
                flash('این آیدی تلگرام در ربات ثبت‌نام نکرده است. لطفاً اول ربات را استارت کنید.', 'error')
                return redirect(url_for('register'))
            else:
                flash('خطا در ارسال پیام. لطفاً بعداً تلاش کنید.', 'error')
                return redirect(url_for('register'))
        except Exception:
            flash('ارتباط با ربات برقرار نشد.', 'error')
            return redirect(url_for('register'))

    return render_template('register.html')

# --- ورود ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        tg_id = request.form.get('telegram_id', '').strip().replace('@', '')
        
        # چک کد تست ادمین
        if tg_id == TEST_ADMIN_PHONE:
            session['is_admin'] = True
            return redirect(url_for('admin_panel'))

        try:
            headers = {'X-API-Key': SITE_API_KEY}
            payload = {'telegram_id': int(tg_id)}
            res = requests.post(f"{BOT_API_URL}/api/send_login_code", json=payload, headers=headers, timeout=10)
            data = res.json()
            
            if data.get('status') == 'ok':
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM site_users WHERE telegram_id=%s", (int(tg_id),))
                        row = cur.fetchone()
                        if not row:
                            cur.execute("SELECT reg_name, phone FROM users WHERE telegram_id=%s", (int(tg_id),))
                            u = cur.fetchone()
                            if u:
                                cur.execute("INSERT INTO site_users (telegram_id, full_name, phone, username) VALUES (%s, %s, %s, %s) RETURNING id", (int(tg_id), u['reg_name'], u['phone'], str(tg_id)))
                                row = cur.fetchone()
                            else:
                                flash('اطلاعات شما در ربات یافت نشد.', 'error')
                                return redirect(url_for('login'))
                                
                        session['temp_user_id'] = row['id']
                flash('کد ورود به تلگرام شما ارسال شد.', 'success')
                return redirect(url_for('verify_code'))
            elif data.get('status') == 'not_found':
                flash('این آیدی در ربات ثبت‌نام نکرده است. لطفاً اول ربات را استارت کنید.', 'error')
                return redirect(url_for('login'))
            else:
                flash('خطا در ارسال پیام.', 'error')
                return redirect(url_for('login'))
        except Exception:
            flash('ارتباط با ربات برقرار نشد.', 'error')
            return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/verify_code', methods=['GET', 'POST'])
def verify_code():
    if 'temp_user_id' not in session:
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM link_codes WHERE code=%s AND used=false AND expires_at > now()", (code,))
                link = cur.fetchone()
                
                if link:
                    cur.execute("SELECT telegram_id FROM site_users WHERE id=%s", (session['temp_user_id'],))
                    u = cur.fetchone()
                    
                    if link['telegram_id'] == u['telegram_id']:
                        cur.execute("UPDATE link_codes SET used=true WHERE id=%s", (link['id'],))
                        session['site_user_id'] = session.pop('temp_user_id')
                        return redirect(url_for('dashboard'))
                
                flash('کد نامعتبر یا منقضی شده است.', 'error')
                return redirect(url_for('verify_code'))
                
    return render_template('verify_code.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    if 'site_user_id' not in session: return redirect(url_for('login'))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM site_users WHERE id=%s", (session['site_user_id'],))
            user = cur.fetchone()
            
            cur.execute("SELECT * FROM orders WHERE user_id=%s ORDER BY created_at DESC", (user['telegram_id'],))
            orders = cur.fetchall()
            
            now = datetime.now()
            for o in orders:
                o['is_expired'] = (o.get('link_expires_at') and o['link_expires_at'].replace(tzinfo=None) < now)
            
            cur.execute("SELECT * FROM site_tickets WHERE user_id=%s ORDER BY created_at DESC", (session['site_user_id'],))
            tickets = cur.fetchall()
    return render_template('dashboard.html', user=user, orders=orders, tickets=tickets, price=REDOWNLOAD_PRICE)

@app.route('/ticket/new', methods=['POST'])
def new_ticket():
    if 'site_user_id' not in session: return redirect(url_for('login'))
    message = request.form.get('message', '').strip()
    if message:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO site_tickets (user_id, message) VALUES (%s, %s)", (session['site_user_id'], message))
        flash('تیکت شما ثبت شد.', 'success')
    return redirect(url_for('dashboard'))

# --- فرآیند خرید و پرداخت ---
@app.route('/buy/<int:pid>', methods=['POST'])
def buy_product(pid):
    if 'site_user_id' not in session:
        return redirect(url_for('login'))
        
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM site_users WHERE id=%s", (session['site_user_id'],))
            user = cur.fetchone()
            tg_id = user['telegram_id']
            
            # چک کن کاربر قبلا این محصول رو نخریده باشه
            cur.execute("SELECT 1 FROM orders WHERE user_id=%s AND product_id=%s AND status IN ('approved','delivered')", (tg_id, pid))
            if cur.fetchone():
                flash('شما قبلاً این محصول را خریداری کرده‌اید.', 'error')
                return redirect(url_for('product_detail', pid=pid))
            
            cur.execute("SELECT * FROM products WHERE id=%s", (pid,))
            product = cur.fetchone()
            
            # ساخت کد پیگیری و ثبت سفارش اولیه
            tc = f"RM-{random.randint(10000, 99999)}"
            amount = product['price']
            
            cur.execute(
                "INSERT INTO orders(user_id, product_id, product_name, amount, payment_method, status, tracking_code) "
                "VALUES(%s, %s, %s, %s, 'card', 'pending', %s) RETURNING id",
                (tg_id, pid, product['name'], amount, tc)
            )
            order_id = cur.fetchone()['id']
            
    return redirect(url_for('payment_page', order_id=order_id))

@app.route('/payment/<int:order_id>')
def payment_page(order_id):
    if 'site_user_id' not in session:
        return redirect(url_for('login'))
    
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM orders WHERE id=%s AND user_id=(SELECT telegram_id FROM site_users WHERE id=%s)", (order_id, session['site_user_id']))
            order = cur.fetchone()
            if not order:
                return redirect(url_for('dashboard'))
                
    return render_template('payment.html', order_id=order['id'], tracking_code=order['tracking_code'], amount=order['amount'], card=CARD_NUMBER, holder=CARD_HOLDER, product_name=order['product_name'])

@app.route('/upload_receipt/<int:order_id>', methods=['POST'])
def upload_receipt(order_id):
    if 'site_user_id' not in session:
        return redirect(url_for('login'))
        
    if 'receipt' not in request.files:
        flash('لطفاً یک عکس انتخاب کنید.', 'error')
        return redirect(url_for('payment_page', order_id=order_id))
        
    file = request.files['receipt']
    if file.filename == '':
        flash('فایلی انتخاب نشده است.', 'error')
        return redirect(url_for('payment_page', order_id=order_id))

    # تبدیل عکس به Base64 برای ذخیره در دیتابیس
    try:
        img_data = file.read()
        if len(img_data) > 5 * 1024 * 1024: # حداکثر ۵ مگابایت
            flash('حجم عکس نباید بیشتر از ۵ مگابایت باشد.', 'error')
            return redirect(url_for('payment_page', order_id=order_id))
            
        b64_string = base64.b64encode(img_data).decode('utf-8')
        
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE orders SET receipt_image_base64=%s WHERE id=%s AND status='pending'", (b64_string, order_id))
        
        flash('رسید شما با موفقیت دریافت شد و در انتظار تایید ادمین است.', 'success')
        return redirect(url_for('dashboard'))
    except Exception as e:
        print(f"Upload Error: {e}")
        flash('خطا در ذخیره عکس. لطفاً دوباره تلاش کنید.', 'error')
        return redirect(url_for('payment_page', order_id=order_id))

# --- پنل ادمین ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'): return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('username') == ADMIN_USER and request.form.get('password') == ADMIN_PASS:
            session['is_admin'] = True
            return redirect(url_for('admin_panel'))
        flash('نام کاربری یا رمز عبور اشتباه است.', 'error')
    return render_template('admin_login.html')

@app.route('/admin/panel')
@admin_required
def admin_panel():
    with get_db() as conn:
        with conn.cursor() as cur:
            # گرفتن سفارش‌ها همراه با اطلاعات کاربر
            cur.execute("""
                SELECT o.*, u.full_name, u.phone 
                FROM orders o 
                LEFT JOIN site_users u ON o.user_id = u.telegram_id 
                ORDER BY o.created_at DESC LIMIT 50
            """)
            orders = cur.fetchall()
            
            cur.execute("SELECT * FROM site_tickets WHERE status='open' ORDER BY created_at DESC")
            tickets = cur.fetchall()
            cur.execute("SELECT * FROM products")
            products = cur.fetchall()
            for p in products:
                cur.execute("SELECT * FROM product_images WHERE product_id=%s ORDER BY is_cover DESC", (p['id'],))
                p['images'] = cur.fetchall()
    return render_template('admin.html', orders=orders, tickets=tickets, products=products)

@app.route('/admin/order/<int:oid>/<action>', methods=['POST'])
@admin_required
def admin_order_action(oid, action):
    if action not in ['approve', 'reject']: return "خطا", 400
    new_status = 'approved' if action == 'approve' else 'rejected'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE orders SET status=%s, updated_at=now(), reviewed_at=now() WHERE id=%s", (new_status, oid))
    flash('وضعیت سفارش تغییر کرد.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/ticket/<int:tid>/reply', methods=['POST'])
@admin_required
def admin_reply_ticket(tid):
    reply = request.form.get('reply', '').strip()
    if reply:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE site_tickets SET reply=%s, status='closed', replied_at=now() WHERE id=%s", (reply, tid))
        flash('پاسخ تیکت ثبت شد.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/product/<int:pid>/update', methods=['POST'])
@admin_required
def admin_update_product(pid):
    new_price = request.form.get('price')
    new_link = request.form.get('link')
    new_cover = request.form.get('cover')
    new_gallery = request.form.get('gallery')
    
    with get_db() as conn:
        with conn.cursor() as cur:
            if new_price:
                cur.execute("UPDATE products SET price=%s WHERE id=%s", (Decimal(new_price), pid))
            if new_link:
                cur.execute("SELECT id FROM product_links WHERE product_id=%s LIMIT 1", (pid,))
                row = cur.fetchone()
                if row:
                    cur.execute("UPDATE product_links SET link=%s WHERE id=%s", (new_link, row['id']))
                else:
                    cur.execute("INSERT INTO product_links (product_id, link) VALUES (%s, %s)", (pid, new_link))
            if new_cover:
                cur.execute("UPDATE product_images SET is_cover=false WHERE product_id=%s", (pid,))
                cur.execute("INSERT INTO product_images (product_id, image_url, is_cover) VALUES (%s, %s, true)", (pid, new_cover))
            if new_gallery:
                cur.execute("DELETE FROM product_images WHERE product_id=%s AND is_cover=false", (pid,))
                links = [x.strip() for x in new_gallery.split(',') if x.strip()]
                for l in links:
                    cur.execute("INSERT INTO product_images (product_id, image_url) VALUES (%s, %s)", (pid, l))
    flash('محصول با موفقیت آپدیت شد.', 'success')
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)