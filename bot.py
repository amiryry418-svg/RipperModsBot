# -*- coding: utf-8 -*-
"""RipperMods Persian Telegram shop + Customer Portal. PostgreSQL-backed, deployable on Render."""
from __future__ import annotations
import os, logging, threading, time, asyncio, io, re, random, string, secrets, requests
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, request, render_template, send_from_directory, redirect
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.error import TelegramError
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters)

try:
    import jdatetime
except ImportError:
    jdatetime = None

logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'), format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('rippermods')

app = Flask(__name__, template_folder='templates', static_folder='static')

BOT_TOKEN = os.environ.get('BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')
BASE_URL = (os.environ.get('BASE_URL') or '').rstrip('/')
LOG_CHANNEL_ID = os.environ.get('LOG_CHANNEL_ID', '-1004308422504')
try:
    LOG_CHANNEL_ID = int(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
except ValueError:
    LOG_CHANNEL_ID = None

_ADMIN_IDS_RAW = os.getenv('ADMIN_IDS', '').strip()
if not _ADMIN_IDS_RAW:
    raise RuntimeError('متغیر ADMIN_IDS تنظیم نشده است.')
try:
    ADMIN_IDS = {int(x.strip()) for x in _ADMIN_IDS_RAW.split(',') if x.strip()}
except ValueError:
    raise RuntimeError('ADMIN_IDS باید لیستی از آیدی‌های عددی باشد.')

_SUPER_ADMIN_RAW = os.getenv('SUPER_ADMIN', '').strip()
if not _SUPER_ADMIN_RAW:
    raise RuntimeError('متغیر SUPER_ADMIN تنظیم نشده است.')
try:
    SUPER_ADMIN = int(_SUPER_ADMIN_RAW)
except ValueError:
    raise RuntimeError('SUPER_ADMIN باید یک آیدی عددی باشد.')

if SUPER_ADMIN not in ADMIN_IDS:
    ADMIN_IDS.add(SUPER_ADMIN)

def _optional_int_env(name: str) -> Optional[int]:
    raw = os.getenv(name, '').strip()
    if not raw: return None
    try: return int(raw)
    except ValueError: return None

PORT_ADMIN = _optional_int_env('PORT_ADMIN')
ADMIN3 = _optional_int_env('ADMIN3')

BASE_LINK = "https://dl2.megadl.click/PS4-Hacked/GTAV/CUSA00419/BASE/"

INSTALL_TUTORIAL = """
🎮 **آموزش نصب پک GTA V (نسخه PS4/PS5)**

پکیج‌های فعلی صرفاً روی نسخه PS4 بازی کار می‌کنند و روش نصب پکیج روی PS4 و PS5 یکسان است. به محض ساخت نسخه PS5، آموزش نصب آن هم اضافه خواهد شد.

1️⃣ **بررسی نسخه:** ابتدا از نسخه بیس بازی خود اطمینان حاصل کنید که با بیس پیشنهادی ما یکی باشد.
2️⃣ **دانلود:** برنامه (WinRAR) را داشته باشید. فایل‌های مربوط به ماد را دانلود کنید.
3️⃣ **استخراج:** همه فایل‌ها را در یک پوشه قرار دهید، سپس روی پارت ۱ کلیک راست کرده و `Extract Here` را انتخاب کنید (نیازی به کار با پارت‌های دیگر نیست، خودش اتوماتیک تشخیص می‌دهد). منتظر بمانید تا فایل اکسترکت شود.
4️⃣ **انتقال:** وقتی فایل واحد `.pkg` ساخته شد، یک هارد اکسترنال یا فلش حداقل ۶۴ گیگ با فرمت `exFAT` به کامپیوتر وصل کنید و فایل را داخل آن کپی کنید.
5️⃣ **نصب روی کنسول:** فلش را به کنسول وصل کنید. وارد مسیر `Debug Settings / Package Installer` شوید و فایل را نصب کنید.
"""

REDOWNLOAD_PRICE = Decimal('150000')

_db_initialized = False
_db_lock = threading.Lock()
_bot_application: Optional[Application] = None

def _fa_dt(dt) -> str:
    if dt is None: return '—'
    try:
        if jdatetime is not None:
            j = jdatetime.datetime.fromgregorian(datetime=dt)
            return j.strftime('%Y/%m/%d - %H:%M')
        return dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        try: return dt.strftime('%Y-%m-%d %H:%M')
        except Exception: return str(dt)

def _gen_tracking_code(prefix: str = 'RM') -> str:
    return f"{prefix}-{random.randint(1000, 9999)}"

def _unique_tracking_code(cur, table: str) -> str:
    for _ in range(20):
        code = _gen_tracking_code()
        cur.execute(f"SELECT 1 FROM {table} WHERE tracking_code=%s", (code,))
        if not cur.fetchone(): return code
    return f"RM-{int(time.time()) % 9000 + 1000}"

def _gen_connect_code() -> str:
    return f"{random.randint(100000, 999999)}"

def _gen_session_token() -> str:
    return secrets.token_urlsafe(24)

def _create_connect_code(cur, user_id: int, ttl_minutes: int = 10) -> str:
    cur.execute("UPDATE connect_codes SET used=true WHERE user_id=%s AND used=false", (user_id,))
    for _ in range(20):
        code = _gen_connect_code()
        cur.execute("SELECT 1 FROM connect_codes WHERE code=%s", (code,))
        if not cur.fetchone():
            expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
            cur.execute("INSERT INTO connect_codes(user_id, code, expires_at) VALUES(%s, %s, %s)", (user_id, code, expires))
            return code
    raise RuntimeError('خطا در تولید کد اتصال')

@app.get('/health')
def health():
    try:
        with db() as conn:
            with conn.cursor() as cur: cur.execute('SELECT 1')
        return jsonify(status='ok', database='connected'), 200
    except Exception:
        return jsonify(status='degraded', database='unavailable'), 503

@app.get('/')
def index():
    return redirect('/portal')

@app.get('/portal')
def portal():
    try: return render_template('portal.html')
    except Exception: return "فایل portal.html پیدا نشد", 500

@app.get('/dashboard')
def dashboard():
    try: return render_template('portal.html')
    except Exception: return "فایل portal.html پیدا نشد", 500

@contextmanager
def db():
    if not DATABASE_URL: raise RuntimeError('DATABASE_URL تنظیم نشده است.')
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=8, sslmode='require', cursor_factory=RealDictCursor)
    try:
        with conn: yield conn
    finally: conn.close()

def load_ps4_trainers_from_files():
    files = {'json': 'json.txt', 'mc4': 'mc4.txt', 'shn': 'shn.txt'}
    total = 0
    with db() as conn:
        with conn.cursor() as cur:
            for fmt, fname in files.items():
                if not os.path.exists(fname): continue
                with open(fname, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or '=' not in line: continue
                        left, right = line.split('=', 1)
                        left = left.strip()
                        game_name = right.strip()
                        m = re.match(r'^(CUSA\d+)_(\d+[.\d]*)[._]', left)
                        if not m: continue
                        cusa_id = m.group(1)
                        version = m.group(2)
                        if fmt == 'mc4': file_url = f"https://raw.githubusercontent.com/GoldHEN/GoldHEN_Cheat_Repository/main/mc4/{left}"
                        elif fmt == 'json': file_url = f"https://raw.githubusercontent.com/GoldHEN/GoldHEN_Cheat_Repository/main/json/{left}"
                        else: file_url = f"https://raw.githubusercontent.com/GoldHEN/GoldHEN_Cheat_Repository/main/shn/{left}"
                        try:
                            cur.execute("INSERT INTO ps4_trainers(cusa_id, version, file_format, game_name, file_url) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", (cusa_id, version, fmt, game_name, file_url))
                            if cur.rowcount > 0: total += 1
                        except Exception: pass
    log.info(f"Loaded {total} PS4 trainers.")

def init_db():
    global _db_initialized
    with _db_lock:
        if _db_initialized: return
        schema = [
            """CREATE TABLE IF NOT EXISTS users (telegram_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, wallet NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK(wallet >= 0), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen TIMESTAMPTZ NOT NULL DEFAULT now(), reg_name TEXT, phone TEXT)""",
            """ALTER TABLE users ADD COLUMN IF NOT EXISTS reg_name TEXT""",
            """ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT""",
            """CREATE TABLE IF NOT EXISTS products (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', price NUMERIC(14,2) NOT NULL CHECK(price >= 0), active BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), upgrade_price NUMERIC(14,2) DEFAULT 0)""",
            """ALTER TABLE products ADD COLUMN IF NOT EXISTS upgrade_price NUMERIC(14,2) DEFAULT 0""",
            """CREATE TABLE IF NOT EXISTS orders (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), product_id BIGINT REFERENCES products(id), product_name TEXT NOT NULL, amount NUMERIC(14,2) NOT NULL CHECK(amount >= 0), payment_method TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','delivered','cancelled')), receipt_file_id TEXT, coupon_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), wallet_debited BOOLEAN NOT NULL DEFAULT false, delivery_sent BOOLEAN NOT NULL DEFAULT false, is_upgrade BOOLEAN NOT NULL DEFAULT false)""",
            """ALTER TABLE orders ADD COLUMN IF NOT EXISTS is_upgrade BOOLEAN NOT NULL DEFAULT false""",
            """ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_code TEXT""",
            """ALTER TABLE orders ADD COLUMN IF NOT EXISTS receipt_message_id BIGINT""",
            """ALTER TABLE orders ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ""",
            """ALTER TABLE orders ADD COLUMN IF NOT EXISTS links_sent_via_portal BOOLEAN NOT NULL DEFAULT false""",
            """ALTER TABLE orders ADD COLUMN IF NOT EXISTS links_expires_at TIMESTAMPTZ""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_tracking ON orders(tracking_code) WHERE tracking_code IS NOT NULL""",
            """CREATE TABLE IF NOT EXISTS wallet_transactions (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), amount NUMERIC(14,2) NOT NULL, kind TEXT NOT NULL, reference TEXT, reason TEXT, admin_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), idempotency_key TEXT UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS topups (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), amount NUMERIC(14,2) NOT NULL CHECK(amount > 0), receipt_file_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')), reviewed_by BIGINT, review_note TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), reviewed_at TIMESTAMPTZ)""",
            """ALTER TABLE topups ADD COLUMN IF NOT EXISTS tracking_code TEXT""",
            """ALTER TABLE topups ADD COLUMN IF NOT EXISTS receipt_message_id BIGINT""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_topups_tracking ON topups(tracking_code) WHERE tracking_code IS NOT NULL""",
            """CREATE TABLE IF NOT EXISTS coupons (id BIGSERIAL PRIMARY KEY, code TEXT NOT NULL UNIQUE, discount_type TEXT NOT NULL CHECK(discount_type IN ('percent','fixed')), value NUMERIC(14,2) NOT NULL CHECK(value > 0), max_uses INTEGER, uses INTEGER NOT NULL DEFAULT 0, expires_at TIMESTAMPTZ, active BOOLEAN NOT NULL DEFAULT true, created_by BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
            """CREATE TABLE IF NOT EXISTS coupon_uses (id BIGSERIAL PRIMARY KEY, coupon_id BIGINT NOT NULL REFERENCES coupons(id), user_id BIGINT NOT NULL REFERENCES users(telegram_id), order_id BIGINT NOT NULL UNIQUE REFERENCES orders(id), used_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(coupon_id,user_id))""",
            """CREATE TABLE IF NOT EXISTS nostalgia_requests (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), game TEXT NOT NULL, platform TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new', admin_reply TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
            """CREATE TABLE IF NOT EXISTS custom_pack_requests (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), game TEXT NOT NULL, platform TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new', admin_reply TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
            """CREATE TABLE IF NOT EXISTS custom_trainer_requests (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), details TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', admin_reply TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
            """CREATE TABLE IF NOT EXISTS support_messages (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), direction TEXT NOT NULL CHECK(direction IN ('user_to_admin','admin_to_user')), message_text TEXT, file_id TEXT, file_type TEXT, admin_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
            """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by BIGINT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
            """CREATE TABLE IF NOT EXISTS admin_audit (id BIGSERIAL PRIMARY KEY, admin_id BIGINT NOT NULL, action TEXT NOT NULL, target TEXT, details TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
            """CREATE TABLE IF NOT EXISTS product_links (id BIGSERIAL PRIMARY KEY, product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE, link TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
            """CREATE TABLE IF NOT EXISTS ps4_trainers (id BIGSERIAL PRIMARY KEY, cusa_id TEXT NOT NULL, version TEXT NOT NULL, file_format TEXT NOT NULL, game_name TEXT NOT NULL, file_url TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(cusa_id, version, file_format))""",
            """CREATE TABLE IF NOT EXISTS connect_codes (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), code TEXT NOT NULL UNIQUE, used BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS web_sessions (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), session_token TEXT NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL, last_used TIMESTAMPTZ NOT NULL DEFAULT now())""",
            "CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
            "CREATE INDEX IF NOT EXISTS idx_topups_status ON topups(status)",
            "CREATE INDEX IF NOT EXISTS idx_nostalgia_status ON nostalgia_requests(status)",
            "CREATE INDEX IF NOT EXISTS idx_support_user_created ON support_messages(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_ps4_trainers_cusa ON ps4_trainers(cusa_id)",
            "CREATE INDEX IF NOT EXISTS idx_connect_codes_code ON connect_codes(code)",
            "CREATE INDEX IF NOT EXISTS idx_web_sessions_token ON web_sessions(session_token)",
        ]
        with db() as conn:
            with conn.cursor() as cur:
                for sql in schema: cur.execute(sql)
                cur.execute("DELETE FROM product_links WHERE product_id NOT IN (SELECT MIN(id) FROM products GROUP BY name)")
                cur.execute("DELETE FROM products WHERE id NOT IN (SELECT MIN(id) FROM products GROUP BY name)")
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_name_unique ON products (name)")
                cur.execute("INSERT INTO products(name,description,price,active) VALUES (%s,%s,%s,true) ON CONFLICT (name) DO NOTHING", ('سوپر پک', 'بسته ویژه RipperMods', 799000))
                cur.execute("INSERT INTO products(name,description,price,active,upgrade_price) VALUES (%s,%s,%s,true,%s) ON CONFLICT (name) DO NOTHING", ('پک ایرانیزه', 'نسخه ایرانیزه شده GTA V (V2)', 799000, 350000))
                
                cur.execute("SELECT id FROM products WHERE name='سوپر پک'")
                prod_row = cur.fetchone()
                if prod_row:
                    prod_id = prod_row['id']
                    cur.execute("SELECT 1 FROM product_links WHERE product_id=%s LIMIT 1", (prod_id,))
                    if not cur.fetchone():
                        initial_links = [
                            "https://s17.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part25.rar",
                            "https://s17.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part24.rar",
                            "https://s17.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part23.rar",
                            "https://s17.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part22.rar",
                            "https://s17.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part21.rar",
                            "https://s17.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part20.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part19.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part18.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part17.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part16.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part15.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part14.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part13.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part12.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part11.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part10.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part09.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part08.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part07.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part06.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part05.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part04.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part03.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part02.rar",
                            "https://s25.uupload.ir/files/rippermodsgtavv100/gtaV%20latest/UP1004-CUSA00419_00-GTAVDIGITALDOWNL-A0153-V0100-maybefinal.part01.rar"
                        ]
                        for link in initial_links:
                            cur.execute("INSERT INTO product_links(product_id, link) VALUES(%s, %s)", (prod_id, link))
                            
        load_ps4_trainers_from_files()
        _db_initialized = True

def audit(admin_id: int, action: str, target: str = '', details: str = ''):
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO admin_audit(admin_id,action,target,details) VALUES(%s,%s,%s,%s)', (admin_id, action, target, details[:2000]))
    except Exception: pass

async def log_event(context: ContextTypes.DEFAULT_TYPE, text: str):
    if not LOG_CHANNEL_ID: return
    try: await context.bot.send_message(LOG_CHANNEL_ID, text[:3900])
    except TelegramError: pass

async def log_photo(context: ContextTypes.DEFAULT_TYPE, photo_file_id: str, caption: str) -> Optional[int]:
    if not LOG_CHANNEL_ID: return None
    try:
        m = await context.bot.send_photo(LOG_CHANNEL_ID, photo_file_id, caption=caption[:1000])
        return m.message_id
    except TelegramError: return None

async def log_reply_to(context: ContextTypes.DEFAULT_TYPE, reply_to_message_id: int, text: str) -> bool:
    if not LOG_CHANNEL_ID: return False
    try:
        await context.bot.send_message(LOG_CHANNEL_ID, text[:3900], reply_to_message_id=reply_to_message_id)
        return True
    except TelegramError: return False

def is_admin(uid: int) -> bool: return uid in ADMIN_IDS
def is_super(uid: int) -> bool: return uid == SUPER_ADMIN

def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton('🛍 فروشگاه'), KeyboardButton('💰 کیف پول')],
        [KeyboardButton('📦 سفارش‌های من'), KeyboardButton('👤 حساب کاربری')],
        [KeyboardButton('🎟 کد تخفیف'), KeyboardButton('📖 آموزش نصب')],
        [KeyboardButton('🎮 ترینرهای PS4')],
        [KeyboardButton('🎮 درخواست پورت'), KeyboardButton('📦 درخواست پک اختصاصی')],
        [KeyboardButton('💬 پشتیبانی'), KeyboardButton('🆓 دریافت بیس رایگان')],
        [KeyboardButton('🔄 دریافت مجدد لینک'), KeyboardButton('🌐 ورود به پورتال')],
        [KeyboardButton('🔄 اتصال مجدد / شروع مجدد')]
    ], resize_keyboard=True)

def back_kb():
    return ReplyKeyboardMarkup([[KeyboardButton('⬅️ بازگشت'), KeyboardButton('❌ لغو')]], resize_keyboard=True)

def admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📊 آمار', callback_data='adm:stats'), InlineKeyboardButton('📦 سفارش‌ها', callback_data='adm:orders')],
        [InlineKeyboardButton('💳 شارژهای در انتظار', callback_data='adm:topups'), InlineKeyboardButton('🧾 سفارش‌های در انتظار', callback_data='adm:pendingorders')],
        [InlineKeyboardButton('📋 درخواست‌ها', callback_data='adm:requests')],
        [InlineKeyboardButton('🔍 پیگیری سفارش', callback_data='adm:track')],
        [InlineKeyboardButton('🎟 ساخت کد تخفیف', callback_data='adm:coupon'), InlineKeyboardButton('🔎 جست‌وجوی کاربر', callback_data='adm:finduser')],
        [InlineKeyboardButton('💰 تغییر موجودی', callback_data='adm:wallet'), InlineKeyboardButton('⚙️ تنظیمات پرداخت', callback_data='adm:settings')],
        [InlineKeyboardButton('💵 تغییر قیمت پک‌ها', callback_data='adm:edit_price'), InlineKeyboardButton('🔗 تغییر لینک محصولات', callback_data='adm:edit_links')],
        [InlineKeyboardButton('📣 پیام همگانی', callback_data='adm:broadcast'), InlineKeyboardButton('📜 سوابق مدیران', callback_data='adm:audit')]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO users(telegram_id,username,first_name) VALUES(%s,%s,%s) ON CONFLICT(telegram_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name,last_seen=now() RETURNING phone, reg_name', (u.id, u.username, u.first_name))
            row = cur.fetchone()
    
    context.user_data.clear()
    
    if not row or not row['phone'] or not row['reg_name']:
        context.user_data['flow'] = 'reg_name'
        await update.effective_message.reply_text(
            'سلام! به فروشگاه RipperMods خوش آمدید 🌟\n'
            'برای استفاده از ربات، ابتدا باید ثبت‌نام کنید.\n\n'
            'لطفاً **نام و نام خانوادگی** خود را وارد کنید (مثال: علی رضایی):',
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton('❌ لغو')]], resize_keyboard=True)
        )
        return

    welcome = 'به فروشگاه RipperMods خوش آمدید 🌟\nاز منوی زیر گزینه موردنظر را انتخاب کنید.'
    photo = os.getenv('WELCOME_PHOTO', '').strip()
    try:
        if photo: await update.effective_message.reply_photo(photo, caption=welcome, reply_markup=main_keyboard())
        else: await update.effective_message.reply_text(welcome, reply_markup=main_keyboard())
    except TelegramError: await update.effective_message.reply_text(welcome, reply_markup=main_keyboard())
    if is_admin(u.id): await update.effective_message.reply_text('پنل مدیریت:', reply_markup=admin_kb())

async def send_portal_access(context: ContextTypes.DEFAULT_TYPE, user_id: int, order_id: int, product_name: str, tracking_code: str):
    if not BASE_URL: return
    with db() as conn:
        with conn.cursor() as cur:
            connect_code = _create_connect_code(cur, user_id, ttl_minutes=10)
            expires_at = datetime.now(timezone.utc) + timedelta(days=5)
            cur.execute("UPDATE orders SET links_expires_at=%s WHERE id=%s", (expires_at, order_id))
    portal_url = f"{BASE_URL}/portal"
    expires_str = _fa_dt(expires_at)
    msg = (
        f"🎉 سفارش شما تایید و تحویل داده شد!\n\n📦 محصول: {product_name}\n🔢 کد پیگیری: {tracking_code}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n🔑 کد اتصال پورتال: {connect_code}\n⏱ اعتبار: ۱۰ دقیقه\n━━━━━━━━━━━━━━━━━━\n\n"
        f"🌐 برای دریافت لینک‌ها به پورتال برو:\n{portal_url}\n\n"
        f"📝 کد پیگیری: {tracking_code}\n📝 کد اتصال: {connect_code}\n\n"
        f"🔒 **رمز فایل:** `t.me/rippermods`\n\n"
        f"⚠️ لینک‌ها تا {expires_str} اعتبار دارن (۵ روز)\n⚠️ فقط یک بار قابل دریافت هستن"
    )
    try: await context.bot.send_message(user_id, msg, disable_web_page_preview=True, parse_mode='Markdown')
    except TelegramError as e: log.error(f'Failed to send portal access: {e}')

async def review_topup(q, context, rid, decision, admin_id):
    uinfo = {}; tracking = '—'; created_at = None; amount = Decimal('0'); receipt_msg_id = None; receipt_file_id = None
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM topups WHERE id=%s FOR UPDATE', (rid,))
            r = cur.fetchone()
            if not r or r['status'] != 'pending':
                await q.message.reply_text('این درخواست قبلاً بررسی شده یا وجود ندارد.')
                return
            new = 'approved' if decision == 'approve' else 'rejected'
            cur.execute("UPDATE topups SET status=%s,reviewed_by=%s,reviewed_at=now() WHERE id=%s AND status='pending'", (new, admin_id, rid))
            if cur.rowcount != 1:
                await q.message.reply_text('این درخواست قبلاً بررسی شده است.')
                return
            if new == 'approved':
                cur.execute('UPDATE users SET wallet=wallet+%s WHERE telegram_id=%s', (r['amount'], r['user_id']))
                cur.execute("INSERT INTO wallet_transactions(user_id,amount,kind,reference,reason,admin_id,idempotency_key) VALUES(%s,%s,'topup',%s,'تأیید شارژ',%s,%s) ON CONFLICT(idempotency_key) DO NOTHING", (r['user_id'], r['amount'], str(rid), admin_id, f'topup-{rid}'))
            cur.execute('SELECT telegram_id, reg_name, phone, username, first_name FROM users WHERE telegram_id=%s', (r['user_id'],))
            uinfo = cur.fetchone() or {}
            uinfo['telegram_id'] = r['user_id']
            tracking = r.get('tracking_code') or '—'
            created_at = r['created_at']
            amount = r['amount']
            receipt_msg_id = r.get('receipt_message_id')
            receipt_file_id = r['receipt_file_id']
    audit(admin_id, 'topup_review', str(rid), decision)
    try:
        if decision == 'approve': await context.bot.send_message(r['user_id'], f"✅ شارژ کیف پول شما تایید شد.\n🔢 کد پیگیری: {tracking}\n💰 مبلغ: {int(amount):,} تومان")
        else: await context.bot.send_message(r['user_id'], f"❌ درخواست شارژ شما رد شد.\n🔢 کد پیگیری: {tracking}")
    except TelegramError: pass
    await q.message.reply_text('بررسی ثبت شد.')
    status_fa = '✅ تایید شد' if decision == 'approve' else '❌ رد شد'
    name = uinfo.get('reg_name') or uinfo.get('first_name') or 'ناشناس'
    uname = f"@{uinfo['username']}" if uinfo.get('username') else 'بدون‌یوزرنیم'
    phone = uinfo.get('phone') or 'نامشخص'
    body = (f"{status_fa} — شارژ کیف پول\n━━━━━━━━━━━━━━━━━━\n🔢 کد پیگیری: {tracking}\n👤 نام: {name}\n🔗 یوزرنیم: {uname}\n🆔 آیدی: {r['user_id']}\n📱 تلفن: {phone}\n💰 مبلغ: {int(amount):,} تومان\n📅 زمان تراکنش: {_fa_dt(created_at)}\n📅 زمان بررسی: {_fa_dt(datetime.now(timezone.utc))}\n👮 مدیر: ادمین اصلی\n━━━━━━━━━━━━━━━━━━")
    replied = False
    if receipt_msg_id: replied = await log_reply_to(context, receipt_msg_id, body)
    if not replied and receipt_file_id and LOG_CHANNEL_ID:
        try: await context.bot.send_photo(LOG_CHANNEL_ID, receipt_file_id, caption=body[:1000])
        except TelegramError: pass

async def review_order(q, context, rid, decision, admin_id):
    uinfo = {}; tracking = '—'; created_at = None; amount = Decimal('0'); product_name = '—'; receipt_msg_id = None; receipt_file_id = None; user_id = None
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE', (rid,))
            r = cur.fetchone()
            if not r or r['status'] != 'pending':
                await q.message.reply_text('این سفارش قبلاً بررسی شده یا وجود ندارد.')
                return
            new = 'approved' if decision == 'approve' else 'rejected'
            cur.execute("UPDATE orders SET status=%s,updated_at=now(),reviewed_at=now() WHERE id=%s AND status='pending'", (new, rid))
            if cur.rowcount != 1:
                await q.message.reply_text('این سفارش قبلاً بررسی شده است.')
                return
            if new == 'approved' and r['coupon_id']:
                cur.execute('INSERT INTO coupon_uses(coupon_id,user_id,order_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING', (r['coupon_id'], r['user_id'], rid))
                if cur.rowcount: cur.execute('UPDATE coupons SET uses=uses+1 WHERE id=%s', (r['coupon_id'],))
            cur.execute('SELECT telegram_id, reg_name, phone, username, first_name FROM users WHERE telegram_id=%s', (r['user_id'],))
            uinfo = cur.fetchone() or {}
            uinfo['telegram_id'] = r['user_id']
            tracking = r.get('tracking_code') or '—'
            created_at = r['created_at']
            amount = r['amount']
            product_name = r['product_name']
            receipt_msg_id = r.get('receipt_message_id')
            receipt_file_id = r['receipt_file_id']
            user_id = r['user_id']
    audit(admin_id, 'order_review', str(rid), decision)
    await q.message.reply_text('بررسی سفارش ثبت شد.')
    if decision == 'approve':
        try:
            await send_portal_access(context, user_id, rid, product_name, tracking)
            with db() as conn:
                with conn.cursor() as cur: cur.execute("UPDATE orders SET status='delivered',delivery_sent=true,updated_at=now() WHERE id=%s", (rid,))
        except Exception as e: log.exception(f'Failed to send portal access for order {rid}: {e}')
    else:
        try: await context.bot.send_message(user_id, f"❌ سفارش شما رد شد.\n🔢 کد پیگیری: {tracking}")
        except TelegramError: pass
    status_fa = '✅ تایید شد' if decision == 'approve' else '❌ رد شد'
    name = uinfo.get('reg_name') or uinfo.get('first_name') or 'ناشناس'
    uname = f"@{uinfo['username']}" if uinfo.get('username') else 'بدون‌یوزرنیم'
    phone = uinfo.get('phone') or 'نامشخص'
    body = (f"{status_fa} — سفارش\n━━━━━━━━━━━━━━━━━━\n🔢 کد پیگیری: {tracking}\n👤 نام: {name}\n🔗 یوزرنیم: {uname}\n🆔 آیدی: {user_id}\n📱 تلفن: {phone}\n📦 محصول: {product_name}\n💰 مبلغ: {int(amount):,} تومان\n📅 زمان تراکنش: {_fa_dt(created_at)}\n📅 زمان بررسی: {_fa_dt(datetime.now(timezone.utc))}\n👮 مدیر: ادمین اصلی\n━━━━━━━━━━━━━━━━━━")
    replied = False
    if receipt_msg_id: replied = await log_reply_to(context, receipt_msg_id, body)
    if not replied and receipt_file_id and LOG_CHANNEL_ID:
        try: await context.bot.send_photo(LOG_CHANNEL_ID, receipt_file_id, caption=body[:1000])
        except TelegramError: pass

async def deliver(context, uid, order_id, product_name):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE orders SET status='delivered',delivery_sent=true,updated_at=now() WHERE id=%s AND status IN ('approved', 'delivered') AND delivery_sent=false RETURNING id", (order_id,))

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data or ''
    try:
        if data.startswith('trainer:'):
            _, cusa_full, version, file_format = data.split(':', 3)
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT game_name, file_url FROM ps4_trainers WHERE cusa_id=%s AND version=%s AND file_format=%s", (cusa_full, version, file_format))
                    row = cur.fetchone()
            if not row: return
            await q.message.reply_text('⏳ در حال دانلود و ارسال فایل...')
            try:
                res = requests.get(row['file_url'], timeout=20)
                file_bytes = io.BytesIO(res.content)
                file_name = f"{cusa_full}_{version}.{file_format if file_format != 'mc4' else 'mc4.xml'}"
                caption = f"✅ ترینر {row['game_name']}\n📀 نسخه: {version}\n📁 فرمت: {file_format}\n\n📌 فایل رو با FTP به مسیر /user/data/GoldHEN/cheats/{file_format}/ منتقل کنید."
                await context.bot.send_document(uid, document=InputFile(file_bytes, filename=file_name), caption=caption, parse_mode='Markdown')
            except Exception: await q.message.reply_text('❌ دانلود فایل با خطا مواجه شد.')
            return
        if data.startswith('buyw:') or data.startswith('buyc:'):
            method = 'wallet' if data.startswith('buyw:') else 'card'
            pid = int(data.split(':')[1])
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT * FROM products WHERE id=%s AND active=true', (pid,))
                    p = cur.fetchone()
                    if not p: return
                    cur.execute("SELECT 1 FROM orders WHERE user_id=%s AND product_id=%s AND status IN ('approved','delivered') AND is_upgrade=false AND product_name NOT LIKE 'دریافت مجدد%%'", (uid, pid))
                    if cur.fetchone(): await q.message.reply_text('⛔ شما قبلاً این محصول را سفارش داده‌اید.'); return
            amount = Decimal(p['price'])
            if method == 'wallet':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT wallet FROM users WHERE telegram_id=%s FOR UPDATE', (uid,))
                        user = cur.fetchone()
                        if not user or user['wallet'] < amount: await q.message.reply_text('موجودی کیف پول کافی نیست.'); return
                        tc = _unique_tracking_code(cur, 'orders')
                        cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,wallet_debited,tracking_code) VALUES(%s,%s,%s,%s,'wallet','approved',true,%s) RETURNING id", (uid, pid, p['name'], amount, tc))
                        order = cur.fetchone()['id']
                        cur.execute('UPDATE users SET wallet=wallet-%s WHERE telegram_id=%s', (amount, uid))
                        cur.execute("INSERT INTO wallet_transactions(user_id,amount,kind,reference,reason,idempotency_key) VALUES(%s,%s,'purchase',%s,'خرید محصول',%s)", (uid, -amount, str(order), f'order-{order}'))
                await q.message.reply_text(f'✅ خرید شما ثبت شد.\n🔢 کد پیگیری: {tc}')
                await deliver(context, uid, order, p['name'])
                await send_portal_access(context, uid, order, p['name'], tc)
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT reg_name, phone, username FROM users WHERE telegram_id=%s', (uid,))
                        uinfo = cur.fetchone() or {}
                await log_event(context, f'🛒 خرید با کیف پول\n🔢 {tc}\n👤 {uinfo.get("reg_name")} | @{uinfo.get("username")} | {uid}\n📦 {p["name"]}\n💰 {int(amount):,} تومان')
            else:
                with db() as conn:
                    with conn.cursor() as cur:
                        tc = _unique_tracking_code(cur, 'orders')
                        cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,tracking_code) VALUES(%s,%s,%s,%s,'card','pending',%s) RETURNING id", (uid, pid, p['name'], amount, tc))
                        order = cur.fetchone()['id']
                card = os.getenv('CARD_NUMBER', 'تنظیم نشده')
                holder = os.getenv('CARD_HOLDER', 'تنظیم نشده')
                msg = f"برای پرداخت، مبلغ {int(amount):,} تومان به کارت زیر واریز کنید:\n\n🔢 کد پیگیری: {tc}\n\n===================\n{card}\n**{holder}**\n===================\n\nبعد از پرداخت رسید را بفرستید."
                kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ پرداخت کردم | ارسال رسید', callback_data=f'rcpt:{order}:order')]])
                await q.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')
            return
        if data.startswith('rlsel:'):
            pid = int(data.split(':')[1])
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT name FROM products WHERE id=%s", (pid,)); p = cur.fetchone()
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('پرداخت با کیف پول', callback_data=f'rl:{pid}:wallet')], [InlineKeyboardButton('پرداخت کارت‌به‌کارت', callback_data=f'rl:{pid}:card')]])
            await q.message.reply_text(f"برای دریافت مجدد لینک‌های {p['name']}، مبلغ {int(REDOWNLOAD_PRICE):,} تومان هزینه دارد.\nنحوه پرداخت را انتخاب کنید:", reply_markup=kb)
            return
        if data.startswith('rl:'):
            _, pid, method = data.split(':')
            pid = int(pid)
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM products WHERE id=%s", (pid,)); p = cur.fetchone()
            context.user_data['flow'] = 'redl_confirm'; context.user_data['rl_pid'] = pid; context.user_data['rl_method'] = method
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ متوجه شدم، ادامه', callback_data=f'rlok:{pid}:{method}')], [InlineKeyboardButton('❌ لغو', callback_data='main:back')]])
            await q.message.reply_text(f"⚠️ **توجه مهم** ⚠️\n\nشما در حال پرداخت {int(REDOWNLOAD_PRICE):,} تومان برای دریافت مجدد لینک‌های {p['name']} هستید.\nاگر این پک را نخریده باشید، پول شما بازگشت داده نمی‌شود.", reply_markup=kb, parse_mode='Markdown')
            return
        if data.startswith('rlok:'):
            _, pid, method = data.split(':'); pid = int(pid)
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM products WHERE id=%s", (pid,)); p = cur.fetchone()
            amount = REDOWNLOAD_PRICE
            if method == 'wallet':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT wallet FROM users WHERE telegram_id=%s FOR UPDATE', (uid,))
                        u = cur.fetchone()
                        if not u or u['wallet'] < amount: await q.message.reply_text('موجودی کافی نیست.'); return
                        cur.execute('UPDATE users SET wallet=wallet-%s WHERE telegram_id=%s', (amount, uid))
                        tc = _unique_tracking_code(cur, 'orders')
                        cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,wallet_debited,delivery_sent,is_upgrade,tracking_code) VALUES(%s,%s,%s,%s,'wallet','approved',true,false,false,%s) RETURNING id", (uid, pid, f"دریافت مجدد {p['name']}", amount, tc))
                        new_oid = cur.fetchone()['id']
                        cur.execute("INSERT INTO wallet_transactions(user_id,amount,kind,reference,reason,idempotency_key) VALUES(%s,%s,'purchase',%s,%s,%s)", (uid, -amount, str(new_oid), f"دریافت مجدد {p['name']}", f'redl-{new_oid}'))
                await q.message.reply_text(f'✅ پرداخت انجام شد.\n🔢 کد پیگیری: {tc}')
                await deliver(context, uid, new_oid, p['name'])
                await send_portal_access(context, uid, new_oid, f"دریافت مجدد {p['name']}", tc)
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT reg_name, phone, username FROM users WHERE telegram_id=%s', (uid,))
                        uinfo = cur.fetchone() or {}
                await log_event(context, f'🔄 دریافت مجدد لینک\n🔢 {tc}\n👤 {uinfo.get("reg_name")} | @{uinfo.get("username")} | {uid}\n💰 {int(amount):,} تومان')
            else:
                with db() as conn:
                    with conn.cursor() as cur:
                        tc = _unique_tracking_code(cur, 'orders')
                        cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,is_upgrade,tracking_code) VALUES(%s,%s,%s,%s,'card','pending',false,%s) RETURNING id", (uid, pid, f"دریافت مجدد {p['name']}", amount, tc))
                        order = cur.fetchone()['id']
                card = os.getenv('CARD_NUMBER', 'تنظیم نشده'); holder = os.getenv('CARD_HOLDER', 'تنظیم نشده')
                msg = f"برای دریافت مجدد لینک ({p['name']})، مبلغ {int(amount):,} تومان به کارت زیر واریز کنید:\n\n🔢 کد پیگیری: {tc}\n\n===================\n{card}\n**{holder}**\n===================\n\nبعد از پرداخت رسید را بفرستید."
                kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ پرداخت کردم | ارسال رسید', callback_data=f'rcpt:{order}:order')]])
                await q.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')
                context.user_data.update(flow='order_receipt', order_id=order)
            return
        if data.startswith('topup:') or data.startswith('orderreview:'):
            _, rid, decision = data.split(':')
            if not is_super(uid): await q.message.reply_text('⛔ فقط مدیر اصلی اجازه بررسی رسیدها را دارد.'); return
            if data.startswith('topup:'): await review_topup(q, context, int(rid), decision, uid)
            else: await review_order(q, context, int(rid), decision, uid)
            return
        if data.startswith('rcpt:'):
            _, rid, kind = data.split(':')
            context.user_data['flow'] = 'order_receipt' if kind == 'order' else 'topup_receipt'
            if kind == 'order': context.user_data['order_id'] = int(rid)
            await q.message.reply_text('لطفاً تصویر رسید پرداخت خود را همینجا ارسال کنید:')
            return
        if data.startswith('adm:'):
            if not is_admin(uid): await q.message.reply_text('دسترسی ندارید.'); return
            action = data[4:]
            if action in ('coupon', 'wallet', 'edit_links', 'edit_price') and not is_super(uid): await q.message.reply_text('⛔ این بخش فقط برای مدیر اصلی است.'); return
            if action == 'track': context.user_data['flow'] = 'adm_track'; await q.message.reply_text('لطفاً کد پیگیری را وارد کنید (مثال: RM-4827):'); return
            if action == 'requests':
                kb = InlineKeyboardMarkup([[InlineKeyboardButton('🎮 درخواست‌های پورت', callback_data='req:ports')], [InlineKeyboardButton('📦 درخواست‌های پک اختصاصی', callback_data='req:custom_packs')], [InlineKeyboardButton('🛠 درخواست‌های ترینر اختصاصی', callback_data='req:custom_trainers')]])
                await q.message.reply_text('📋 کدوم لیست درخواست‌ها رو می‌خوای ببینی؟', reply_markup=kb); return
            if action == 'stats':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT count(*) n,coalesce(sum(amount),0) total FROM orders WHERE status IN ('approved','delivered')"); sales = cur.fetchone()
                        cur.execute("SELECT count(*) n FROM orders WHERE status='pending'"); pending = cur.fetchone()['n']
                        cur.execute("SELECT count(*) n FROM topups WHERE status='pending'"); top = cur.fetchone()['n']
                        cur.execute('SELECT count(*) n FROM users'); users = cur.fetchone()['n']
                await q.message.reply_text(f"📊 آمار\nکاربران: {users}\nسفارش‌های تأییدشده/تحویل‌شده: {sales['n']}\nدرآمد ثبت‌شده: {int(sales['total']):,} تومان\nسفارش‌های در انتظار: {pending}\nشارژهای در انتظار: {top}", reply_markup=admin_kb())
            elif action in ('topups', 'pendingorders'):
                table = 'topups' if action == 'topups' else 'orders'
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {table} WHERE status='pending' ORDER BY id LIMIT 30"); rows = cur.fetchall()
                if not rows: await q.message.reply_text('موردی در انتظار نیست.'); return
                for r in rows:
                    if table == 'topups':
                        kb = InlineKeyboardMarkup([[InlineKeyboardButton('تأیید شارژ', callback_data=f"topup:{r['id']}:approve"), InlineKeyboardButton('رد شارژ', callback_data=f"topup:{r['id']}:reject")]])
                        await q.message.reply_photo(r['receipt_file_id'], caption=f"شارژ {r.get('tracking_code') or '#'+str(r['id'])} | کاربر {r['user_id']} | {int(r['amount']):,} تومان", reply_markup=kb)
                    else:
                        kb = InlineKeyboardMarkup([[InlineKeyboardButton('تأیید و تحویل', callback_data=f"orderreview:{r['id']}:approve"), InlineKeyboardButton('رد سفارش', callback_data=f"orderreview:{r['id']}:reject")]])
                        txt = f"سفارش {r.get('tracking_code') or '#'+str(r['id'])} | کاربر {r['user_id']} | {r['product_name']} | {int(r['amount']):,} تومان"
                        if r.get('receipt_file_id'): await q.message.reply_photo(r['receipt_file_id'], caption=txt, reply_markup=kb)
                        else: await q.message.reply_text(txt, reply_markup=kb)
            elif action == 'coupon': context.user_data['flow'] = 'adm_coupon_code'; await q.message.reply_text('مرحله ۱ از ۴:\nلطفاً یک نام (کد) برای تخفیف وارد کنید (مثلاً: OFF20):')
            elif action == 'finduser': context.user_data['flow'] = 'finduser'; await q.message.reply_text('Telegram ID کاربر را وارد کنید:')
            elif action == 'wallet': context.user_data['flow'] = 'adm_wallet_id'; await q.message.reply_text('مرحله ۱ از ۳:\nآیدی عددی (Telegram ID) کاربر را وارد کنید:')
            elif action == 'settings': await q.message.reply_text('تنظیمات از Environment Variables خوانده می‌شود.')
            elif action == 'orders':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT id,tracking_code,user_id,product_name,amount,status FROM orders ORDER BY id DESC LIMIT 20'); rows = cur.fetchall()
                await q.message.reply_text('\n'.join([f"{r.get('tracking_code') or '#'+str(r['id'])} | {r['user_id']} | {r['product_name']} | {int(r['amount']):,} | {r['status']}" for r in rows]) or 'سفارشی نیست.')
            elif action == 'broadcast': context.user_data['flow'] = 'broadcast'; await q.message.reply_text('متن پیام همگانی را وارد کنید:')
            elif action == 'audit':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT * FROM admin_audit ORDER BY id DESC LIMIT 20'); rows = cur.fetchall()
                await q.message.reply_text('\n'.join([f"{_fa_dt(r['created_at'])} | مدیر {r['admin_id']} | {r['action']} | {r['target']} | {r['details']}" for r in rows]) or 'سابقه‌ای ثبت نشده است.')
            elif action == 'edit_price':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT id, name FROM products WHERE active=true ORDER BY id'); rows = cur.fetchall()
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(r['name'], callback_data=f"chg_price:{r['id']}")] for r in rows])
                await q.message.reply_text('محصولی که می‌خواهید قیمت آن را تغییر دهید انتخاب کنید:', reply_markup=kb)
            elif action == 'edit_links':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT id, name FROM products WHERE active=true ORDER BY id'); rows = cur.fetchall()
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(r['name'], callback_data=f"chg_links:{r['id']}")] for r in rows])
                await q.message.reply_text('محصولی که می‌خواهید لینک‌های آن را تغییر دهید انتخاب کنید:', reply_markup=kb)
            return
        if data.startswith('chg_price:'):
            if not is_super(uid): return
            pid = int(data.split(':')[1])
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT name FROM products WHERE id=%s', (pid,)); p = cur.fetchone()
            context.user_data['flow'] = 'edit_price_text'; context.user_data['edit_price_pid'] = pid
            await q.message.reply_text(f'لطفاً قیمت جدید را برای {p["name"]} به تومان و فقط به صورت عدد وارد کنید:')
            return
        if data.startswith('chg_links:'):
            if not is_super(uid): return
            pid = int(data.split(':')[1])
            context.user_data['flow'] = 'edit_links_text'; context.user_data['edit_links_pid'] = pid
            await q.message.reply_text('لطفاً تمام لینک‌های جدید را (هر لینک در یک خط) ارسال کنید.')
            return
        if data == 'main:back': await start(update, context); return
    except Exception:
        log.exception('Callback handler error')
        try: await q.message.reply_text('خطایی رخ داد.')
        except TelegramError: pass

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    text = (msg.text or '').strip()
    if text in ('❌ لغو', '⬅️ بازگشت', '🔄 اتصال مجدد / شروع مجدد'):
        context.user_data.clear()
        if text == '🔄 اتصال مجدد / شروع مجدد': await start(update, context)
        else: await msg.reply_text('عملیات لغو شد.', reply_markup=main_keyboard())
        return
        
    flow = context.user_data.get('flow')
    if flow == 'reg_name':
        context.user_data['reg_name'] = text; context.user_data['flow'] = 'reg_phone'
        await msg.reply_text('لطفاً **شماره تماس** خود را وارد کنید (مثال: 09131234352):')
        return
    if flow == 'reg_phone':
        phone = text; reg_name = context.user_data.get('reg_name')
        with db() as conn:
            with conn.cursor() as cur: cur.execute('UPDATE users SET reg_name=%s, phone=%s WHERE telegram_id=%s', (reg_name, phone, u.id))
        context.user_data.clear()
        await msg.reply_text('✅ ثبت‌نام شما انجام شد!', reply_markup=main_keyboard())
        return

    if text == '🛍 فروشگاه':
        await msg.reply_text("🛍 **سوپر پک GTA V**\n💵 قیمت: ۷۹۹,۰۰۰ تومان\n\nاز منوی «💰 کیف پول» شارژ کنید.", reply_markup=main_keyboard())
        return
    if text == '💰 کیف پول':
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT wallet FROM users WHERE telegram_id=%s', (u.id,)); row = cur.fetchone()
                cur.execute('SELECT amount,kind,reason,created_at FROM wallet_transactions WHERE user_id=%s ORDER BY id DESC LIMIT 10', (u.id,)); tx = cur.fetchall()
        kind_map = {'purchase': '🛒 خرید', 'topup': '💰 شارژ', 'admin_adjustment': '⚙️ تنظیم'}
        lines = [f"💳 موجودی شما: {int(row['wallet']):,} تومان", '', '📜 آخرین تراکنش‌ها:']
        for r in tx:
            amt = int(r['amount'])
            lines.append(f"┌ {_fa_dt(r['created_at'])}\n├ {kind_map.get(r['kind'], r['kind'])}\n└ {'🟢 +' if amt > 0 else '🔴 '}{abs(amt):,} تومان")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('➕ درخواست شارژ', callback_data='wallet:topup')]])
        await msg.reply_text('\n'.join(lines), reply_markup=kb)
        return
    if text == '👤 حساب کاربری':
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT reg_name, phone, wallet FROM users WHERE telegram_id=%s', (u.id,)); r = cur.fetchone()
        await msg.reply_text(f"👤 **اطلاعات شما**\n📝 {r['reg_name']}\n📱 {r['phone']}\n🆔 {u.id}\n💳 {int(r['wallet']):,} تومان", reply_markup=main_keyboard(), parse_mode='Markdown')
        return
    if text == '📦 سفارش‌های من':
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id,tracking_code,product_name,amount,status,created_at FROM orders WHERE user_id=%s ORDER BY id DESC LIMIT 20", (u.id,)); rows = cur.fetchall()
        if not rows: await msg.reply_text('هنوز سفارشی ندارید.', reply_markup=main_keyboard()); return
        status_map = {'pending':'⏳ در انتظار','approved':'✅ تایید','delivered':'📦 تحویل','rejected':'❌ رد','cancelled':'🚫 لغو'}
        lines = [f"🔢 {r['tracking_code'] or '—'} — {r['product_name']}\n💰 {int(r['amount']):,} تومان | {status_map.get(r['status'], r['status'])}\n{_fa_dt(r['created_at'])}" for r in rows]
        await msg.reply_text('📦 سفارش‌های شما:\n\n' + '\n\n'.join(lines), reply_markup=main_keyboard())
        return
    if text == '📖 آموزش نصب':
        await msg.reply_text(INSTALL_TUTORIAL, reply_markup=main_keyboard(), parse_mode='Markdown'); return
    if text == '🎟 کد تخفیف':
        context.user_data['flow'] = 'coupon_apply'
        await msg.reply_text('کد تخفیف را وارد کنید:', reply_markup=back_kb()); return
    if text == '💬 پشتیبانی':
        context.user_data['flow'] = 'support'
        await msg.reply_text('پیام خود را ارسال کنید.', reply_markup=back_kb()); return
    if text == '🆓 دریافت بیس رایگان':
        await msg.reply_text(f'🎮 **بیس رایگان:**\n\n{BASE_LINK}', reply_markup=main_keyboard(), disable_web_page_preview=True); return
    if text == '🌐 ورود به پورتال':
        if not BASE_URL: await msg.reply_text('⚠️ آدرس پورتال تنظیم نشده.'); return
        with db() as conn:
            with conn.cursor() as cur: connect_code = _create_connect_code(cur, u.id, ttl_minutes=10)
        await msg.reply_text(f"🌐 **ورود به پورتال**\n\n🔑 کد اتصال شما: `{connect_code}`\n⏱ اعتبار: ۱۰ دقیقه\n\n🌐 آدرس: {BASE_URL}/portal\n\n📝 با کد پیگیری سفارشت و کد بالا وارد شو.", parse_mode='Markdown', disable_web_page_preview=True)
        return
    if text == '🔄 دریافت مجدد لینک':
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id, name FROM products WHERE active=true ORDER BY id'); rows = cur.fetchall()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(r['name'], callback_data=f"rlsel:{r['id']}")] for r in rows])
        await msg.reply_text('پکی که خریدید را انتخاب کنید:', reply_markup=kb)
        return
    if text == '🎮 ترینرهای PS4':
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('🔍 جستجوی ترینر', callback_data='trainer_search')], [InlineKeyboardButton('🛠 ساخت ترینر اختصاصی', callback_data='trainer_custom')], [InlineKeyboardButton('📖 راهنمای مشاهده CUSA', callback_data='trainer_cusa_guide')], [InlineKeyboardButton('💳 حمایت مالی', url='https://daramet.com/Amir3243')]])
        await msg.reply_text('🎮 **بخش ترینرهای PS4**', reply_markup=kb, parse_mode='Markdown'); return

    try:
        if flow == 'adm_track' and is_admin(u.id):
            code = text.strip().upper()
            context.user_data.clear()
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT o.*, u.reg_name, u.phone, u.username, u.first_name FROM orders o LEFT JOIN users u ON o.user_id=u.telegram_id WHERE o.tracking_code=%s", (code,))
                    order = cur.fetchone()
            if not order:
                await msg.reply_text('❌ با این کد پیگیری چیزی پیدا نشد.', reply_markup=main_keyboard())
                return
            status_map = {'pending':'⏳ در انتظار','approved':'✅ تایید','rejected':'❌ رد','delivered':'📦 تحویل','cancelled':'🚫 لغو'}
            name = order.get('reg_name') or order.get('first_name') or 'ناشناس'
            uname = f"@{order['username']}" if order.get('username') else 'بدون‌یوزرنیم'
            info = (
                f"📦 **جزئیات سفارش**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🔢 کد پیگیری: {order['tracking_code']}\n"
                f"🆔 شماره: #{order['id']}\n"
                f"👤 نام: {name}\n"
                f"🔗 یوزرنیم: {uname}\n"
                f"🆔 آیدی: {order['user_id']}\n"
                f"📱 تلفن: {order.get('phone') or 'نامشخص'}\n"
                f"📦 محصول: {order['product_name']}\n"
                f"💰 مبلغ: {int(order['amount']):,} تومان\n"
                f"💳 روش: {'کیف پول' if order['payment_method']=='wallet' else 'کارت‌به‌کارت'}\n"
                f"📊 وضعیت: {status_map.get(order['status'], order['status'])}\n"
                f"📅 زمان ثبت: {_fa_dt(order['created_at'])}\n"
                f"📅 زمان بررسی: {_fa_dt(order['reviewed_at']) if order.get('reviewed_at') else '—'}\n"
                f"✅ لینک ارسال شده: {'بله' if order.get('links_sent_via_portal') else 'خیر'}\n"
                f"━━━━━━━━━━━━━━━━━━"
            )
            await msg.reply_text(info, reply_markup=main_keyboard(), parse_mode='Markdown')
            return

        if flow == 'topup_amount':
            amount = Decimal(text); context.user_data['topup_amount'] = amount; context.user_data['flow'] = 'topup_amount_confirm'
            card = os.getenv('CARD_NUMBER', 'تنظیم نشده'); holder = os.getenv('CARD_HOLDER', 'تنظیم نشده')
            msg_text = f"برای شارژ {int(amount):,} تومان، به کارت زیر واریز کنید:\n\n===================\n{card}\n**{holder}**\n===================\n\nبعد از پرداخت رسید را بفرستید."
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ پرداخت کردم | ارسال رسید', callback_data='rcpt:0:topup')]])
            await msg.reply_text(msg_text, reply_markup=kb, parse_mode='Markdown')
            return
        if flow == 'topup_receipt' and msg.photo:
            amount = context.user_data.pop('topup_amount'); file_id = msg.photo[-1].file_id
            with db() as conn:
                with conn.cursor() as cur:
                    tc = _unique_tracking_code(cur, 'topups')
                    cur.execute('INSERT INTO topups(user_id,amount,receipt_file_id,tracking_code) VALUES(%s,%s,%s,%s) RETURNING id', (u.id, amount, file_id, tc)); rid = cur.fetchone()['id']
                    cur.execute('SELECT reg_name, phone FROM users WHERE telegram_id=%s', (u.id,)); uinfo = cur.fetchone()
                    cur.execute('SELECT created_at FROM topups WHERE id=%s', (rid,)); created = cur.fetchone()['created_at']
            context.user_data.clear()
            await msg.reply_text(f'⏳ شارژ ثبت شد.\n🔢 کد پیگیری: {tc}', reply_markup=main_keyboard())
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ تایید', callback_data=f'topup:{rid}:approve'), InlineKeyboardButton('❌ رد', callback_data=f'topup:{rid}:reject')]])
            user_info = f"{uinfo['reg_name']} | @{u.username or 'بدون‌یوزرنیم'} | {u.id} | تلفن: {uinfo['phone']}"
            try: await context.bot.send_photo(SUPER_ADMIN, file_id, caption=f'💳 شارژ\n🔢 {tc}\n👤 {user_info}\n💰 {int(amount):,} تومان', reply_markup=kb)
            except TelegramError: pass
            cap = f"🧾 رسید شارژ — در انتظار\n━━━━━━━━━━━━━━━━━━\n🔢 کد پیگیری: {tc}\n👤 نام: {uinfo['reg_name']}\n🔗 یوزرنیم: @{u.username or 'بدون‌یوزرنیم'}\n🆔 آیدی: {u.id}\n📱 تلفن: {uinfo['phone']}\n💰 مبلغ: {int(amount):,} تومان\n📅 زمان تراکنش: {_fa_dt(created)}\n━━━━━━━━━━━━━━━━━━\n⏳ وضعیت: در انتظار بررسی"
            msg_id = await log_photo(context, file_id, cap)
            if msg_id:
                with db() as conn:
                    with conn.cursor() as cur: cur.execute('UPDATE topups SET receipt_message_id=%s WHERE id=%s', (msg_id, rid))
            return
        if flow == 'order_receipt' and msg.photo:
            rid = context.user_data.get('order_id'); file_id = msg.photo[-1].file_id
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET receipt_file_id=%s WHERE id=%s AND user_id=%s AND status='pending' RETURNING id,amount,product_name,tracking_code,created_at", (file_id, rid, u.id)); r = cur.fetchone()
                    cur.execute('SELECT reg_name, phone FROM users WHERE telegram_id=%s', (u.id,)); uinfo = cur.fetchone()
            context.user_data.clear()
            if not r: await msg.reply_text('سفارش یافت نشد.'); return
            tc = r['tracking_code'] or '—'
            await msg.reply_text(f'⏳ رسید سفارش دریافت شد.\n🔢 {tc}', reply_markup=main_keyboard())
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ تایید', callback_data=f'orderreview:{rid}:approve'), InlineKeyboardButton('❌ رد', callback_data=f'orderreview:{rid}:reject')]])
            user_info = f"{uinfo['reg_name']} | @{u.username or 'بدون‌یوزرنیم'} | {u.id} | تلفن: {uinfo['phone']}"
            try: await context.bot.send_photo(SUPER_ADMIN, file_id, caption=f"📦 رسید سفارش\n🔢 {tc}\n👤 {user_info}\n🛍 {r['product_name']}\n💰 {int(r['amount']):,} تومان", reply_markup=kb)
            except TelegramError: pass
            cap = f"🧾 رسید سفارش — در انتظار\n━━━━━━━━━━━━━━━━━━\n🔢 کد پیگیری: {tc}\n👤 نام: {uinfo['reg_name']}\n🔗 یوزرنیم: @{u.username or 'بدون‌یوزرنیم'}\n🆔 آیدی: {u.id}\n📱 تلفن: {uinfo['phone']}\n📦 محصول: {r['product_name']}\n💰 مبلغ: {int(r['amount']):,} تومان\n📅 {_fa_dt(r['created_at'])}\n━━━━━━━━━━━━━━━━━━\n⏳ در انتظار بررسی"
            msg_id = await log_photo(context, file_id, cap)
            if msg_id:
                with db() as conn:
                    with conn.cursor() as cur: cur.execute('UPDATE orders SET receipt_message_id=%s WHERE id=%s', (msg_id, rid))
            return
        if flow == 'coupon_apply':
            code = text.upper()
            with db() as conn:
                with conn.cursor() as cur: cur.execute("SELECT * FROM coupons WHERE code=%s AND active=true", (code,)); cp = cur.fetchone()
            if not cp: await msg.reply_text('کد نامعتبر.'); return
            context.user_data.update(coupon_code=code, flow=None)
            await msg.reply_text('✅ کد تخفیف ذخیره شد.', reply_markup=main_keyboard())
            return
        if flow == 'support':
            file_id = msg.photo[-1].file_id if msg.photo else None
            body = text if text else (msg.caption or '')
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO support_messages(user_id,direction,message_text,file_id) VALUES(%s,'user_to_admin',%s,%s) RETURNING id", (u.id, body, file_id)); sid = cur.fetchone()['id']
            context.user_data.clear()
            await msg.reply_text('پیام ارسال شد.', reply_markup=main_keyboard())
            for aid in ADMIN_IDS:
                try:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton('پاسخ', callback_data=f'supportreply:{u.id}')]])
                    if file_id: await context.bot.send_photo(aid, file_id, caption=f'پشتیبانی از {u.id}\n{body}', reply_markup=kb)
                    else: await context.bot.send_message(aid, f'پشتیبانی از {u.id}\n{body}', reply_markup=kb)
                except TelegramError: pass
            return
        await msg.reply_text('از منو انتخاب کنید.', reply_markup=main_keyboard())
    except Exception:
        log.exception('Message handler error')

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error('Error', exc_info=context.error)

def build_application() -> Application:
    app_inst = Application.builder().token(BOT_TOKEN).build()
    app_inst.add_handler(CommandHandler('start', start))
    app_inst.add_handler(CallbackQueryHandler(callback))
    app_inst.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
    app_inst.add_error_handler(error_handler)
    return app_inst

def main():
    global _bot_application
    init_db()
    _bot_application = build_application()
    log.info('Bot starting')
    _bot_application.run_polling(drop_pending_updates=False, close_loop=False, stop_signals=None)

_bot_thread = None
def run_bot_thread():
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
        main()
    except Exception: log.exception('Bot stopped')

def _start_bot_thread_once():
    global _bot_thread
    if _bot_thread is not None and _bot_thread.is_alive(): return
    _bot_thread = threading.Thread(target=run_bot_thread, name='telegram-bot', daemon=True)
    _bot_thread.start()

# ==================== API ====================
def _get_user_from_token(cur, token: str) -> Optional[dict]:
    if not token: return None
    cur.execute("SELECT ws.*, u.reg_name, u.phone, u.username, u.first_name, u.wallet FROM web_sessions ws JOIN users u ON ws.user_id=u.telegram_id WHERE ws.session_token=%s AND ws.expires_at > now()", (token,))
    session = cur.fetchone()
    if session: cur.execute("UPDATE web_sessions SET last_used=now() WHERE id=%s", (session['id'],))
    return session

def _extract_token() -> str:
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '): return auth[7:].strip()
    return ''

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    try:
        data = request.get_json(silent=True) or {}
        order_code = (data.get('order_code') or '').strip().upper()
        connect_code = (data.get('connect_code') or '').strip()
        if not order_code or not connect_code: return jsonify(success=False, error='کدها الزامی هستند.'), 400
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM connect_codes WHERE code=%s AND used=false AND expires_at > now()", (connect_code,))
                cc = cur.fetchone()
                if not cc: return jsonify(success=False, error='کد اتصال منقضی یا نامعتبر.'), 401
                user_id = cc['user_id']
                cur.execute("SELECT * FROM orders WHERE tracking_code=%s AND user_id=%s AND status IN ('approved','delivered')", (order_code, user_id))
                order = cur.fetchone()
                if not order: return jsonify(success=False, error='سفارش یافت نشد یا متعلق به شما نیست.'), 401
                cur.execute("UPDATE connect_codes SET used=true WHERE id=%s", (cc['id'],))
                token = _gen_session_token()
                expires = datetime.now(timezone.utc) + timedelta(hours=24)
                cur.execute("INSERT INTO web_sessions(user_id, session_token, expires_at) VALUES(%s, %s, %s)", (user_id, token, expires))
                cur.execute("SELECT reg_name, first_name FROM users WHERE telegram_id=%s", (user_id,))
                uinfo = cur.fetchone()
                name = (uinfo.get('reg_name') or uinfo.get('first_name') or 'کاربر') if uinfo else 'کاربر'
        return jsonify(success=True, session_token=token, user_name=name)
    except Exception: return jsonify(success=False, error='خطای سرور'), 500

@app.route('/api/me', methods=['GET'])
def api_me():
    try:
        with db() as conn:
            with conn.cursor() as cur:
                session = _get_user_from_token(cur, _extract_token())
                if not session: return jsonify(success=False, error='نشست منقضی شده'), 401
                return jsonify(success=True, user_name=session.get('reg_name') or session.get('first_name'), user_id=session['user_id'])
    except Exception: return jsonify(success=False, error='خطای سرور'), 500

@app.route('/api/my-orders', methods=['GET'])
def api_my_orders():
    try:
        with db() as conn:
            with conn.cursor() as cur:
                session = _get_user_from_token(cur, _extract_token())
                if not session: return jsonify(success=False, error='نشست منقضی شده'), 401
                cur.execute("SELECT id, tracking_code, product_name, amount, status, created_at, links_sent_via_portal, links_expires_at, is_upgrade FROM orders WHERE user_id=%s AND status IN ('approved','delivered') AND product_name NOT LIKE 'دریافت مجدد%%' ORDER BY created_at DESC", (session['user_id'],))
                rows = cur.fetchall()
        orders = [{'id': r['id'], 'tracking_code': r['tracking_code'], 'product_name': r['product_name'], 'amount': int(r['amount']), 'status': r['status'], 'created_at': _fa_dt(r['created_at']), 'links_sent': bool(r.get('links_sent_via_portal')), 'links_expires_at': _fa_dt(r.get('links_expires_at')) if r.get('links_expires_at') else None} for r in rows]
        return jsonify(success=True, orders=orders)
    except Exception: return jsonify(success=False, error='خطای سرور'), 500

@app.route('/api/request-links', methods=['POST'])
def api_request_links():
    try:
        data = request.get_json(silent=True) or {}
        order_id = data.get('order_id')
        if not order_id: return jsonify(success=False, error='شناسه سفارش لازمه'), 400
        
        with db() as conn:
            with conn.cursor() as cur:
                session = _get_user_from_token(cur, _extract_token())
                if not session: return jsonify(success=False, error='نشست منقضی'), 401
                user_id = session['user_id']
                cur.execute("SELECT * FROM orders WHERE id=%s AND user_id=%s AND status IN ('approved','delivered')", (order_id, user_id))
                order = cur.fetchone()
                if not order: return jsonify(success=False, error='سفارش یافت نشد'), 404
                if order.get('links_sent_via_portal'): return jsonify(success=False, error='قبلاً ارسال شده. با پشتیبانی تماس بگیرید.'), 400
                links_exp = order.get('links_expires_at')
                if links_exp and links_exp < datetime.now(timezone.utc): return jsonify(success=False, error='مهلت ۵ روزه تمام شده'), 400
                
                clean_name = order['product_name'].replace('دریافت مجدد ', '')
                cur.execute("SELECT pl.link FROM product_links pl JOIN products p ON pl.product_id = p.id WHERE p.name ILIKE %s ORDER BY pl.id", (f"%{clean_name}%",))
                links = [r['link'] for r in cur.fetchall()]
                if not links: return jsonify(success=False, error='لینکی تعریف نشده. با پشتیبانی تماس بگیرید.'), 500
        
        global _bot_application
        if _bot_application and _bot_application.bot:
            try:
                expires_str = _fa_dt(order.get('links_expires_at')) if order.get('links_expires_at') else '—'
                
                header = (
                    f"📦 **لینک‌های {clean_name}**\n\n"
                    f"🔢 کد پیگیری: {order['tracking_code']}\n\n"
                    f"🔒 **رمز فایل:** `t.me/rippermods`\n\n"
                    f"⚠️ این لینک‌ها بعد از {expires_str} منقضی می‌شن.\n"
                    f"⚠️ فقط یک بار قابل دریافت هستن.\n\n"
                )
                await _bot_application.bot.send_message(user_id, header, parse_mode='Markdown')
                
                file_content = (
                    f"🔒 رمز فایل: t.me/rippermods\n"
                    f"⚠️ این لینک‌ها بعد از {expires_str} منقضی می‌شن.\n\n"
                    + "\n".join(links)
                )
                file_io = io.BytesIO(file_content.encode('utf-8'))
                file_io.name = f"RipperMods_{order['tracking_code']}.txt"
                await _bot_application.bot.send_document(
                    user_id,
                    document=InputFile(file_io, filename=f"RipperMods_{order['tracking_code']}.txt"),
                    caption=f"✅ لینک‌های {clean_name}"
                )
                
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE orders SET links_sent_via_portal=true WHERE id=%s", (order_id,))
                        
            except Exception as e:
                log.exception(f'Failed to send links via telegram: {e}')
                return jsonify(success=False, error=f'خطا در ارسال به تلگرام: {str(e)}'), 500
        
        audit(user_id, 'links_requested_portal', str(order_id), clean_name)
        return jsonify(success=True, message='لینک‌ها به تلگرام شما ارسال شدن. چت ربات رو چک کنید.')
    except Exception as e:
        log.exception('api_request_links error')
        return jsonify(success=False, error=f'خطای سرور: {str(e)}'), 500

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    try:
        token = _extract_token()
        if token:
            with db() as conn:
                with conn.cursor() as cur: cur.execute("DELETE FROM web_sessions WHERE session_token=%s", (token,))
        return jsonify(success=True)
    except Exception: return jsonify(success=True)

if __name__ == '__main__':
    try: init_db()
    except Exception: log.exception('init_db failed')
    _start_bot_thread_once()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '10000')), use_reloader=False)
else:
    try: init_db()
    except Exception: log.exception('init_db failed')
    _start_bot_thread_once()
