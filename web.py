# web.py
import os
import secrets
import random
import base64
import string
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
import resend

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'rippermods-super-secret-key')

DATABASE_URL = os.environ.get('DATABASE_URL')
ADMIN_USER = os.environ.get('ADMIN1_USER', 'Rippermw')
ADMIN_PASS = os.environ.get('ADMIN1_PASSWORD', '123456')

# تنظیم API Key برای Resend
resend.api_key = os.environ.get('RESEND_API_KEY', 're_YOUR_API_KEY_HERE')

REDOWNLOAD_PRICE = Decimal('150000')
BASE_LINK = "https://dl2.megadl.click/PS4-Hacked/GTAV/CUSA00419/BASE/"

CARD_NUMBER = os.environ.get('CARD_NUMBER', '5022291544593963')
CARD_HOLDER = os.environ.get('CARD_HOLDER', 'ریبعی نژاد')

# --- کد تست برای ورود ادمین ---
TEST_ADMIN_EMAIL = "admin@rippermods.ir"

def get_db():
    kwargs = dict(connect_timeout=8, cursor_factory=RealDictCursor)
    if 'sslmode' not in DATABASE_URL:
        kwargs['sslmode'] = 'require'
    return psycopg2.connect(DATABASE_URL, **kwargs)

def init_db():
    print("Initializing DB...")
    with get_db() as conn:
        with conn.cursor() as cur:
            try:
                # ۱. ساخت جدول کاربران سایت اگر وجود نداشته باشد
                cur.execute("""CREATE TABLE IF NOT EXISTS site_users (
                    id BIGSERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    full_name TEXT NOT NULL,
                    phone TEXT NOT NULL UNIQUE,
                    telegram_id BIGINT UNIQUE,
                    username TEXT,
                    created_at TIMESTAMPTZ DEFAULT now()
                )""")
                cur.execute("ALTER TABLE site_users ADD COLUMN IF NOT EXISTS email TEXT UNIQUE")

                # ۲. حذف کلیدهای خارجی قدیمی (برای اینکه بتونیم رکوردها رو پاک کنیم)
                cur.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_user_id_fkey")
                cur.execute("ALTER TABLE link_codes DROP CONSTRAINT IF EXISTS link_codes_telegram_id_fkey")
                cur.execute("ALTER TABLE coupon_uses DROP CONSTRAINT IF EXISTS coupon_uses_order_id_fkey")

                # ۳. پاک کردن رکوردهای coupon_uses که به سفارش‌های نامعتبر وصلن
                cur.execute("""
                    DELETE FROM coupon_uses 
                    WHERE order_id NOT IN (SELECT id FROM orders WHERE user_id IN (SELECT id FROM site_users))
                """)

                # ۴. پاک کردن رکوردهای orders و link_codes که کاربرشون توی site_users نیست
                cur.execute("DELETE FROM orders WHERE user_id NOT IN (SELECT id FROM site_users)")
                cur.execute("DELETE FROM link_codes WHERE telegram_id NOT IN (SELECT id FROM site_users)")

                # ۵. اضافه کردن مجدد کلیدهای خارجی، ولی اینبار به site_users
                cur.execute("""
                    ALTER TABLE orders 
                    ADD CONSTRAINT orders_user_id_fkey 
                    FOREIGN KEY (user_id) REFERENCES site_users(id) ON DELETE CASCADE
                """)
                cur.execute("""
                    ALTER TABLE link_codes 
                    ADD CONSTRAINT link_codes_telegram_id_fkey 
                    FOREIGN KEY (telegram_id) REFERENCES site_users(id) ON DELETE CASCADE
                """)

                # ۶. اضافه کردن ستون‌های جدید
                cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS short_desc TEXT")
                cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS full_desc TEXT")
                cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS video_url TEXT")
                cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS receipt_image_base64 TEXT")

                # ۷. ساخت جداول جانبی
                cur.execute("""CREATE TABLE IF NOT EXISTS product_images (
                    id BIGSERIAL PRIMARY KEY,
                    product_id BIGINT REFERENCES products(id) ON DELETE CASCADE,
                    image_url TEXT NOT NULL,
                    is_cover BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT now()
                )""")
                cur.execute("""CREATE TABLE IF NOT EXISTS site_tickets (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    message TEXT NOT NULL,
                    reply TEXT,
                    status TEXT DEFAULT 'open',
                    created_at TIMESTAMPTZ DEFAULT now(),
                    replied_at TIMESTAMPTZ
                )""")

                # ۸. اضافه کردن کاور پیش‌فرض
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
                
                print("DB Initialized successfully.")
            except Exception as e:
                print(f"DB Init Error: {e}")
                conn.rollback()
                raise e

# تابع ارسال ایمیل با Resend
def send_email_code(email, code):
    try:
        resend.Emails.send({
            "from": "RipperMods <noreply@rippermods.ir>", 
            "to": email,
            "subject": "کد ورود به RipperMods",
            "html": f"""
                <div dir="rtl" style="font-family: tahoma; text-align: center; padding: 20px;">
                    <h2>کد تایید شما</h2>
                    <h1 style="color: #007bff; letter-spacing: 5px;">{code}</h1>
                    <p>این کد فقط ۲ دقیقه اعتبار دارد.</p>
                </div>
            """
        })
        return True
    except Exception as e:
        print(f"Resend Error: {e}")
        return False

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
                cur.execute("SELECT 1 FROM orders WHERE user_id=%s AND product_id=%s AND status IN ('approved','delivered')", (session['site_user_id'], pid))
                has_bought = cur.fetchone() is not None
            
    video_url = product.get('video_url')
    if not video_url and ('سوپر' in product['name'] or 'super' in product['name'].lower()):
        video_url = "https://s21.uupload.ir/files/rippermodsgtavv100/Amir/4/VID_20261101_150016_007.mp4?play"
        
    return render_template('product.html', product=product, images=images, base_link=BASE_LINK, video_url=video_url, has_bought=has_bought)

# --- ثبت‌نام ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip().lower()
        
        if not full_name or not phone or not email:
            flash('لطفاً همه فیلدها را پر کنید.', 'error')
            return redirect(url_for('register'))
            
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM site_users WHERE email=%s", (email,))
                    existing_user = cur.fetchone()
                    
                    if existing_user:
                        flash('شما قبلاً با این ایمیل ثبت‌نام کرده‌اید. لطفاً وارد شوید.', 'error')
                        return redirect(url_for('login'))
                    
                    cur.execute("""
                        INSERT INTO site_users (email, full_name, phone) 
                        VALUES (%s, %s, %s) 
                        RETURNING id
                    """, (email, full_name, phone))
                    uid = cur.fetchone()['id']
                    
                    code = ''.join(random.choices(string.digits, k=6))
                    expires_at = datetime.now() + timedelta(minutes=2)
                    
                    cur.execute("DELETE FROM link_codes WHERE telegram_id=%s", (uid,)) 
                    cur.execute("INSERT INTO link_codes (telegram_id, code, expires_at) VALUES (%s, %s, %s)", (uid, code, expires_at))
                    
                    session['temp_user_id'] = uid
            
            if send_email_code(email, code):
                flash('کد ورود به ایمیل شما ارسال شد.', 'success')
                return redirect(url_for('verify_code'))
            else:
                flash('خطا در ارسال ایمیل. لطفاً بعداً تلاش کنید.', 'error')
                return redirect(url_for('register'))
                
        except Exception as e:
            print(f"Register Error: {e}")
            flash(f'خطا در ثبت‌نام: {str(e)}', 'error')
            return redirect(url_for('register'))

    return render_template('register.html')

# --- ورود ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        
        if email == TEST_ADMIN_EMAIL:
            session['is_admin'] = True
            return redirect(url_for('admin_panel'))

        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM site_users WHERE email=%s", (email,))
                    user = cur.fetchone()
                    
                    if not user:
                        flash('این ایمیل در سایت ثبت نام نکرده است.', 'error')
                        return redirect(url_for('login'))
                        
                    uid = user['id']
                    
                    code = ''.join(random.choices(string.digits, k=6))
                    expires_at = datetime.now() + timedelta(minutes=2)
                    
                    cur.execute("DELETE FROM link_codes WHERE telegram_id=%s", (uid,))
                    cur.execute("INSERT INTO link_codes (telegram_id, code, expires_at) VALUES (%s, %s, %s)", (uid, code, expires_at))
                    
                    session['temp_user_id'] = uid

            if send_email_code(email, code):
                flash('کد ورود به ایمیل شما ارسال شد.', 'success')
                return redirect(url_for('verify_code'))
            else:
                flash('خطا در ارسال ایمیل.', 'error')
                return redirect(url_for('login'))
        except Exception as e:
            print(f"Login Error: {e}")
            flash('خطا در ورود.', 'error')
            return redirect(url_for('login'))

    return render_template('login.html')

# --- تایید کد ---
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
                
                if link and link['telegram_id'] == session['temp_user_id']:
                    cur.execute("UPDATE link_codes SET used=true WHERE code=%s", (code,))
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
            
            cur.execute("SELECT * FROM orders WHERE user_id=%s ORDER BY created_at DESC", (user['id'],))
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
            uid = session['site_user_id']
            
            cur.execute("SELECT 1 FROM orders WHERE user_id=%s AND product_id=%s AND status IN ('approved','delivered')", (uid, pid))
            if cur.fetchone():
                flash('شما قبلاً این محصول را خریداری کرده‌اید.', 'error')
                return redirect(url_for('product_detail', pid=pid))
            
            cur.execute("SELECT * FROM products WHERE id=%s", (pid,))
            product = cur.fetchone()
            
            tc = f"RM-{random.randint(10000, 99999)}"
            amount = product['price']
            
            cur.execute(
                "INSERT INTO orders(user_id, product_id, product_name, amount, payment_method, status, tracking_code) "
                "VALUES(%s, %s, %s, %s, 'card', 'pending', %s) RETURNING id",
                (uid, pid, product['name'], amount, tc)
            )
            order_id = cur.fetchone()['id']
            
    return redirect(url_for('payment_page', order_id=order_id))

@app.route('/payment/<int:order_id>')
def payment_page(order_id):
    if 'site_user_id' not in session:
        return redirect(url_for('login'))
    
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM orders WHERE id=%s AND user_id=%s", (order_id, session['site_user_id']))
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

    try:
        img_data = file.read()
        if len(img_data) > 5 * 1024 * 1024:
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
            cur.execute("""
                SELECT o.*, u.full_name, u.phone, u.email 
                FROM orders o 
                LEFT JOIN site_users u ON o.user_id = u.id 
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
    new_short_desc = request.form.get('short_desc')
    new_full_desc = request.form.get('full_desc')
    new_video_url = request.form.get('video_url')
    
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
            
            cur.execute("""
                UPDATE products 
                SET short_desc = COALESCE(%s, short_desc),
                    full_desc = COALESCE(%s, full_desc),
                    video_url = COALESCE(%s, video_url)
                WHERE id = %s
            """, (new_short_desc, new_full_desc, new_video_url, pid))

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

try:
    init_db()
except Exception as e:
    print(f"Error initializing DB: {e}")

if __name__ == '__main__':
    app.run(debug=True, port=5000)