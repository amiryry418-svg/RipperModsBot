"""RipperMods Persian Telegram shop. PostgreSQL-backed, deployable on Render."""
from __future__ import annotations
import os, logging, threading, time, asyncio, io, re, random, string, requests
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.error import TelegramError
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters)

try:
    import jdatetime
except ImportError:
    jdatetime = None

logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'), format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('rippermods')
app = Flask(__name__)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')
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
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None

PORT_ADMIN = _optional_int_env('PORT_ADMIN')
ADMIN3 = _optional_int_env('ADMIN3')
DELETE_DELIVERY = os.getenv('DELETE_DELIVERY_AFTER_30_SECONDS', 'true').lower() in ('1', 'true', 'yes')

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


def _fa_dt(dt) -> str:
    """Format datetime as Jalali (Shamsi) date or fallback."""
    if dt is None:
        return '—'
    try:
        if jdatetime is not None:
            j = jdatetime.datetime.fromgregorian(datetime=dt)
            return j.strftime('%Y/%m/%d - %H:%M')
        return dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        try:
            return dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            return str(dt)


def _gen_tracking_code(prefix: str = 'RM') -> str:
    """Generate a random tracking code like RM-4827."""
    return f"{prefix}-{random.randint(1000, 9999)}"


def _unique_tracking_code(cur, table: str) -> str:
    """Generate a unique tracking code for given table (orders/topups)."""
    for _ in range(20):
        code = _gen_tracking_code()
        cur.execute(f"SELECT 1 FROM {table} WHERE tracking_code=%s", (code,))
        if not cur.fetchone():
            return code
    return f"RM-{int(time.time()) % 9000 + 1000}"


def _get_decimal_env(name: str, default: str = '0') -> Decimal:
    raw = (os.getenv(name, default) or default).strip()
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal(default)

@app.get('/health')
def health():
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
        return jsonify(status='ok', database='connected'), 200
    except Exception:
        return jsonify(status='degraded', database='unavailable'), 503

@contextmanager
def db():
    if not DATABASE_URL:
        raise RuntimeError('متغیر DATABASE_URL تنظیم نشده است.')
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=8, sslmode='require', cursor_factory=RealDictCursor)
    try:
        with conn:
            yield conn
    finally:
        conn.close()

def load_ps4_trainers_from_files():
    files = {'json': 'json.txt', 'mc4': 'mc4.txt', 'shn': 'shn.txt'}
    total = 0
    with db() as conn:
        with conn.cursor() as cur:
            for fmt, fname in files.items():
                if not os.path.exists(fname):
                    log.warning(f"File {fname} not found, skipping.")
                    continue
                with open(fname, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or '=' not in line:
                            continue
                        left, right = line.split('=', 1)
                        left = left.strip()
                        game_name = right.strip()
                        m = re.match(r'^(CUSA\d+)_(\d+[.\d]*)[._]', left)
                        if not m:
                            continue
                        cusa_id = m.group(1)
                        version = m.group(2)
                        
                        if fmt == 'mc4':
                            file_url = f"https://raw.githubusercontent.com/GoldHEN/GoldHEN_Cheat_Repository/main/mc4/{left}"
                        elif fmt == 'json':
                            file_url = f"https://raw.githubusercontent.com/GoldHEN/GoldHEN_Cheat_Repository/main/json/{left}"
                        else:
                            file_url = f"https://raw.githubusercontent.com/GoldHEN/GoldHEN_Cheat_Repository/main/shn/{left}"
                        
                        try:
                            cur.execute("""
                                INSERT INTO ps4_trainers(cusa_id, version, file_format, game_name, file_url)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (cusa_id, version, file_format) DO NOTHING
                            """, (cusa_id, version, fmt, game_name, file_url))
                            if cur.rowcount > 0:
                                total += 1
                        except Exception as e:
                            log.warning(f"Skip {left}: {e}")
    log.info(f"Loaded {total} PS4 trainers into DB.")

def init_db():
    global _db_initialized
    with _db_lock:
        if _db_initialized:
            return
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
            """CREATE TABLE IF NOT EXISTS ps4_trainers (
                id BIGSERIAL PRIMARY KEY,
                cusa_id TEXT NOT NULL,
                version TEXT NOT NULL,
                file_format TEXT NOT NULL,
                game_name TEXT NOT NULL,
                file_url TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(cusa_id, version, file_format)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
            "CREATE INDEX IF NOT EXISTS idx_topups_status ON topups(status)",
            "CREATE INDEX IF NOT EXISTS idx_nostalgia_status ON nostalgia_requests(status)",
            "CREATE INDEX IF NOT EXISTS idx_support_user_created ON support_messages(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_ps4_trainers_cusa ON ps4_trainers(cusa_id)",
        ]
        with db() as conn:
            with conn.cursor() as cur:
                for sql in schema:
                    cur.execute(sql)
                
                # --- پاکسازی و ایمن‌سازی محصولات ---
                cur.execute("DELETE FROM product_links WHERE product_id NOT IN (SELECT MIN(id) FROM products GROUP BY name)")
                cur.execute("DELETE FROM products WHERE id NOT IN (SELECT MIN(id) FROM products GROUP BY name)")
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_name_unique ON products (name)")
                cur.execute("INSERT INTO products(name,description,price,active) VALUES (%s,%s,%s,true) ON CONFLICT (name) DO NOTHING", ('سوپر پک', 'بسته ویژه RipperMods', 799000))
                cur.execute("INSERT INTO products(name,description,price,active,upgrade_price) VALUES (%s,%s,%s,true,%s) ON CONFLICT (name) DO NOTHING", ('پک ایرانیزه', 'نسخه ایرانیزه شده GTA V (V2)', 799000, 350000))
                
                # لینک‌های سوپر پک
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
    except Exception:
        log.exception('audit failed')


async def log_event(context: ContextTypes.DEFAULT_TYPE, text: str):
    if not LOG_CHANNEL_ID: return
    try:
        await context.bot.send_message(LOG_CHANNEL_ID, text[:3900])
    except TelegramError:
        pass


async def log_photo(context: ContextTypes.DEFAULT_TYPE, photo_file_id: str, caption: str) -> Optional[int]:
    """Send photo to log channel, return message_id."""
    if not LOG_CHANNEL_ID: return None
    try:
        m = await context.bot.send_photo(LOG_CHANNEL_ID, photo_file_id, caption=caption[:1000])
        return m.message_id
    except TelegramError:
        return None


async def log_reply_to(context: ContextTypes.DEFAULT_TYPE, reply_to_message_id: int, text: str, photo_file_id: Optional[str] = None, caption: Optional[str] = None) -> bool:
    """Reply to a message in log channel. If photo given and reply fails, send new."""
    if not LOG_CHANNEL_ID: return False
    try:
        if photo_file_id and caption:
            await context.bot.send_photo(LOG_CHANNEL_ID, photo_file_id, caption=caption[:1000], reply_to_message_id=reply_to_message_id)
            return True
        else:
            await context.bot.send_message(LOG_CHANNEL_ID, text[:3900], reply_to_message_id=reply_to_message_id)
            return True
    except TelegramError:
        return False


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def is_super(uid: int) -> bool:
    return uid == SUPER_ADMIN

def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton('🛍 فروشگاه'), KeyboardButton('💰 کیف پول')],
        [KeyboardButton('📦 سفارش‌های من'), KeyboardButton('👤 حساب کاربری')],
        [KeyboardButton('🎟 کد تخفیف'), KeyboardButton('📖 آموزش نصب')],
        [KeyboardButton('🎮 ترینرهای PS4')],
        [KeyboardButton('🎮 درخواست پورت'), KeyboardButton('📦 درخواست پک اختصاصی')],
        [KeyboardButton('💬 پشتیبانی'), KeyboardButton('🆓 دریافت بیس رایگان')],
        [KeyboardButton('🔄 دریافت مجدد لینک'), KeyboardButton('🔄 اتصال مجدد / شروع مجدد')]
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
        if photo:
            await update.effective_message.reply_photo(photo, caption=welcome, reply_markup=main_keyboard())
        else:
            await update.effective_message.reply_text(welcome, reply_markup=main_keyboard())
    except TelegramError:
        await update.effective_message.reply_text(welcome, reply_markup=main_keyboard())
    if is_admin(u.id):
        await update.effective_message.reply_text('پنل مدیریت:', reply_markup=admin_kb())


async def show_shop(update, context):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('🛍 سوپر پک', callback_data='shop:super_pack')],
        [InlineKeyboardButton('📦 پک ایرانیزه', callback_data='shop:iranian_pack')]
    ])
    await update.effective_message.reply_text('لطفاً یکی از پک‌های زیر را انتخاب کنید:', reply_markup=kb)


async def show_product_details(update, context, p_name):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM products WHERE name=%s AND active=true', (p_name,))
            p = cur.fetchone()
    if not p:
        await update.effective_message.reply_text('محصول یافت نشد یا غیرفعال است.', reply_markup=main_keyboard())
        return

    base_price = Decimal(p['price'])
    final_price = base_price
    
    coupon_code = context.user_data.get('coupon_code')
    if coupon_code:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM coupons WHERE code=%s AND active=true AND (expires_at IS NULL OR expires_at>now())", (coupon_code.upper(),))
                cp = cur.fetchone()
        if cp and cp['discount_type'] == 'percent':
            discount = base_price * (cp['value'] / Decimal(100))
            final_price = base_price - discount
            final_price = max(Decimal('0'), final_price).quantize(Decimal('0.01'))

    text = f"🛍 {p['name']}\n{p['description']}\n"
    if coupon_code and final_price < base_price:
        text += f"❌ قیمت اصلی: {int(base_price):,} تومان\n"
        text += f"✅ قیمت با تخفیف ({coupon_code}): {int(final_price):,} تومان"
    else:
        text += f"💵 قیمت: {int(final_price):,} تومان"
        
    if p['name'] == 'پک ایرانیزه':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('خرید با کیف پول', callback_data=f"buyw:{p['id']}"), InlineKeyboardButton('پرداخت کارت‌به‌کارت', callback_data=f"buyc:{p['id']}")],
            [InlineKeyboardButton('🚀 ارتقا از V1', callback_data=f"upgrade:{p['id']}")],
            [InlineKeyboardButton('🎟 وارد کردن کد تخفیف', callback_data=f"appcpn:{p['id']}")]
        ])
        photo = 'https://s31.uupload.ir/files/rippermodsgtavv100/Amir/4/IMG_20261021_205807_285.jpg'
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('خرید با کیف پول', callback_data=f"buyw:{p['id']}"), InlineKeyboardButton('پرداخت کارت‌به‌کارت', callback_data=f"buyc:{p['id']}")],
            [InlineKeyboardButton('🎟 وارد کردن کد تخفیف', callback_data=f"appcpn:{p['id']}")]
        ])
        photo = 'https://s50.uupload.ir/files/rippermodsgtavv100/Amir/IMG_20261021_205811_651.jpg' if p['name'] == 'سوپر پک' else ''
        
    try:
        if photo:
            await update.effective_message.reply_photo(photo, caption=text, reply_markup=kb)
        else:
            await update.effective_message.reply_text(text, reply_markup=kb)
    except TelegramError:
        await update.effective_message.reply_text(text, reply_markup=kb)


async def wallet(update, context):
    uid = update.effective_user.id
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT wallet FROM users WHERE telegram_id=%s', (uid,))
            row = cur.fetchone()
            cur.execute('SELECT amount,kind,reason,created_at FROM wallet_transactions WHERE user_id=%s ORDER BY id DESC LIMIT 10', (uid,))
            tx = cur.fetchall()
    
    kind_map = {
        'purchase': '🛒 خرید',
        'topup': '💰 شارژ',
        'admin_adjustment': '⚙️ تنظیم موجودی'
    }
    
    lines = [f"💳 موجودی شما: {int(row['wallet']):,} تومان", '', '📜 آخرین تراکنش‌ها:']
    
    if not tx:
        lines.append('هنوز تراکنشی ثبت نشده است.')
    else:
        for r in tx:
            k_fa = kind_map.get(r['kind'], r['kind'])
            reason_txt = f" ({r['reason']})" if r['reason'] else ""
            amt = int(r['amount'])
            sign = "🟢 +" if amt > 0 else "🔴 "
            amt_str = f"{sign}{abs(amt):,}"
            
            lines.append(f"┌ {_fa_dt(r['created_at'])}")
            lines.append(f"├ {k_fa}{reason_txt}")
            lines.append(f"└ {amt_str} تومان")
            
    kb = InlineKeyboardMarkup([[InlineKeyboardButton('➕ درخواست شارژ', callback_data='wallet:topup')]])
    await update.effective_message.reply_text('\n'.join(lines), reply_markup=kb)


async def account_info(update, context):
    uid = update.effective_user.id
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT reg_name, phone, wallet FROM users WHERE telegram_id=%s', (uid,))
            r = cur.fetchone()
    if not r:
        await update.effective_message.reply_text('اطلاعات حساب یافت نشد. لطفاً /start را بزنید.', reply_markup=main_keyboard())
        return
    text = (
        f"👤 **اطلاعات حساب کاربری شما**\n\n"
        f"📝 نام و نام خانوادگی: {r['reg_name']}\n"
        f"📱 شماره تماس: {r['phone']}\n"
        f"🆔 آیدی عددی: {uid}\n"
        f"💳 موجودی کیف پول: {int(r['wallet']):,} تومان"
    )
    await update.effective_message.reply_text(text, reply_markup=main_keyboard(), parse_mode='Markdown')


async def orders_for_user(update, context):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE orders SET status='cancelled', updated_at=now() WHERE status='pending' AND created_at < now() - interval '24 hours'")
            cur.execute("SELECT id,tracking_code,product_name,amount,payment_method,status,created_at FROM orders WHERE user_id=%s AND product_name NOT LIKE 'دریافت مجدد%%' ORDER BY id DESC LIMIT 20", (update.effective_user.id,))
            rows = cur.fetchall()
    if not rows:
        await update.effective_message.reply_text('هنوز سفارشی ندارید.', reply_markup=main_keyboard())
        return
    
    status_map = {
        'pending': '⏳ در حال انجام',
        'approved': '✅ تایید شده',
        'delivered': '📦 موفق (تحویل داده شد)',
        'rejected': '❌ رد شده',
        'cancelled': '🚫 لغو شده (مهلت پرداخت تمام شد)'
    }
    
    lines = []
    for r in rows:
        st_text = status_map.get(r['status'], r['status'])
        tc = r['tracking_code'] or '—'
        lines.append(f"🔢 کد پیگیری: {tc}\n#{r['id']} — {r['product_name']} — {int(r['amount']):,} تومان\nوضعیت: {st_text} | {_fa_dt(r['created_at'])}")
        
    text = '📦 سفارش‌های شما:\n\n' + '\n\n'.join(lines)
    await update.effective_message.reply_text(text, reply_markup=main_keyboard())


async def begin_topup(update, context):
    context.user_data['flow'] = 'topup_amount'
    await update.effective_message.reply_text(
        'لطفاً مبلغ شارژ را به تومان و فقط به صورت عدد وارد کنید.\n'
        'مثال: 799000', 
        reply_markup=back_kb()
    )


async def begin_topup_from_callback(q, context):
    context.user_data['flow'] = 'topup_amount'
    await q.message.reply_text(
        'لطفاً مبلغ شارژ را به تومان و فقط به صورت عدد وارد کنید.\n'
        'مثال: 799000', 
        reply_markup=back_kb()
    )


def _build_receipt_caption(kind_label: str, tracking: str, user_info: dict, amount: Decimal, extra_line: str, created_at, status_line: str) -> str:
    return (
        f"🧾 {kind_label}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔢 کد پیگیری: {tracking}\n"
        f"👤 نام: {user_info.get('reg_name') or user_info.get('first_name') or 'ناشناس'}\n"
        f"🔗 یوزرنیم: @{user_info.get('username')}\n" if user_info.get('username') else
        f"🧾 {kind_label}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔢 کد پیگیری: {tracking}\n"
        f"👤 نام: {user_info.get('reg_name') or user_info.get('first_name') or 'ناشناس'}\n"
        f"🔗 یوزرنیم: بدون‌یوزرنیم\n"
    ) + (
        f"🆔 آیدی: {user_info.get('telegram_id', '—')}\n"
        f"📱 تلفن: {user_info.get('phone') or 'نامشخص'}\n"
        f"💰 مبلغ: {int(amount):,} تومان\n"
        f"{extra_line}"
        f"📅 زمان تراکنش: {_fa_dt(created_at)}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{status_line}"
    )


async def review_topup(q, context, rid, decision, admin_id):
    uinfo = {}
    tracking = '—'
    created_at = None
    amount = Decimal('0')
    receipt_msg_id = None
    receipt_file_id = None
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
    
    # پیام ساده و کوتاه به خریدار
    try:
        if decision == 'approve':
            await context.bot.send_message(r['user_id'], f"✅ شارژ کیف پول شما تایید شد.\n🔢 کد پیگیری: {tracking}\n💰 مبلغ: {int(amount):,} تومان\n\nموجودی کیف پول شما به‌روزرسانی شد.")
        else:
            await context.bot.send_message(r['user_id'], f"❌ درخواست شارژ شما رد شد.\n🔢 کد پیگیری: {tracking}\n\nبرای پیگیری با پشتیبانی تماس بگیرید.")
    except TelegramError:
        pass
    
    await q.message.reply_text('بررسی ثبت شد.')
    
    # لاگ کامل توی چنل
    status_fa = '✅ تایید شد' if decision == 'approve' else '❌ رد شد'
    name = uinfo.get('reg_name') or uinfo.get('first_name') or 'ناشناس'
    uname = f"@{uinfo['username']}" if uinfo.get('username') else 'بدون‌یوزرنیم'
    phone = uinfo.get('phone') or 'نامشخص'
    body = (
        f"{status_fa} — شارژ کیف پول\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔢 کد پیگیری: {tracking}\n"
        f"👤 نام: {name}\n"
        f"🔗 یوزرنیم: {uname}\n"
        f"🆔 آیدی: {r['user_id']}\n"
        f"📱 تلفن: {phone}\n"
        f"💰 مبلغ: {int(amount):,} تومان\n"
        f"📅 زمان تراکنش: {_fa_dt(created_at)}\n"
        f"📅 زمان بررسی: {_fa_dt(None) if False else _fa_dt_jnow()}\n"
        f"👮 مدیر: ادمین اصلی\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    replied = False
    if receipt_msg_id:
        replied = await log_reply_to(context, receipt_msg_id, body)
    if not replied:
        if receipt_file_id:
            try:
                await context.bot.send_photo(LOG_CHANNEL_ID, receipt_file_id, caption=body[:1000])
            except TelegramError:
                pass


async def review_order(q, context, rid, decision, admin_id):
    uinfo = {}
    tracking = '—'
    created_at = None
    amount = Decimal('0')
    product_name = '—'
    receipt_msg_id = None
    receipt_file_id = None
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
                if cur.rowcount:
                    cur.execute('UPDATE coupons SET uses=uses+1 WHERE id=%s', (r['coupon_id'],))
            cur.execute('SELECT telegram_id, reg_name, phone, username, first_name FROM users WHERE telegram_id=%s', (r['user_id'],))
            uinfo = cur.fetchone() or {}
            uinfo['telegram_id'] = r['user_id']
            tracking = r.get('tracking_code') or '—'
            created_at = r['created_at']
            amount = r['amount']
            product_name = r['product_name']
            receipt_msg_id = r.get('receipt_message_id')
            receipt_file_id = r['receipt_file_id']
    audit(admin_id, 'order_review', str(rid), decision)
    await q.message.reply_text('بررسی سفارش ثبت شد.')
    
    # پیام ساده و کوتاه به خریدار
    if decision == 'approve':
        await deliver(context, r['user_id'], rid, product_name)
    else:
        try:
            await context.bot.send_message(r['user_id'], f"❌ سفارش شما رد شد.\n🔢 کد پیگیری: {tracking}\n\nبرای پیگیری با پشتیبانی تماس بگیرید.")
        except TelegramError:
            pass
    
    status_fa = '✅ تایید شد' if decision == 'approve' else '❌ رد شد'
    name = uinfo.get('reg_name') or uinfo.get('first_name') or 'ناشناس'
    uname = f"@{uinfo['username']}" if uinfo.get('username') else 'بدون‌یوزرنیم'
    phone = uinfo.get('phone') or 'نامشخص'
    body = (
        f"{status_fa} — سفارش\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔢 کد پیگیری: {tracking}\n"
        f"👤 نام: {name}\n"
        f"🔗 یوزرنیم: {uname}\n"
        f"🆔 آیدی: {r['user_id']}\n"
        f"📱 تلفن: {phone}\n"
        f"📦 محصول: {product_name}\n"
        f"💰 مبلغ: {int(amount):,} تومان\n"
        f"📅 زمان تراکنش: {_fa_dt(created_at)}\n"
        f"📅 زمان بررسی: {_fa_dt_jnow()}\n"
        f"👮 مدیر: ادمین اصلی\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    replied = False
    if receipt_msg_id:
        replied = await log_reply_to(context, receipt_msg_id, body)
    if not replied:
        if receipt_file_id:
            try:
                await context.bot.send_photo(LOG_CHANNEL_ID, receipt_file_id, caption=body[:1000])
            except TelegramError:
                pass


def _fa_dt_jnow() -> str:
    from datetime import datetime, timezone
    return _fa_dt(datetime.now(timezone.utc))


async def deliver(context, uid, order_id, product_name):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE orders SET status='delivered',delivery_sent=true,updated_at=now() WHERE id=%s AND status IN ('approved', 'delivered') AND delivery_sent=false RETURNING id", (order_id,))
            row = cur.fetchone()
    if not row:
        log.warning(f"Order {order_id} not delivered: row not found or already sent.")
        return

    clean_name = product_name.replace('دریافت مجدد ', '')
    
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pl.link FROM product_links pl JOIN products p ON pl.product_id = p.id WHERE p.name ILIKE %s ORDER BY pl.id", (f"%{clean_name}%",))
            links = [r['link'] for r in cur.fetchall()]

    if not links:
        try:
            await context.bot.send_message(uid, f'❌ متأسفانه لینک‌های دانلود برای {clean_name} در حال حاضر در دسترس نیست. لطفاً با پشتیبانی تماس بگیرید.')
        except TelegramError:
            pass
        return

    msg_header = f'🎉 محصول شما آماده تحویل است!\nسفارش: #{order_id}\nمحصول: {clean_name}\n\n'
    msg_header += '⚠️ توجه: لینک‌های دانلود به صورت یک فایل متنی در پیام بعدی برای شما ارسال می‌شود.\n'
    msg_header += '⚠️ این لینک‌ها فقط تا ۵ روز آینده قابل استفاده هستند.'

    try:
        await context.bot.send_message(uid, msg_header)
    except TelegramError as e:
        log.error(f"Failed to send header to {uid}: {e}")
        return

    file_content = "⚠️ این لینک‌ها فقط تا 5 روز آینده قابل استفاده هستند.\n\n" + "\n".join(links)
    file_io = io.BytesIO(file_content.encode('utf-8'))
    file_io.name = f"RipperMods_Links.txt"

    try:
        await context.bot.send_document(
            uid, 
            document=InputFile(file_io, filename=f"RipperMods_Links.txt"), 
            caption=f"✅ این فایل حاوی لینک‌های {clean_name} است."
        )
    except TelegramError as e:
        log.error(f"Failed to send file to {uid}: {e}")
        try:
            await context.bot.send_message(uid, "❌ متأسفانه ارسال فایل لینک‌ها با خطا مواجه شد. لطفاً به پشتیبانی پیام دهید.")
        except TelegramError:
            pass


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data or ''
    try:
        if data == 'locked':
            await q.message.reply_text('🔒 این محصول به زودی اضافه خواهد شد.')
            return

        if data.startswith('shop:super_pack'):
            await show_product_details(update, context, 'سوپر پک')
            return

        if data.startswith('shop:iranian_pack'):
            await show_product_details(update, context, 'پک ایرانیزه')
            return

        if data.startswith('trainer:'):
            _, cusa_full, version, file_format = data.split(':', 3)
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT game_name, file_url FROM ps4_trainers WHERE cusa_id=%s AND version=%s AND file_format=%s", (cusa_full, version, file_format))
                    row = cur.fetchone()
            if not row:
                await q.message.reply_text('❌ این ترینر پیدا نشد.')
                return
            await q.message.reply_text('⏳ در حال دانلود و ارسال فایل...')
            try:
                res = requests.get(row['file_url'], timeout=20)
                res.raise_for_status()
                file_bytes = io.BytesIO(res.content)
                file_name = f"{cusa_full}_{version}"
                if file_format == 'json':
                    file_name += '.json'
                elif file_format == 'mc4':
                    file_name += '.mc4.xml'
                elif file_format == 'shn':
                    file_name += '.shn'
                caption = (
                    f"✅ ترینر **{row['game_name']}**\n"
                    f"📀 نسخه: {version}\n"
                    f"📁 فرمت: {file_format}\n\n"
                    f"📌 **راهنمای نصب:**\n"
                    f"فایل رو با FTP به مسیر `/user/data/GoldHEN/cheats/{file_format}/` روی کنسول PS4 منتقل کنید.\n"
                    f"سپس توی منوی GoldHEN (با نگه داشتن دکمه Share) چیت‌ها رو فعال کنید."
                )
                await context.bot.send_document(
                    uid, document=InputFile(file_bytes, filename=file_name),
                    caption=caption, parse_mode='Markdown'
                )
            except Exception as e:
                log.error(f"Trainer download error: {e}")
                await q.message.reply_text('❌ متأسفانه دانلود فایل با خطا مواجه شد. لطفاً بعداً دوباره تلاش کنید.')
            return

        if data == 'trainer_search':
            context.user_data['flow'] = 'ps4_cusa_search'
            await q.message.reply_text('لطفاً کد CUSA بازی مورد نظرتون رو بفرستید (فقط عدد، مثلاً 00411):', reply_markup=back_kb())
            return

        if data == 'trainer_custom':
            context.user_data['flow'] = 'trainer_custom_request'
            await q.message.reply_text(
                'لطفاً اطلاعات ترینر اختصاصی مورد نظرتون رو بفرستید.\n\n'
                'مثال: `CUSA00411 - v1.27 - GTA V`\n\n'
                'می‌تونید هر توضیح دیگه‌ای هم که لازمه اضافه کنید.',
                reply_markup=back_kb(), parse_mode='Markdown'
            )
            return

        if data == 'trainer_cusa_guide':
            guide = (
                "🎮 **راهنمای پیدا کردن CUSA بازی در PS4**\n\n"
                "برای پیدا کردن Title ID (کد CUSA) بازی، مراحل زیر رو دنبال کنید:\n\n"
                "1️⃣ وارد GoldHEN بشید.\n"
                "2️⃣ گزینه دوم: `Cheat Settings` رو انتخاب کنید.\n"
                "3️⃣ گزینه پنجم: `Show Title ID` رو انتخاب کنید و روی حالت `Last` (آخری) بذارید.\n"
                "4️⃣ حالا وارد بازی مورد نظرتون بشید. کد CUSA (مثلاً CUSA00411) روی صفحه نمایش داده می‌شه."
            )
            await q.message.reply_text(guide, reply_markup=back_kb(), parse_mode='Markdown')
            return

        if data.startswith('customtrainer_reply:'):
            if not is_admin(uid):
                await q.message.reply_text('دسترسی ندارید.')
                return
            target = int(data.split(':')[1])
            context.user_data['flow'] = 'customtrainer_reply_text'
            context.user_data['customtrainer_target'] = target
            await q.message.reply_text('پاسخ خودتون رو بنویسید:')
            return

        if data.startswith('rlsel:'):
            pid = int(data.split(':')[1])
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT name FROM products WHERE id=%s", (pid,))
                    p = cur.fetchone()
            if not p: return
            
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('پرداخت با کیف پول', callback_data=f'rl:{pid}:wallet')],
                [InlineKeyboardButton('پرداخت کارت‌به‌کارت', callback_data=f'rl:{pid}:card')]
            ])
            await q.message.reply_text(
                f"برای دریافت مجدد لینک‌های {p['name']}، مبلغ {int(REDOWNLOAD_PRICE):,} تومان هزینه دارد.\n"
                f"نحوه پرداخت را انتخاب کنید:",
                reply_markup=kb
            )
            return

        if data.startswith('appcpn:'):
            pid = int(data.split(':')[1])
            context.user_data['flow'] = 'coupon_apply'
            context.user_data['coupon_target_pid'] = pid
            await q.message.reply_text('لطفاً کد تخفیف خود را وارد کنید:', reply_markup=back_kb())
            return

        if data.startswith('upgrade:'):
            pid = int(data.split(':')[1])
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT * FROM products WHERE id=%s', (pid,))
                    p = cur.fetchone()
            if not p: return
            
            context.user_data['flow'] = 'upgrade_confirm'
            context.user_data['upgrade_pid'] = pid
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ متوجه شدم، ادامه', callback_data=f'upgok:{pid}')], [InlineKeyboardButton('❌ لغو', callback_data='shop:iranian_pack')]])
            await q.message.reply_text(
                f"⚠️ **توجه مهم** ⚠️\n\n"
                f"شما در حال ارتقای پک V1 به V2 با مبلغ {int(p['upgrade_price']):,} تومان هستید.\n\n"
                f"اگر شما نسخه V1 را خریداری نکرده‌اید، لطفاً این عملیات را لغو کنید و مستقیماً V2 را خریداری نمایید.\n"
                f"در غیر این صورت، اگر واریز کنید و ادمین متوجه شود که شما V1 را نخریده‌اید، **پول شما بازگشت داده نمی‌شود.**",
                reply_markup=kb, parse_mode='Markdown'
            )
            return

        if data.startswith('upgok:'):
            pid = int(data.split(':')[1])
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT * FROM products WHERE id=%s', (pid,))
                    p = cur.fetchone()
            amount = p['upgrade_price']
            context.user_data['upgrade_amount'] = amount
            
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('پرداخت با کیف پول', callback_data=f'upgw:{pid}')],
                [InlineKeyboardButton('پرداخت کارت‌به‌کارت', callback_data=f'upgc:{pid}')]
            ])
            await q.message.reply_text(f'مبلغ ارتقا: {int(amount):,} تومان\nروش پرداخت را انتخاب کنید:', reply_markup=kb)
            return
            
        if data.startswith('upgw:') or data.startswith('upgc:'):
            method = 'wallet' if data.startswith('upgw:') else 'card'
            pid = int(data.split(':')[1])
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT * FROM products WHERE id=%s', (pid,))
                    p = cur.fetchone()
            amount = p['upgrade_price']
            
            if method == 'wallet':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT wallet FROM users WHERE telegram_id=%s FOR UPDATE', (uid,))
                        user = cur.fetchone()
                        if not user or user['wallet'] < amount:
                            await q.message.reply_text('موجودی کیف پول کافی نیست.')
                            return
                        tc = _unique_tracking_code(cur, 'orders')
                        cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,wallet_debited,is_upgrade,tracking_code) VALUES(%s,%s,'ارتقا پک ایرانیزه',%s,'wallet','approved',true,true,%s) RETURNING id", (uid, pid, amount, tc))
                        order = cur.fetchone()['id']
                        cur.execute('UPDATE users SET wallet=wallet-%s WHERE telegram_id=%s', (amount, uid))
                        cur.execute("INSERT INTO wallet_transactions(user_id,amount,kind,reference,reason,idempotency_key) VALUES(%s,%s,'purchase',%s,%s,%s)", (uid, -amount, str(order), 'ارتقا پک', f'upg-{order}'))
                await q.message.reply_text(f'✅ پرداخت ارتقا ثبت شد.\n🔢 کد پیگیری: {tc}\nشماره سفارش: #{order}\n\nدر حال ارسال لینک‌ها...')
                await deliver(context, uid, order, 'پک ایرانیزه')
            else:
                with db() as conn:
                    with conn.cursor() as cur:
                        tc = _unique_tracking_code(cur, 'orders')
                        cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,is_upgrade,tracking_code) VALUES(%s,%s,'ارتقا پک ایرانیزه',%s,'card','pending',true,%s) RETURNING id", (uid, pid, amount, tc))
                        order = cur.fetchone()['id']
                card = os.getenv('CARD_NUMBER', '5022291544593963').strip() or 'تنظیم نشده'
                holder = os.getenv('CARD_HOLDER', 'ریبعی نژاد').strip() or 'تنظیم نشده'
                msg = (
                    f"برای ارتقای پک، مبلغ {int(amount):,} تومان را به شماره‌ی حساب زیر واریز کنید 👇\n\n"
                    f"🔢 کد پیگیری سفارش شما: {tc}\n\n"
                    f"===================\n"
                    f"{card}\n"
                    f"**{holder}**\n"
                    f"===================\n\n"
                    f"❌ این تراکنش به مدت ۲۴ ساعت اعتبار دارد و پس از آن امکان پرداخت این تراکنش وجود نخواهد داشت.\n\n"
                    f"‼️ مبلغ باید همان مبلغی باشد که در بالا ذکر شده واریز نمایید.\n"
                    f"‼️ امکان برداشت وجه از کیف پول نیست.\n\n"
                    f"👆 بعد از پرداخت دکمه پرداخت کردم را زده سپس تصویر رسید را ارسال نمایید."
                )
                kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ پرداخت کردم | ارسال رسید', callback_data=f'rcpt:{order}:order')]])
                await q.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')
            return

        if data.startswith('buyw:') or data.startswith('buyc:'):
            method = 'wallet' if data.startswith('buyw:') else 'card'
            pid = int(data.split(':')[1])
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT * FROM products WHERE id=%s AND active=true', (pid,))
                    p = cur.fetchone()
                    if not p:
                        await q.message.reply_text('محصول یافت نشد یا غیرفعال است.')
                        return
                    
                    cur.execute("SELECT 1 FROM orders WHERE user_id=%s AND product_id=%s AND status IN ('approved','delivered') AND is_upgrade=false AND product_name NOT LIKE 'دریافت مجدد%%'", (uid, pid))
                    if cur.fetchone():
                        await q.message.reply_text('⛔ شما قبلاً این محصول را سفارش داده‌اید و امکان سفارش مجدد وجود ندارد.')
                        return

            context.user_data['coupon_id'] = None
            amount = Decimal(p['price'])
            coupon_code = context.user_data.get('coupon_code')
            if coupon_code:
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT * FROM coupons WHERE code=%s AND active=true AND (expires_at IS NULL OR expires_at>now()) AND (max_uses IS NULL OR uses<max_uses)", (coupon_code.upper(),))
                        cp = cur.fetchone()
                if cp:
                    with db() as conn:
                        with conn.cursor() as cur:
                            cur.execute('SELECT 1 FROM coupon_uses WHERE coupon_id=%s AND user_id=%s', (cp['id'], uid))
                            used = cur.fetchone()
                    if used:
                        await q.message.reply_text('این کد را قبلاً استفاده کرده‌اید.')
                        return
                    if cp['discount_type'] == 'percent':
                        amount = amount * (Decimal(100) - cp['value']) / Decimal(100)
                    else:
                        amount = amount - cp['value']
                    amount = max(Decimal('0'), amount).quantize(Decimal('0.01'))
                    context.user_data['coupon_id'] = cp['id']

            if method == 'wallet':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT wallet FROM users WHERE telegram_id=%s FOR UPDATE', (uid,))
                        user = cur.fetchone()
                        if not user or user['wallet'] < amount:
                            await q.message.reply_text('موجودی کیف پول کافی نیست.')
                            return
                        tc = _unique_tracking_code(cur, 'orders')
                        cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,wallet_debited,coupon_id,tracking_code) VALUES(%s,%s,%s,%s,'wallet','approved',true,%s,%s) RETURNING id", (uid, pid, p['name'], amount, context.user_data.get('coupon_id'), tc))
                        order = cur.fetchone()['id']
                        cur.execute('UPDATE users SET wallet=wallet-%s WHERE telegram_id=%s', (amount, uid))
                        cur.execute("INSERT INTO wallet_transactions(user_id,amount,kind,reference,reason,idempotency_key) VALUES(%s,%s,'purchase',%s,%s,%s)", (uid, -amount, str(order), 'خرید محصول', f'order-{order}'))
                        if context.user_data.get('coupon_id'):
                            cur.execute('INSERT INTO coupon_uses(coupon_id,user_id,order_id) VALUES(%s,%s,%s)', (context.user_data['coupon_id'], uid, order))
                            cur.execute('UPDATE coupons SET uses=uses+1 WHERE id=%s', (context.user_data['coupon_id'],))
                await q.message.reply_text(f'✅ خرید شما ثبت شد.\n🔢 کد پیگیری: {tc}\nشماره سفارش: #{order}')
                await deliver(context, uid, order, p['name'])
            else:
                with db() as conn:
                    with conn.cursor() as cur:
                        tc = _unique_tracking_code(cur, 'orders')
                        cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,coupon_id,tracking_code) VALUES(%s,%s,%s,%s,'card','pending',%s,%s) RETURNING id", (uid, pid, p['name'], amount, context.user_data.get('coupon_id'), tc))
                        order = cur.fetchone()['id']
                card = os.getenv('CARD_NUMBER', '5022291544593963').strip() or 'تنظیم نشده'
                holder = os.getenv('CARD_HOLDER', 'ریبعی نژاد').strip() or 'تنظیم نشده'
                msg = (
                    f"برای پرداخت، مبلغ {int(amount):,} تومان را به شماره‌ی حساب زیر واریز کنید 👇\n\n"
                    f"🔢 کد پیگیری سفارش شما: {tc}\n\n"
                    f"===================\n"
                    f"{card}\n"
                    f"**{holder}**\n"
                    f"===================\n\n"
                    f"❌ این تراکنش به مدت ۲۴ ساعت اعتبار دارد و پس از آن امکان پرداخت این تراکنش وجود نخواهد داشت.\n\n"
                    f"‼️ مبلغ باید همان مبلغی باشد که در بالا ذکر شده واریز نمایید.\n"
                    f"‼️ امکان برداشت وجه از کیف پول نیست.\n"
                    f"‼️ مسئولیت واریز اشتباهی با شماست.\n\n"
                    f"👆 بعد از پرداخت دکمه پرداخت کردم را زده سپس تصویر رسید را ارسال نمایید.\n\n"
                    f"💰 بعد از تایید پرداختتون توسط ادمین کیف پول شما شارژ خواهد شد."
                )
                kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ پرداخت کردم | ارسال رسید', callback_data=f'rcpt:{order}:order')]])
                await q.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')
            return

        if data == 'wallet:topup':
            await begin_topup_from_callback(q, context)
            return

        if data.startswith('topup:') or data.startswith('orderreview:'):
            parts = data.split(':')
            kind = parts[0]
            rid = int(parts[1])
            decision = parts[2]
            if not is_super(uid):
                await q.message.reply_text('⛔ فقط مدیر اصلی اجازه بررسی رسیدها را دارد.')
                audit(uid, 'unauthorized_receipt_review', f'{kind}:{rid}', decision)
                return
            if kind == 'topup':
                await review_topup(q, context, rid, decision, uid)
            else:
                await review_order(q, context, rid, decision, uid)
            return

        if data.startswith('rcpt:'):
            _, rid, kind = data.split(':')
            context.user_data['flow'] = 'order_receipt' if kind == 'order' else 'topup_receipt'
            if kind == 'order':
                context.user_data['order_id'] = int(rid)
            await q.message.reply_text('لطفاً تصویر رسید پرداخت خود را همینجا ارسال کنید:')
            return

        if data.startswith('adm:'):
            if not is_admin(uid):
                await q.message.reply_text('دسترسی ندارید.')
                return
            action = data[4:]
            
            if action in ('coupon', 'wallet', 'edit_links', 'edit_price') and not is_super(uid):
                await q.message.reply_text('⛔ این بخش فقط برای مدیر اصلی است.')
                return

            if action == 'track':
                context.user_data['flow'] = 'adm_track'
                await q.message.reply_text('لطفاً کد پیگیری را وارد کنید (مثال: RM-4827):')
                return

            if action == 'requests':
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton('🎮 درخواست‌های پورت', callback_data='req:ports')],
                    [InlineKeyboardButton('📦 درخواست‌های پک اختصاصی', callback_data='req:custom_packs')],
                    [InlineKeyboardButton('🛠 درخواست‌های ترینر اختصاصی', callback_data='req:custom_trainers')],
                ])
                await q.message.reply_text('📋 کدوم لیست درخواست‌ها رو می‌خوای ببینی؟', reply_markup=kb)
                return
                
            if action == 'stats':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT count(*) n,coalesce(sum(amount),0) total FROM orders WHERE status IN ('approved','delivered')")
                        sales = cur.fetchone()
                        cur.execute("SELECT count(*) n FROM orders WHERE status='pending'")
                        pending = cur.fetchone()['n']
                        cur.execute("SELECT count(*) n FROM topups WHERE status='pending'")
                        top = cur.fetchone()['n']
                        cur.execute('SELECT count(*) n FROM users')
                        users = cur.fetchone()['n']
                await q.message.reply_text(f"📊 آمار\nکاربران: {users}\nسفارش‌های تأییدشده/تحویل‌شده: {sales['n']}\nدرآمد ثبت‌شده: {int(sales['total']):,} تومان\nسفارش‌های در انتظار: {pending}\nشارژهای در انتظار: {top}", reply_markup=admin_kb())
            elif action in ('topups', 'pendingorders'):
                table = 'topups' if action == 'topups' else 'orders'
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {table} WHERE status='pending' ORDER BY id LIMIT 30")
                        rows = cur.fetchall()
                if not rows:
                    await q.message.reply_text('موردی در انتظار نیست.')
                    return
                for r in rows:
                    if table == 'topups':
                        kb = InlineKeyboardMarkup([[InlineKeyboardButton('تأیید شارژ', callback_data=f"topup:{r['id']}:approve"), InlineKeyboardButton('رد شارژ', callback_data=f"topup:{r['id']}:reject")]])
                        await q.message.reply_photo(r['receipt_file_id'], caption=f"شارژ {r.get('tracking_code') or '#'+str(r['id'])} | کاربر {r['user_id']} | {int(r['amount']):,} تومان", reply_markup=kb)
                    else:
                        kb = InlineKeyboardMarkup([[InlineKeyboardButton('تأیید و تحویل', callback_data=f"orderreview:{r['id']}:approve"), InlineKeyboardButton('رد سفارش', callback_data=f"orderreview:{r['id']}:reject")]])
                        txt = f"سفارش {r.get('tracking_code') or '#'+str(r['id'])} | کاربر {r['user_id']} | {r['product_name']} | {int(r['amount']):,} تومان"
                        if r.get('receipt_file_id'):
                            await q.message.reply_photo(r['receipt_file_id'], caption=txt, reply_markup=kb)
                        else:
                            await q.message.reply_text(txt, reply_markup=kb)
            elif action == 'coupon':
                context.user_data['flow'] = 'adm_coupon_code'
                context.user_data['adm_coupon_type'] = 'percent'
                await q.message.reply_text('مرحله ۱ از ۴:\nلطفاً یک نام (کد) برای تخفیف وارد کنید (مثلاً: OFF20):')
            elif action == 'finduser':
                context.user_data['flow'] = 'finduser'
                await q.message.reply_text('Telegram ID کاربر را وارد کنید:')
            elif action == 'wallet':
                context.user_data['flow'] = 'adm_wallet_id'
                await q.message.reply_text('مرحله ۱ از ۳:\nآیدی عددی (Telegram ID) کاربر را وارد کنید:')
            elif action == 'settings':
                await q.message.reply_text('تنظیمات از Environment Variables خوانده می‌شود. برای تغییر، CARD_NUMBER یا CARD_HOLDER را در Render تغییر دهید.')
            elif action == 'orders':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT id,tracking_code,user_id,product_name,amount,status FROM orders ORDER BY id DESC LIMIT 20')
                        rows = cur.fetchall()
                lines = []
                for r in rows:
                    tc = r.get('tracking_code') or f"#{r['id']}"
                    lines.append(f"{tc} | {r['user_id']} | {r['product_name']} | {int(r['amount']):,} | {r['status']}")
                await q.message.reply_text('\n'.join(lines) or 'سفارشی نیست.')
            elif action == 'ports':
                await q.message.reply_text('لطفاً از منوی «📋 درخواست‌ها» استفاده کنید.', reply_markup=admin_kb())
            elif action == 'support':
                context.user_data['flow'] = 'support_reply'
                await q.message.reply_text('برای پاسخ وارد کنید: TelegramID,متن پاسخ')
            elif action == 'broadcast':
                context.user_data['flow'] = 'broadcast'
                await q.message.reply_text('متن پیام همگانی را وارد کنید:')
            elif action == 'audit':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT * FROM admin_audit ORDER BY id DESC LIMIT 20')
                        rows = cur.fetchall()
                await q.message.reply_text('\n'.join([f"{_fa_dt(r['created_at'])} | مدیر {r['admin_id']} | {r['action']} | {r['target']} | {r['details']}" for r in rows]) or 'سابقه‌ای ثبت نشده است.')
            elif action == 'edit_price':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT id, name FROM products WHERE active=true ORDER BY id')
                        rows = cur.fetchall()
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(r['name'], callback_data=f"chg_price:{r['id']}")] for r in rows])
                await q.message.reply_text('محصولی که می‌خواهید قیمت آن را تغییر دهید انتخاب کنید:', reply_markup=kb)
            elif action == 'edit_links':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT id, name FROM products WHERE active=true ORDER BY id')
                        rows = cur.fetchall()
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(r['name'], callback_data=f"chg_links:{r['id']}")] for r in rows])
                await q.message.reply_text('محصولی که می‌خواهید لینک‌های آن را تغییر دهید انتخاب کنید:', reply_markup=kb)
            return

        if data.startswith('req:'):
            if not is_admin(uid):
                await q.message.reply_text('دسترسی ندارید.')
                return
            req_type = data.split(':')[1]
            if req_type == 'ports':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT * FROM nostalgia_requests WHERE status != 'done' ORDER BY id DESC LIMIT 20")
                        rows = cur.fetchall()
                if not rows:
                    await q.message.reply_text('هیچ درخواست پورت در انتظاری وجود نداره.')
                    return
                for r in rows:
                    await q.message.reply_text(
                        f"🎮 درخواست پورت #{r['id']}\nکاربر: {r['user_id']}\nبازی: {r['game']}\nپلتفرم: {r['platform']}\nتوضیحات: {r['details']}\nوضعیت: {r['status']}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton('در حال بررسی', callback_data=f"portstatus:{r['id']}:reviewing"), InlineKeyboardButton('انجام شد', callback_data=f"portstatus:{r['id']}:done")],
                            [InlineKeyboardButton('پاسخ به کاربر', callback_data=f"portreply:{r['id']}")]
                        ])
                    )
            elif req_type == 'custom_packs':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT * FROM custom_pack_requests WHERE status != 'done' ORDER BY id DESC LIMIT 20")
                        rows = cur.fetchall()
                if not rows:
                    await q.message.reply_text('هیچ درخواست پک اختصاصی در انتظاری وجود نداره.')
                    return
                for r in rows:
                    await q.message.reply_text(
                        f"📦 درخواست پک اختصاصی #{r['id']}\nکاربر: {r['user_id']}\nبازی: {r['game']}\nپلتفرم: {r['platform']}\nتوضیحات: {r['details']}\nوضعیت: {r['status']}"
                    )
            elif req_type == 'custom_trainers':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT * FROM custom_trainer_requests WHERE status != 'done' ORDER BY id DESC LIMIT 20")
                        rows = cur.fetchall()
                if not rows:
                    await q.message.reply_text('هیچ درخواست ترینر اختصاصی در انتظاری وجود نداره.')
                    return
                for r in rows:
                    await q.message.reply_text(
                        f"🛠 درخواست ترینر اختصاصی #{r['id']}\nکاربر: {r['user_id']}\nتوضیحات: {r['details']}\nوضعیت: {r['status']}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton('در حال بررسی', callback_data=f"trainerstatus:{r['id']}:reviewing"), InlineKeyboardButton('انجام شد', callback_data=f"trainerstatus:{r['id']}:done")],
                            [InlineKeyboardButton('پاسخ به کاربر', callback_data=f"customtrainer_reply:{r['user_id']}")]
                        ])
                    )
            return

        if data.startswith('trainerstatus:'):
            if not is_admin(uid):
                await q.message.reply_text('دسترسی ندارید.')
                return
            _, rid, status = data.split(':', 2)
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('UPDATE custom_trainer_requests SET status=%s,updated_at=now() WHERE id=%s RETURNING user_id', (status, int(rid)))
                    row = cur.fetchone()
            if row:
                audit(uid, 'trainer_status', rid, status)
                try:
                    await context.bot.send_message(row['user_id'], f'وضعیت درخواست ترینر اختصاصی #{rid} به «{status}» تغییر کرد.')
                except TelegramError:
                    pass
                await q.message.reply_text('وضعیت ثبت شد.')
            else:
                await q.message.reply_text('درخواست پیدا نشد.')
            return

        if data.startswith('chg_price:'):
            if not is_super(uid): return
            pid = int(data.split(':')[1])
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT name FROM products WHERE id=%s', (pid,))
                    p = cur.fetchone()
            context.user_data['flow'] = 'edit_price_text'
            context.user_data['edit_price_pid'] = pid
            await q.message.reply_text(f'لطفاً قیمت جدید را برای {p["name"]} به تومان و فقط به صورت عدد وارد کنید:\nمثال: 799000')
            return

        if data.startswith('chg_links:'):
            if not is_super(uid): return
            pid = int(data.split(':')[1])
            context.user_data['flow'] = 'edit_links_text'
            context.user_data['edit_links_pid'] = pid
            await q.message.reply_text('لطفاً تمام لینک‌های جدید را (هر لینک در یک خط) ارسال کنید.\nبا این کار، لینک‌های قبلی کاملاً پاک می‌شوند.')
            return

        if data.startswith('supportreply:'):
            if not is_admin(uid):
                await q.message.reply_text('دسترسی ندارید.')
                return
            context.user_data.update(flow='support_reply_target', support_target=int(data.split(':')[1]))
            await q.message.reply_text('پاسخ خود را بنویسید:')
            return

        if data.startswith('portstatus:'):
            if not is_admin(uid):
                await q.message.reply_text('دسترسی ندارید.')
                return
            _, rid, status = data.split(':', 2)
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('UPDATE nostalgia_requests SET status=%s,updated_at=now() WHERE id=%s RETURNING user_id', (status, int(rid)))
                    row = cur.fetchone()
            if row:
                audit(uid, 'port_status', rid, status)
                try:
                    await context.bot.send_message(row['user_id'], f'وضعیت درخواست پورت #{rid} به «{status}» تغییر کرد.')
                except TelegramError:
                    pass
                await q.message.reply_text('وضعیت ثبت شد.')
            else:
                await q.message.reply_text('درخواست پیدا نشد.')
            return

        if data.startswith('portreply:'):
            if not is_admin(uid):
                await q.message.reply_text('دسترسی ندارید.')
                return
            context.user_data.update(flow='port_reply', port_id=int(data.split(':')[1]))
            await q.message.reply_text('پاسخ درخواست را بنویسید:')
            return
            
        if data.startswith('rl:'):
            _, pid, method = data.split(':')
            pid = int(pid)
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM products WHERE id=%s", (pid,))
                    p = cur.fetchone()
                    if not p: return
                    
                    context.user_data['flow'] = 'redl_confirm'
                    context.user_data['rl_pid'] = pid
                    context.user_data['rl_method'] = method
                    
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ متوجه شدم، ادامه', callback_data=f'rlok:{pid}:{method}')], [InlineKeyboardButton('❌ لغو', callback_data='main:back')]])
                    await q.message.reply_text(
                        f"⚠️ **توجه مهم** ⚠️\n\n"
                        f"شما در حال پرداخت مبلغ {int(REDOWNLOAD_PRICE):,} تومان برای دریافت مجدد لینک‌های {p['name']} هستید.\n\n"
                        f"ادمین بررسی می‌کند که آیا شما قبلاً این پک ({p['name']}) را خریداری کرده‌اید یا خیر.\n"
                        f"اگر این پک را نخریده باشید، **مبلغ پرداختی شما بازگشت داده نخواهد شد.**\n\n"
                        f"در صورت نداشتن پک، لطفاً این عملیات را لغو کنید و از بخش فروشگاه آن را خریداری نمایید.",
                        reply_markup=kb, parse_mode='Markdown'
                    )
            return
            
        if data.startswith('rlok:'):
            _, pid, method = data.split(':')
            pid = int(pid)
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM products WHERE id=%s", (pid,))
                    p = cur.fetchone()
                    
            amount = REDOWNLOAD_PRICE
            
            if method == 'wallet':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT wallet FROM users WHERE telegram_id=%s FOR UPDATE', (uid,))
                        user = cur.fetchone()
                        if not user or user['wallet'] < amount:
                            await q.message.reply_text('موجودی کیف پول کافی نیست.')
                            return
                        cur.execute('UPDATE users SET wallet=wallet-%s WHERE telegram_id=%s', (amount, uid))
                        tc = _unique_tracking_code(cur, 'orders')
                        cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,wallet_debited,delivery_sent,is_upgrade,tracking_code) VALUES(%s,%s,%s,%s,'wallet','approved',true,false,false,%s) RETURNING id", (uid, pid, f"دریافت مجدد {p['name']}", amount, tc))
                        new_oid = cur.fetchone()['id']
                        cur.execute("INSERT INTO wallet_transactions(user_id,amount,kind,reference,reason,idempotency_key) VALUES(%s,%s,'purchase',%s,%s,%s)", (uid, -amount, str(new_oid), f"دریافت مجدد {p['name']}", f'redl-{new_oid}'))
                await q.message.reply_text(f'✅ پرداخت انجام شد.\n🔢 کد پیگیری: {tc}\n\nدر حال ارسال لینک‌ها...')
                await deliver(context, uid, new_oid, p['name'])
            else:
                with db() as conn:
                    with conn.cursor() as cur:
                        tc = _unique_tracking_code(cur, 'orders')
                        cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,is_upgrade,tracking_code) VALUES(%s,%s,%s,%s,'card','pending',false,%s) RETURNING id", (uid, pid, f"دریافت مجدد {p['name']}", amount, tc))
                        order = cur.fetchone()['id']
                card = os.getenv('CARD_NUMBER', '5022291544593963').strip() or 'تنظیم نشده'
                holder = os.getenv('CARD_HOLDER', 'ریبعی نژاد').strip() or 'تنظیم نشده'
                msg = (
                    f"برای دریافت مجدد لینک ({p['name']})، مبلغ {int(amount):,} تومان را به شماره‌ی حساب زیر واریز کنید 👇\n\n"
                    f"🔢 کد پیگیری سفارش شما: {tc}\n\n"
                    f"===================\n"
                    f"{card}\n"
                    f"**{holder}**\n"
                    f"===================\n\n"
                    f"❌ این تراکنش به مدت ۲۴ ساعت اعتبار دارد و پس از آن امکان پرداخت این تراکنش وجود نخواهد داشت.\n\n"
                    f"‼️ مبلغ باید همان مبلغی باشد که در بالا ذکر شده واریز نمایید.\n"
                    f"‼️ امکان برداشت وجه از کیف پول نیست.\n"
                    f"‼️ مسئولیت واریز اشتباهی با شماست.\n\n"
                    f"👆 بعد از پرداخت دکمه پرداخت کردم را زده سپس تصویر رسید را ارسال نمایید.\n\n"
                    f"💰 بعد از تایید پرداختتون توسط ادمین لینک‌ها برای شما ارسال می‌شود."
                )
                kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ پرداخت کردم | ارسال رسید', callback_data=f'rcpt:{order}:order')]])
                await q.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')
                context.user_data.update(flow='order_receipt', order_id=order)
            return
            
        if data == 'main:back':
            await start(update, context)
            return

    except Exception as e:
        log.exception('Callback handler error')
        try:
            await q.message.reply_text('خطایی رخ داد. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.')
        except TelegramError:
            pass


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    text = (msg.text or '').strip()
    if text in ('❌ لغو', '⬅️ بازگشت', '🔄 اتصال مجدد / شروع مجدد'):
        context.user_data.clear()
        if text == '🔄 اتصال مجدد / شروع مجدد':
            await start(update, context)
        else:
            await msg.reply_text('عملیات لغو شد.', reply_markup=main_keyboard())
        return
        
    # --- Registration Flow ---
    flow = context.user_data.get('flow')
    if flow == 'reg_name':
        context.user_data['reg_name'] = text
        context.user_data['flow'] = 'reg_phone'
        await msg.reply_text('لطفاً **شماره تماس** خود را وارد کنید (مثال: 09131234352):')
        return
        
    if flow == 'reg_phone':
        phone = text
        reg_name = context.user_data.get('reg_name')
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE users SET reg_name=%s, phone=%s WHERE telegram_id=%s', 
                            (reg_name, phone, u.id))
        context.user_data.clear()
        await msg.reply_text('✅ ثبت‌نام شما با موفقیت انجام شد! حالا می‌توانید از منوی زیر استفاده کنید.', reply_markup=main_keyboard())
        if is_admin(u.id):
            await msg.reply_text('پنل مدیریت:', reply_markup=admin_kb())
        return

    # --- Normal Flows ---
    if text == '🛍 فروشگاه':
        await show_shop(update, context); return
    if text == '💰 کیف پول':
        await wallet(update, context); return
    if text == '👤 حساب کاربری':
        await account_info(update, context); return
    if text == '📦 سفارش‌های من':
        await orders_for_user(update, context); return
    if text == '📖 آموزش نصب':
        await msg.reply_text(INSTALL_TUTORIAL, reply_markup=main_keyboard(), parse_mode='Markdown'); return
    if text == '🎟 کد تخفیف':
        context.user_data['flow'] = 'coupon_apply'
        await msg.reply_text('کد تخفیف را وارد کنید. سپس محصول را از فروشگاه انتخاب کنید.', reply_markup=back_kb()); return
    if text == '💬 پشتیبانی':
        context.user_data['flow'] = 'support'
        await msg.reply_text('پیام متنی یا تصویر خود را ارسال کنید.', reply_markup=back_kb()); return
    if text == '🎮 درخواست پورت':
        context.user_data['flow'] = 'port_game'
        await msg.reply_text('نام بازی را وارد کنید:', reply_markup=back_kb()); return
    if text == '📦 درخواست پک اختصاصی':
        context.user_data['flow'] = 'custom_pack_game'
        await msg.reply_text('نام بازی/نرم‌افزار مورد نظر خود را وارد کنید:', reply_markup=back_kb()); return
    if text == '🆓 دریافت بیس رایگان':
        await msg.reply_text(f'🎮 **لینک دانلود فایل بیس رایگان:**\n\n{BASE_LINK}\n\n⚠️ لطفاً توجه داشته باشید که برای استفاده از مودها، حتماً باید نسخه بازی شما با این نسخه بیس یکی باشد.', reply_markup=main_keyboard(), disable_web_page_preview=True)
        return
    if text == '🎮 ترینرهای PS4':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('🔍 جستجوی ترینر', callback_data='trainer_search')],
            [InlineKeyboardButton('🛠 ساخت ترینر اختصاصی', callback_data='trainer_custom')],
            [InlineKeyboardButton('📖 راهنمای مشاهده CUSA', callback_data='trainer_cusa_guide')],
            [InlineKeyboardButton('💳 حمایت مالی', url='https://daramet.com/Amir3243')]
        ])
        await msg.reply_text('🎮 **بخش ترینرهای PS4**\nلطفاً یکی از گزینه‌های زیر رو انتخاب کنید:', reply_markup=kb, parse_mode='Markdown')
        return
    if text == '🔄 دریافت مجدد لینک':
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id, name FROM products WHERE active=true ORDER BY id')
                rows = cur.fetchall()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(r['name'], callback_data=f"rlsel:{r['id']}")] for r in rows])
        await msg.reply_text('برای دریافت مجدد لینک، پکی که قبلاً خریداری کرده‌اید را انتخاب کنید:', reply_markup=kb)
        return
    if is_admin(u.id) and text == 'پنل مدیریت':
        await msg.reply_text('پنل مدیریت:', reply_markup=admin_kb()); return
        
    try:
        if flow == 'adm_track' and is_admin(u.id):
            code = text.strip().upper()
            context.user_data.clear()
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT o.*, u.reg_name, u.phone, u.username, u.first_name FROM orders o LEFT JOIN users u ON o.user_id=u.telegram_id WHERE o.tracking_code=%s", (code,))
                    order = cur.fetchone()
                    cur.execute("SELECT t.*, u.reg_name, u.phone, u.username, u.first_name FROM topups t LEFT JOIN users u ON t.user_id=u.telegram_id WHERE t.tracking_code=%s", (code,))
                    topup = cur.fetchone()
            if not order and not topup:
                await msg.reply_text('❌ با این کد پیگیری چیزی پیدا نشد.', reply_markup=main_keyboard())
                return
            status_map = {'pending':'⏳ در انتظار','approved':'✅ تایید','rejected':'❌ رد','delivered':'📦 تحویل','cancelled':'🚫 لغو'}
            if order:
                name = order.get('reg_name') or order.get('first_name') or 'ناشناس'
                uname = f"@{order['username']}" if order.get('username') else 'بدون‌یوزرنیم'
                info = (
                    f"📦 سفارش\n"
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
                    f"━━━━━━━━━━━━━━━━━━"
                )
                await msg.reply_text(info, reply_markup=main_keyboard())
            if topup:
                name = topup.get('reg_name') or topup.get('first_name') or 'ناشناس'
                uname = f"@{topup['username']}" if topup.get('username') else 'بدون‌یوزرنیم'
                info = (
                    f"💰 شارژ کیف پول\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🔢 کد پیگیری: {topup['tracking_code']}\n"
                    f"🆔 شماره: #{topup['id']}\n"
                    f"👤 نام: {name}\n"
                    f"🔗 یوزرنیم: {uname}\n"
                    f"🆔 آیدی: {topup['user_id']}\n"
                    f"📱 تلفن: {topup.get('phone') or 'نامشخص'}\n"
                    f"💰 مبلغ: {int(topup['amount']):,} تومان\n"
                    f"📊 وضعیت: {status_map.get(topup['status'], topup['status'])}\n"
                    f"📅 زمان ثبت: {_fa_dt(topup['created_at'])}\n"
                    f"📅 زمان بررسی: {_fa_dt(topup['reviewed_at']) if topup.get('reviewed_at') else '—'}\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )
                await msg.reply_text(info, reply_markup=main_keyboard())
            return

        if flow == 'ps4_cusa_search':
            raw_cusa = text.strip().upper()
            cusa_num = raw_cusa[4:] if raw_cusa.startswith('CUSA') else raw_cusa
            cusa_full = f"CUSA{cusa_num}"
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT game_name, version, file_format FROM ps4_trainers WHERE cusa_id = %s ORDER BY version DESC", (cusa_full,))
                    rows = cur.fetchall()
            context.user_data['flow'] = None
            if not rows:
                await msg.reply_text(f'❌ برای کد {cusa_full} هیچ ترینری پیدا نشد.\nلطفاً کد رو بررسی کنید یا با پشتیبانی تماس بگیرید.', reply_markup=main_keyboard())
                return
            game_name = rows[0]['game_name']
            kb_buttons = []
            for r in rows:
                btn_text = f"{r['version']} - {r['file_format']}"
                cb_data = f"trainer:{cusa_full}:{r['version']}:{r['file_format']}"
                kb_buttons.append([InlineKeyboardButton(btn_text, callback_data=cb_data)])
            kb = InlineKeyboardMarkup(kb_buttons)
            await msg.reply_text(
                f"🎮 ترینرهای موجود برای: **{game_name}** ({cusa_full})\nلطفاً نسخه مورد نظر خودتون رو انتخاب کنید:",
                reply_markup=kb, parse_mode='Markdown'
            )
            return

        if flow == 'trainer_custom_request':
            context.user_data['flow'] = None
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT reg_name, phone FROM users WHERE telegram_id=%s', (u.id,))
                    uinfo = cur.fetchone()
                    cur.execute('INSERT INTO custom_trainer_requests(user_id, details) VALUES(%s,%s) RETURNING id', (u.id, text))
                    rid = cur.fetchone()['id']
            
            name = uinfo['reg_name'] if uinfo else 'ناشناس'
            phone = uinfo['phone'] if uinfo else 'نامشخص'
            uname = f"@{u.username}" if u.username else 'بدون‌یوزرنیم'
            
            msg_to_admin = (
                f"🛠 **درخواست ترینر اختصاصی #{rid}**\n\n"
                f"📝 متن درخواست:\n{text}\n\n"
                f"👤 کاربر: {name}\n"
                f"🔗 یوزرنیم: {uname}\n"
                f"🆔 آیدی: `{u.id}`\n"
                f"📱 تلفن: {phone}"
            )
            
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('💬 پاسخ به کاربر', callback_data=f'customtrainer_reply:{u.id}')]])
            
            admin3 = ADMIN3
            try:
                if admin3:
                    await context.bot.send_message(admin3, msg_to_admin, reply_markup=kb, parse_mode='Markdown')
            except TelegramError:
                pass
            await msg.reply_text('✅ درخواست شما برای ساخت ترینر اختصاصی ثبت شد. به زودی با شما تماس می‌گیریم.', reply_markup=main_keyboard())
            return

        if flow == 'customtrainer_reply_text' and is_admin(u.id):
            target = context.user_data['customtrainer_target']
            body = text
            try:
                await context.bot.send_message(target, f'💬 **پاسخ به درخواست ترینر اختصاصی شما:**\n\n{body}', parse_mode='Markdown')
            except TelegramError:
                pass
            audit(u.id, 'customtrainer_reply', str(target), body)
            context.user_data.clear()
            await msg.reply_text('پاسخ شما برای کاربر ارسال شد.')
            return

        if flow == 'topup_amount':
            amount = Decimal(text)
            if amount <= 0 or amount > Decimal('1000000000'):
                raise InvalidOperation()
            context.user_data['topup_amount'] = amount
            context.user_data['flow'] = 'topup_amount_confirm'
            
            card = os.getenv('CARD_NUMBER', '5022291544593963').strip() or 'تنظیم نشده'
            holder = os.getenv('CARD_HOLDER', 'ریبعی نژاد').strip() or 'تنظیم نشده'
            msg_text = (
                f"برای افزایش موجودی، مبلغ {int(amount):,} تومان را به شماره‌ی حساب زیر واریز کنید 👇\n\n"
                f"===================\n"
                f"{card}\n"
                f"**{holder}**\n"
                f"===================\n\n"
                f"❌ این تراکنش به مدت ۲۴ ساعت اعتبار دارد و پس از آن امکان پرداخت این تراکنش وجود نخواهد داشت.\n\n"
                f"‼️ مبلغ باید همان مبلغی باشد که در بالا ذکر شده واریز نمایید.\n"
                f"‼️ امکان برداشت وجه از کیف پول نیست.\n"
                f"‼️ مسئولیت واریز اشتباهی با شماست.\n\n"
                f"👆 بعد از پرداخت دکمه پرداخت کردم را زده سپس تصویر رسید را ارسال نمایید.\n\n"
                f"💰 بعد از تایید پرداختتون توسط ادمین کیف پول شما شارژ خواهد شد."
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ پرداخت کردم | ارسال رسید', callback_data='rcpt:0:topup')]])
            await msg.reply_text(msg_text, reply_markup=kb, parse_mode='Markdown')
            return
            
        if flow == 'topup_receipt' and msg.photo:
            amount = context.user_data.pop('topup_amount')
            file_id = msg.photo[-1].file_id
            with db() as conn:
                with conn.cursor() as cur:
                    tc = _unique_tracking_code(cur, 'topups')
                    cur.execute('INSERT INTO topups(user_id,amount,receipt_file_id,tracking_code) VALUES(%s,%s,%s,%s) RETURNING id', (u.id, amount, file_id, tc))
                    rid = cur.fetchone()['id']
                    cur.execute('SELECT reg_name, phone FROM users WHERE telegram_id=%s', (u.id,))
                    uinfo = cur.fetchone()
                    cur.execute('SELECT created_at FROM topups WHERE id=%s', (rid,))
                    created = cur.fetchone()['created_at']
            context.user_data.clear()
            await msg.reply_text(f'⏳ درخواست شارژ شما ثبت شد.\n🔢 کد پیگیری: {tc}\n\nپس از بررسی مدیر اصلی، نتیجه به شما اطلاع داده می‌شود.', reply_markup=main_keyboard())
            
            # ارسال به ادمین اصلی
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ تایید شارژ', callback_data=f'topup:{rid}:approve'), InlineKeyboardButton('❌ رد شارژ', callback_data=f'topup:{rid}:reject')]])
            user_info = f"{uinfo['reg_name']} | @{u.username or 'بدون‌یوزرنیم'} | {u.id} | تلفن: {uinfo['phone']}"
            try:
                await context.bot.send_photo(SUPER_ADMIN, file_id, caption=f'💳 درخواست شارژ جدید\n🔢 کد پیگیری: {tc}\n👤 {user_info}\n💰 مبلغ: {int(amount):,} تومان\n📅 {_fa_dt(created)}', reply_markup=kb)
            except TelegramError:
                pass
            
            # لاگ توی چنل با کد پیگیری
            cap = (
                f"🧾 رسید جدید — در انتظار بررسی\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🔢 کد پیگیری: {tc}\n"
                f"👤 نام: {uinfo['reg_name']}\n"
                f"🔗 یوزرنیم: @{u.username or 'بدون‌یوزرنیم'}\n"
                f"🆔 آیدی: {u.id}\n"
                f"📱 تلفن: {uinfo['phone']}\n"
                f"💰 مبلغ: {int(amount):,} تومان\n"
                f"📦 نوع: شارژ کیف پول\n"
                f"📅 زمان تراکنش: {_fa_dt(created)}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⏳ وضعیت: در انتظار بررسی"
            )
            msg_id = await log_photo(context, file_id, cap)
            if msg_id:
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('UPDATE topups SET receipt_message_id=%s WHERE id=%s', (msg_id, rid))
            return
            
        if flow == 'order_receipt' and msg.photo:
            rid = context.user_data.get('order_id')
            file_id = msg.photo[-1].file_id
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET receipt_file_id=%s WHERE id=%s AND user_id=%s AND status='pending' RETURNING id,amount,product_name,tracking_code,created_at", (file_id, rid, u.id))
                    r = cur.fetchone()
                    cur.execute('SELECT reg_name, phone FROM users WHERE telegram_id=%s', (u.id,))
                    uinfo = cur.fetchone()
            context.user_data.clear()
            if not r:
                await msg.reply_text('سفارش در انتظار یافت نشد.'); return
            tc = r['tracking_code'] or '—'
            await msg.reply_text(f'⏳ رسید سفارش شما دریافت شد.\n🔢 کد پیگیری: {tc}\n\nپس از بررسی مدیر اصلی، نتیجه به شما اطلاع داده می‌شود.', reply_markup=main_keyboard())
            
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ تایید و تحویل', callback_data=f'orderreview:{rid}:approve'), InlineKeyboardButton('❌ رد سفارش', callback_data=f'orderreview:{rid}:reject')]])
            user_info = f"{uinfo['reg_name']} | @{u.username or 'بدون‌یوزرنیم'} | {u.id} | تلفن: {uinfo['phone']}"
            try:
                await context.bot.send_photo(SUPER_ADMIN, file_id, caption=f"📦 رسید سفارش جدید\n🔢 کد پیگیری: {tc}\n👤 {user_info}\n🛍 {r['product_name']}\n💰 {int(r['amount']):,} تومان\n📅 {_fa_dt(r['created_at'])}", reply_markup=kb)
            except TelegramError:
                pass
            
            cap = (
                f"🧾 رسید جدید — در انتظار بررسی\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🔢 کد پیگیری: {tc}\n"
                f"👤 نام: {uinfo['reg_name']}\n"
                f"🔗 یوزرنیم: @{u.username or 'بدون‌یوزرنیم'}\n"
                f"🆔 آیدی: {u.id}\n"
                f"📱 تلفن: {uinfo['phone']}\n"
                f"📦 محصول: {r['product_name']}\n"
                f"💰 مبلغ: {int(r['amount']):,} تومان\n"
                f"📅 زمان تراکنش: {_fa_dt(r['created_at'])}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⏳ وضعیت: در انتظار بررسی"
            )
            msg_id = await log_photo(context, file_id, cap)
            if msg_id:
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('UPDATE orders SET receipt_message_id=%s WHERE id=%s', (msg_id, rid))
            return
            
        if flow == 'coupon_apply':
            code = text.upper()
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM coupons WHERE code=%s AND active=true AND (expires_at IS NULL OR expires_at>now()) AND (max_uses IS NULL OR uses<max_uses)", (code,))
                    cp = cur.fetchone()
            if not cp:
                await msg.reply_text('کد نامعتبر، منقضی یا غیرفعال است.'); return
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT 1 FROM coupon_uses WHERE coupon_id=%s AND user_id=%s', (cp['id'], u.id))
                    used = cur.fetchone()
            if used:
                await msg.reply_text('این کد را قبلاً استفاده کرده‌اید.'); return
            context.user_data.update(coupon_code=code, flow=None)
            target_pid = context.user_data.pop('coupon_target_pid', None)
            if target_pid:
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT name FROM products WHERE id=%s', (target_pid,))
                        pr = cur.fetchone()
                await msg.reply_text('✅ کد تخفیف با موفقیت اعمال شد.', reply_markup=main_keyboard())
                await show_product_details(update, context, pr['name'])
            else:
                await msg.reply_text('✅ کد ذخیره شد. حالا روی محصولی که می‌خواید تخفیف بگیرید کلیک کنید.', reply_markup=main_keyboard())
            return
        if flow == 'port_game':
            context.user_data.update(port_game=text, flow='port_platform')
            await msg.reply_text('پلتفرم موردنظر را وارد کنید (مثلاً PC، Android، کنسول):'); return
        if flow == 'port_platform':
            context.user_data.update(port_platform=text, flow='port_details')
            await msg.reply_text('توضیحات اضافی را وارد کنید یا «ندارد» بنویسید:'); return
        if flow == 'port_details':
            game = context.user_data['port_game']
            platform = context.user_data['port_platform']
            details = '' if text == 'ندارد' else text
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('INSERT INTO nostalgia_requests(user_id,game,platform,details) VALUES(%s,%s,%s,%s) RETURNING id', (u.id, game, platform, details))
                    rid = cur.fetchone()['id']
                    cur.execute('SELECT reg_name, phone FROM users WHERE telegram_id=%s', (u.id,))
                    uinfo = cur.fetchone()
            context.user_data.clear()
            await msg.reply_text(f'درخواست پورت #{rid} ثبت شد.', reply_markup=main_keyboard())
            
            name = uinfo['reg_name'] if uinfo else 'ناشناس'
            phone = uinfo['phone'] if uinfo else 'نامشخص'
            uname = f"@{u.username}" if u.username else 'بدون‌یوزرنیم'
            
            msg_to_admin = (
                f"🎮 **درخواست پورت #{rid}**\n\n"
                f"🎯 بازی: {game}\n"
                f"💻 پلتفرم: {platform}\n"
                f"📝 توضیحات: {details}\n\n"
                f"👤 کاربر: {name}\n"
                f"🔗 یوزرنیم: {uname}\n"
                f"🆔 آیدی: `{u.id}`\n"
                f"📱 تلفن: {phone}"
            )
            
            for aid in {SUPER_ADMIN, PORT_ADMIN}:
                try:
                    if aid:
                        await context.bot.send_message(aid, msg_to_admin, parse_mode='Markdown')
                except TelegramError:
                    pass
            return
        if flow == 'custom_pack_game':
            context.user_data.update(custom_pack_game=text, flow='custom_pack_platform')
            await msg.reply_text('پلتفرم موردنظر را وارد کنید (مثلاً PC، PS4، Xbox):'); return
        if flow == 'custom_pack_platform':
            context.user_data.update(custom_pack_platform=text, flow='custom_pack_details')
            await msg.reply_text('توضیحات/نیازمندی‌های پک اختصاصی خود را وارد کنید:'); return
        if flow == 'custom_pack_details':
            game = context.user_data['custom_pack_game']
            platform = context.user_data['custom_pack_platform']
            details = text
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('INSERT INTO custom_pack_requests(user_id,game,platform,details) VALUES(%s,%s,%s,%s) RETURNING id', (u.id, game, platform, details))
                    rid = cur.fetchone()['id']
                    cur.execute('SELECT reg_name, phone FROM users WHERE telegram_id=%s', (u.id,))
                    uinfo = cur.fetchone()
            context.user_data.clear()
            await msg.reply_text(f'درخواست پک اختصاصی #{rid} ثبت شد. به زودی با شما تماس می‌گیریم.', reply_markup=main_keyboard())
            
            name = uinfo['reg_name'] if uinfo else 'ناشناس'
            phone = uinfo['phone'] if uinfo else 'نامشخص'
            uname = f"@{u.username}" if u.username else 'بدون‌یوزرنیم'
            
            msg_to_admin = (
                f"📦 **درخواست پک اختصاصی #{rid}**\n\n"
                f"🎯 بازی: {game}\n"
                f"💻 پلتفرم: {platform}\n"
                f"📝 توضیحات: {details}\n\n"
                f"👤 کاربر: {name}\n"
                f"🔗 یوزرنیم: {uname}\n"
                f"🆔 آیدی: `{u.id}`\n"
                f"📱 تلفن: {phone}"
            )
            
            try:
                await context.bot.send_message(SUPER_ADMIN, msg_to_admin, parse_mode='Markdown')
            except TelegramError:
                pass
            return
        if flow == 'support':
            file_id = msg.photo[-1].file_id if msg.photo else None
            ftype = 'photo' if file_id else None
            body = text if text else (msg.caption or '')
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO support_messages(user_id,direction,message_text,file_id,file_type) VALUES(%s,'user_to_admin',%s,%s,%s) RETURNING id", (u.id, body, file_id, ftype))
                    sid = cur.fetchone()['id']
            context.user_data.clear()
            await msg.reply_text('پیام شما برای پشتیبانی ارسال شد.', reply_markup=main_keyboard())
            for aid in ADMIN_IDS:
                try:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton('پاسخ به کاربر', callback_data=f'supportreply:{u.id}')]])
                    if file_id:
                        await context.bot.send_photo(aid, file_id, caption=f'پشتیبانی #{sid} از {u.id}\n{body}', reply_markup=kb)
                    else:
                        await context.bot.send_message(aid, f'پشتیبانی #{sid} از {u.id}\n{body}', reply_markup=kb)
                except TelegramError:
                    pass
            return
        if flow == 'finduser' and is_admin(u.id):
            target = int(text)
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT * FROM users WHERE telegram_id=%s', (target,))
                    r = cur.fetchone()
            await msg.reply_text(f"کاربر: {target}\nنام: {r['first_name'] if r else 'یافت نشد'}\nموجودی: {int(r['wallet']):,} تومان" if r else f"کاربر: {target}\nنام: یافت نشد")
            audit(u.id, 'user_lookup', str(target))
            context.user_data.clear(); return
        if flow == 'adm_wallet_id':
            if not is_super(u.id): return
            target = int(text)
            context.user_data['adm_wallet_target'] = target
            context.user_data['flow'] = 'adm_wallet_amount'
            await msg.reply_text('مرحله ۲ از ۳:\nمبلغ مورد نظر را وارد کنید (برای کسر، عدد را با منفی وارد کنید. مثلاً: -50000):'); return
        if flow == 'adm_wallet_amount':
            if not is_super(u.id): return
            amount = Decimal(text)
            context.user_data['adm_wallet_amount'] = amount
            context.user_data['flow'] = 'adm_wallet_reason'
            await msg.reply_text('مرحله ۳ از ۳:\nدلیل تغییر موجودی را وارد کنید (مثلاً: بابت شارژ دستی):'); return
        if flow == 'adm_wallet_reason':
            if not is_super(u.id): return
            target = context.user_data['adm_wallet_target']
            amount = context.user_data['adm_wallet_amount']
            reason = text
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT wallet FROM users WHERE telegram_id=%s FOR UPDATE', (target,))
                    r = cur.fetchone()
                    if not r:
                        await msg.reply_text('کاربر یافت نشد. کاربر باید ابتدا ربات را /start کند.'); return
                    if r['wallet'] + amount < 0:
                        await msg.reply_text('موجودی نمی‌تواند منفی شود.'); return
                    cur.execute('UPDATE users SET wallet=wallet+%s WHERE telegram_id=%s', (amount, target))
                    cur.execute("INSERT INTO wallet_transactions(user_id,amount,kind,reason,admin_id,idempotency_key) VALUES(%s,%s,'admin_adjustment',%s,%s,%s)", (target, amount, reason, u.id, f'adj-{u.id}-{time.time_ns()}'))
            audit(u.id, 'wallet_adjustment', str(target), f'{amount}: {reason}')
            await msg.reply_text(f'موجودی کاربر {target} به میزان {amount:,} تغییر کرد.', reply_markup=main_keyboard())
            try:
                await context.bot.send_message(target, f'💰 موجودی کیف پول شما به میزان {amount:,} تومان تغییر کرد.\nدلیل: {reason}')
            except TelegramError:
                pass
            context.user_data.clear(); return
        if flow == 'adm_coupon_code':
            if not is_super(u.id): return
            code = text.upper()
            if ' ' in code:
                await msg.reply_text('کد نباید فاصله داشته باشد. دوباره وارد کنید:'); return
            context.user_data['adm_coupon_code'] = code
            context.user_data['flow'] = 'adm_coupon_value'
            await msg.reply_text('مرحله ۲ از ۴:\nلطفاً **درصد تخفیف** را وارد کنید (مثلاً 20 برای ۲۰٪):'); return
        if flow == 'adm_coupon_value':
            if not is_super(u.id): return
            try:
                val = Decimal(text)
                if val <= 0 or val > 100:
                    await msg.reply_text('درصد تخفیف باید بین ۱ تا ۱۰۰ باشد.'); return
                context.user_data['adm_coupon_value'] = val
                context.user_data['flow'] = 'adm_coupon_maxuses'
                await msg.reply_text('مرحله ۳ از ۴:\nحداکثر تعداد استفاده از این کد را وارد کنید (یا 0 برای نامحدود):')
            except InvalidOperation:
                await msg.reply_text('عدد معتبر نیست. لطفاً دوباره وارد کنید.')
            return
        if flow == 'adm_coupon_maxuses':
            if not is_super(u.id): return
            mu = int(text)
            context.user_data['adm_coupon_maxuses'] = mu if mu > 0 else None
            context.user_data['flow'] = 'adm_coupon_days'
            await msg.reply_text('مرحله ۴ از ۴:\nاین کد چند روز اعتبار داشته باشد؟ (0 برای بدون انقضا):'); return
        if flow == 'adm_coupon_days':
            if not is_super(u.id): return
            days = int(text)
            code = context.user_data['adm_coupon_code']
            value = context.user_data['adm_coupon_value']
            mu = context.user_data['adm_coupon_maxuses']
            d = days if days > 0 else None
            
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO coupons(code,discount_type,value,max_uses,expires_at,created_by) VALUES(%s,'percent',%s,%s,CASE WHEN %s IS NULL THEN NULL ELSE now()+(%s * interval '1 day') END,%s)", (code, value, mu, d, d, u.id))
            audit(u.id, 'coupon_created', code, f'percent {value}')
            await msg.reply_text(f'کد تخفیف {code} با موفقیت ایجاد شد.', reply_markup=main_keyboard())
            context.user_data.clear(); return

        if flow == 'edit_links_text':
            if not is_super(u.id): return
            pid = context.user_data.get('edit_links_pid')
            links = [line.strip() for line in text.split('\n') if line.strip()]
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM product_links WHERE product_id=%s", (pid,))
                    for link in links:
                        cur.execute("INSERT INTO product_links(product_id, link) VALUES(%s, %s)", (pid, link))
            audit(u.id, 'links_updated', str(pid), f'{len(links)} links')
            await msg.reply_text(f'✅ تعداد {len(links)} لینک با موفقیت برای این محصول ثبت شد.', reply_markup=main_keyboard())
            context.user_data.clear(); return

        if flow == 'edit_price_text':
            if not is_super(u.id): return
            pid = context.user_data.get('edit_price_pid')
            new_price = Decimal(text)
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE products SET price=%s WHERE id=%s", (new_price, pid))
            audit(u.id, 'price_updated', str(pid), f'{new_price}')
            await msg.reply_text(f'✅ قیمت محصول با موفقیت به {int(new_price):,} تومان تغییر یافت.', reply_markup=main_keyboard())
            context.user_data.clear(); return

        if flow == 'support_reply_target' and is_admin(u.id):
            target = int(context.user_data['support_target']); body = text
            try:
                await context.bot.send_message(target, '💬 پاسخ پشتیبانی:\n' + body)
            except TelegramError:
                pass
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO support_messages(user_id,direction,message_text,admin_id) VALUES(%s,'admin_to_user',%s,%s)", (target, body, u.id))
            audit(u.id, 'support_reply', str(target), body)
            context.user_data.clear()
            await msg.reply_text('پاسخ ارسال شد.')
            return
        if flow == 'support_reply' and is_admin(u.id):
            p = text.split(',', 1)
            if len(p) != 2:
                await msg.reply_text('قالب: TelegramID,متن پاسخ'); return
            target = int(p[0]); body = p[1]
            try:
                await context.bot.send_message(target, '💬 پاسخ پشتیبانی:\n' + body)
            except TelegramError:
                pass
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO support_messages(user_id,direction,message_text,admin_id) VALUES(%s,'admin_to_user',%s,%s)", (target, body, u.id))
            audit(u.id, 'support_reply', str(target), body)
            await msg.reply_text('پاسخ ارسال شد.')
            context.user_data.clear(); return
        if flow == 'port_reply' and is_admin(u.id):
            rid = context.user_data['port_id']
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('UPDATE nostalgia_requests SET admin_reply=%s,updated_at=now() WHERE id=%s RETURNING user_id', (text, rid))
                    r = cur.fetchone()
            if r:
                try:
                    await context.bot.send_message(r['user_id'], f'پاسخ درخواست پورت #{rid}:\n{text}')
                except TelegramError:
                    pass
            audit(u.id, 'port_reply', str(rid), text)
            context.user_data.clear()
            await msg.reply_text('پاسخ ارسال شد.')
            return
        if is_admin(u.id) and text.startswith('/admin'):
            await msg.reply_text('پنل مدیریت:', reply_markup=admin_kb()); return
        await msg.reply_text('از منوی زیر انتخاب کنید یا /start را بزنید.', reply_markup=main_keyboard())
    except (ValueError, InvalidOperation):
        await msg.reply_text('ورودی معتبر نیست. لطفاً دوباره بررسی کنید.')
    except Exception as e:
        log.exception('Message handler error')
        try:
            await msg.reply_text('خطایی رخ داد. لطفاً دوباره تلاش کنید.')
        except TelegramError:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error('Unhandled Telegram update error', exc_info=context.error)
    if isinstance(update, Update) and update.effective_user and is_admin(update.effective_user.id):
        try:
            await update.effective_message.reply_text('خطای داخلی رخ داد؛ جزئیات محرمانه در پیام نمایش داده نمی‌شود.')
        except Exception:
            pass


def build_application() -> Application:
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
    application.add_error_handler(error_handler)
    return application


def main():
    if not BOT_TOKEN:
        raise RuntimeError('متغیر BOT_TOKEN تنظیم نشده است.')
    if not DATABASE_URL:
        raise RuntimeError('متغیر DATABASE_URL تنظیم نشده است.')
    init_db()
    application = build_application()
    log.info('RipperMods bot starting')
    application.run_polling(drop_pending_updates=False, close_loop=False, stop_signals=None)


_bot_thread = None
_bot_error = None


def run_bot_thread():
    global _bot_error
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
        main()
    except Exception as exc:
        _bot_error = exc
        log.exception('Bot stopped during startup/runtime')


def _start_bot_thread_once():
    global _bot_thread
    if _bot_thread is not None and _bot_thread.is_alive():
        return
    _bot_thread = threading.Thread(target=run_bot_thread, name='telegram-bot', daemon=True)
    _bot_thread.start()


if __name__ == '__main__':
    try:
        init_db()
    except Exception:
        log.exception('init_db failed at startup')
    _start_bot_thread_once()
    port = int(os.getenv('PORT', '10000'))
    log.info('Starting Flask health server on 0.0.0.0:%s', port)
    app.run(host='0.0.0.0', port=port, use_reloader=False)
else:
    try:
        init_db()
    except Exception:
        log.exception('init_db failed on import')
    _start_bot_thread_once()
