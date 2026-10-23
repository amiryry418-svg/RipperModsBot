"""RipperMods Persian Telegram shop. PostgreSQL-backed, deployable on Render."""
from __future__ import annotations
import os, logging, threading, time, asyncio, re
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import errors
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters)

logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'), format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('rippermods')
app = Flask(__name__)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')
LOG_CHANNEL_ID = os.environ.get('LOG_CHANNEL_ID', '-1004308422504')
try: LOG_CHANNEL_ID = int(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
except ValueError: LOG_CHANNEL_ID = None
ADMIN_IDS = {1808953401, 1891282471, 6887063710}
if os.getenv('ADMIN_IDS'):
    try: ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS','').split(',') if x.strip()}
    except ValueError: log.error('ADMIN_IDS must be comma-separated integers; using built-in defaults')
SUPER_ADMIN = 1808953401
PORT_ADMIN = 1891282471
ADMIN3 = 6887063710
DELETE_DELIVERY = os.getenv('DELETE_DELIVERY_AFTER_30_SECONDS','true').lower() in ('1','true','yes')

@app.get('/health')
def health():
    try:
        with db() as conn:
            with conn.cursor() as cur: cur.execute('SELECT 1')
        return jsonify(status='ok', database='connected'), 200
    except Exception:
        log.exception('Health check database failure')
        return jsonify(status='degraded', database='unavailable'), 503

@contextmanager
def db():
    if not DATABASE_URL:
        raise RuntimeError('متغیر DATABASE_URL تنظیم نشده است. آن را در Environment Variables سرویس Render قرار دهید.')
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=8, sslmode='require', cursor_factory=RealDictCursor)
    try:
        with conn:
            yield conn
    finally:
        conn.close()

def init_db():
    """Idempotent schema initialization. Existing records are preserved."""
    schema = [
    """CREATE TABLE IF NOT EXISTS users (telegram_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, wallet NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK(wallet >= 0), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS products (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', price NUMERIC(14,2) NOT NULL CHECK(price >= 0), active BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS orders (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), product_id BIGINT REFERENCES products(id), product_name TEXT NOT NULL, amount NUMERIC(14,2) NOT NULL CHECK(amount >= 0), payment_method TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','delivered','cancelled')), receipt_file_id TEXT, coupon_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), wallet_debited BOOLEAN NOT NULL DEFAULT false, delivery_sent BOOLEAN NOT NULL DEFAULT false)""",
    """CREATE TABLE IF NOT EXISTS wallet_transactions (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), amount NUMERIC(14,2) NOT NULL, kind TEXT NOT NULL, reference TEXT, reason TEXT, admin_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), idempotency_key TEXT UNIQUE)""",
    """CREATE TABLE IF NOT EXISTS topups (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), amount NUMERIC(14,2) NOT NULL CHECK(amount > 0), receipt_file_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')), reviewed_by BIGINT, review_note TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), reviewed_at TIMESTAMPTZ)""",
    """CREATE TABLE IF NOT EXISTS coupons (id BIGSERIAL PRIMARY KEY, code TEXT NOT NULL UNIQUE, discount_type TEXT NOT NULL CHECK(discount_type IN ('percent','fixed')), value NUMERIC(14,2) NOT NULL CHECK(value > 0), max_uses INTEGER, uses INTEGER NOT NULL DEFAULT 0, expires_at TIMESTAMPTZ, active BOOLEAN NOT NULL DEFAULT true, created_by BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS coupon_uses (id BIGSERIAL PRIMARY KEY, coupon_id BIGINT NOT NULL REFERENCES coupons(id), user_id BIGINT NOT NULL REFERENCES users(telegram_id), order_id BIGINT NOT NULL UNIQUE REFERENCES orders(id), used_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(coupon_id,user_id))""",
    """CREATE TABLE IF NOT EXISTS nostalgia_requests (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), game TEXT NOT NULL, platform TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new', admin_reply TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS support_messages (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(telegram_id), direction TEXT NOT NULL CHECK(direction IN ('user_to_admin','admin_to_user')), message_text TEXT, file_id TEXT, file_type TEXT, admin_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by BIGINT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS admin_audit (id BIGSERIAL PRIMARY KEY, admin_id BIGINT NOT NULL, action TEXT NOT NULL, target TEXT, details TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    "CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
    "CREATE INDEX IF NOT EXISTS idx_topups_status ON topups(status)",
    "CREATE INDEX IF NOT EXISTS idx_nostalgia_status ON nostalgia_requests(status)",
    "CREATE INDEX IF NOT EXISTS idx_support_user_created ON support_messages(user_id, created_at DESC)",
    ]
    with db() as conn:
        with conn.cursor() as cur:
            for sql in schema: cur.execute(sql)
            cur.execute("INSERT INTO products(name,description,price) SELECT %s,%s,%s WHERE NOT EXISTS (SELECT 1 FROM products WHERE name=%s)", ('Super Pack','بسته ویژه RipperMods', Decimal(os.getenv('SUPER_PACK_PRICE','0') or '0'),'Super Pack'))

def audit(admin_id:int, action:str, target:str='', details:str=''):
    with db() as conn:
        with conn.cursor() as cur: cur.execute('INSERT INTO admin_audit(admin_id,action,target,details) VALUES(%s,%s,%s,%s)',(admin_id,action,target,details[:2000]))

async def log_event(context:ContextTypes.DEFAULT_TYPE, text:str):
    if not LOG_CHANNEL_ID: return
    try: await context.bot.send_message(LOG_CHANNEL_ID, text[:3900])
    except TelegramError: log.exception('Could not send event to log channel')

def is_admin(uid:int)->bool: return uid in ADMIN_IDS
def is_super(uid:int)->bool: return uid == SUPER_ADMIN

def main_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton('🛍 فروشگاه'),KeyboardButton('💰 کیف پول')],
      [KeyboardButton('🎟 کد تخفیف'),KeyboardButton('📦 سفارش‌های من')],
      [KeyboardButton('💬 پشتیبانی'),KeyboardButton('🎮 درخواست پورت نوستالژی')],
      [KeyboardButton('🔄 اتصال مجدد / شروع مجدد')]], resize_keyboard=True)
def back_kb(): return ReplyKeyboardMarkup([[KeyboardButton('⬅️ بازگشت'),KeyboardButton('❌ لغو')]], resize_keyboard=True)
def admin_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton('📊 آمار',callback_data='adm:stats'),InlineKeyboardButton('📦 سفارش‌ها',callback_data='adm:orders')],
      [InlineKeyboardButton('💳 شارژهای در انتظار',callback_data='adm:topups'),InlineKeyboardButton('🧾 سفارش‌های در انتظار',callback_data='adm:pendingorders')],
      [InlineKeyboardButton('🎟 ساخت کد تخفیف',callback_data='adm:coupon'),InlineKeyboardButton('🔎 جست‌وجوی کاربر',callback_data='adm:finduser')],
      [InlineKeyboardButton('💰 تغییر موجودی',callback_data='adm:wallet'),InlineKeyboardButton('⚙️ تنظیمات پرداخت',callback_data='adm:settings')],
      [InlineKeyboardButton('🎮 درخواست‌های پورت',callback_data='adm:ports'),InlineKeyboardButton('💬 پاسخ پشتیبانی',callback_data='adm:support')],
      [InlineKeyboardButton('📣 پیام همگانی',callback_data='adm:broadcast'),InlineKeyboardButton('📜 سوابق مدیران',callback_data='adm:audit')]])

async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO users(telegram_id,username,first_name) VALUES(%s,%s,%s) ON CONFLICT(telegram_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name,last_seen=now()', (u.id,u.username,u.first_name))
    context.user_data.clear()
    welcome='به فروشگاه RipperMods خوش آمدید 🌟\nاز منوی زیر گزینه موردنظر را انتخاب کنید.'
    photo=os.getenv('WELCOME_PHOTO')
    try:
        if photo: await update.effective_message.reply_photo(photo,caption=welcome,reply_markup=main_keyboard())
        else: await update.effective_message.reply_text(welcome,reply_markup=main_keyboard())
    except TelegramError: await update.effective_message.reply_text(welcome,reply_markup=main_keyboard())
    if is_admin(u.id): await update.effective_message.reply_text('پنل مدیریت:',reply_markup=admin_kb())

async def show_shop(update, context):
    with db() as conn:
        with conn.cursor() as cur: cur.execute('SELECT * FROM products WHERE active=true ORDER BY id') ; products=cur.fetchall()
    if not products:
        await update.effective_message.reply_text('فعلاً محصول فعالی وجود ندارد.',reply_markup=main_keyboard()); return
    for p in products:
        text=f"🛍 {p['name']}\n{p['description']}\n💵 قیمت: {p['price']} تومان"
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('خرید با کیف پول',callback_data=f"buyw:{p['id']}"),InlineKeyboardButton('پرداخت کارت‌به‌کارت',callback_data=f"buyc:{p['id']}")]])
        photo=os.getenv('SUPER_PACK_PHOTO') if p['name']=='Super Pack' else None
        try:
            if photo: await update.effective_message.reply_photo(photo,caption=text,reply_markup=kb)
            else: await update.effective_message.reply_text(text,reply_markup=kb)
        except TelegramError: await update.effective_message.reply_text(text,reply_markup=kb)

async def wallet(update, context):
    uid=update.effective_user.id
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT wallet FROM users WHERE telegram_id=%s',(uid,)); row=cur.fetchone()
            cur.execute('SELECT amount,kind,reason,created_at FROM wallet_transactions WHERE user_id=%s ORDER BY id DESC LIMIT 10',(uid,)); tx=cur.fetchall()
    lines=[f"موجودی شما: {row['wallet'] if row else 0} تومان",'','آخرین تراکنش‌ها:']
    lines += [f"{r['created_at']:%Y-%m-%d} | {r['kind']} | {r['amount']} تومان {('- '+r['reason']) if r['reason'] else ''}" for r in tx] or ['هنوز تراکنشی ثبت نشده است.']
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('➕ درخواست شارژ',callback_data='wallet:topup')]])
    await update.effective_message.reply_text('\n'.join(lines),reply_markup=kb)

async def orders_for_user(update, context):
    with db() as conn:
        with conn.cursor() as cur: cur.execute('SELECT id,product_name,amount,payment_method,status,created_at FROM orders WHERE user_id=%s ORDER BY id DESC LIMIT 20',(update.effective_user.id,)); rows=cur.fetchall()
    if not rows: await update.effective_message.reply_text('هنوز سفارشی ندارید.',reply_markup=main_keyboard()); return
    text='📦 سفارش‌های شما:\n\n'+'\n'.join([f"#{r['id']} — {r['product_name']} — {r['amount']} تومان\nوضعیت: {r['status']} | {r['created_at']:%Y-%m-%d}" for r in rows])
    await update.effective_message.reply_text(text,reply_markup=main_keyboard())

async def begin_topup(update, context):
    context.user_data['flow']='topup_amount'
    await update.effective_message.reply_text('مبلغ شارژ را به تومان و به‌صورت عدد مثبت وارد کنید:',reply_markup=back_kb())

async def callback(update:Update, context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    uid=q.from_user.id; data=q.data or ''
    try:
        if data.startswith('buyw:') or data.startswith('buyc:'):
            method='wallet' if data.startswith('buyw:') else 'card'; pid=int(data.split(':')[1])
            with db() as conn:
                with conn.cursor() as cur: cur.execute('SELECT * FROM products WHERE id=%s AND active=true',(pid,)); p=cur.fetchone()
            if not p: await q.message.reply_text('محصول یافت نشد یا غیرفعال است.'); return
            context.user_data['coupon_id']=None
            amount=Decimal(p['price'])
            coupon_code=context.user_data.get('coupon_code')
            if coupon_code:
                with db() as conn:
                    with conn.cursor() as cur: cur.execute("SELECT * FROM coupons WHERE code=%s AND active=true AND (expires_at IS NULL OR expires_at>now()) AND (max_uses IS NULL OR uses<max_uses)",(coupon_code.upper(),)); cp=cur.fetchone()
                if cp:
                    with db() as conn:
                        with conn.cursor() as cur: cur.execute('SELECT 1 FROM coupon_uses WHERE coupon_id=%s AND user_id=%s',(cp['id'],uid)); used=cur.fetchone()
                    if used: await q.message.reply_text('این کد را قبلاً استفاده کرده‌اید.'); return
                    amount=amount*(Decimal(100)-cp['value'])/Decimal(100) if cp['discount_type']=='percent' else amount-cp['value']
                    amount=max(Decimal('0'),amount).quantize(Decimal('0.01')); context.user_data['coupon_id']=cp['id']
            if method=='wallet':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT wallet FROM users WHERE telegram_id=%s FOR UPDATE',(uid,)); user=cur.fetchone()
                        if not user or user['wallet'] < amount: await q.message.reply_text('موجودی کیف پول کافی نیست.'); return
                        cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,wallet_debited,coupon_id) VALUES(%s,%s,%s,%s,'wallet','approved',true,%s) RETURNING id",(uid,pid,p['name'],amount,context.user_data.get('coupon_id'))); order=cur.fetchone()['id']
                        cur.execute('UPDATE users SET wallet=wallet-%s WHERE telegram_id=%s',(amount,uid))
                        cur.execute("INSERT INTO wallet_transactions(user_id,amount,kind,reference,reason,idempotency_key) VALUES(%s,%s,'purchase',%s,%s,%s)",(uid,-amount,str(order),'خرید محصول',f'order-{order}'))
                        if context.user_data.get('coupon_id'):
                            cur.execute('INSERT INTO coupon_uses(coupon_id,user_id,order_id) VALUES(%s,%s,%s)',(context.user_data['coupon_id'],uid,order)); cur.execute('UPDATE coupons SET uses=uses+1 WHERE id=%s',(context.user_data['coupon_id'],))
                await q.message.reply_text(f'خرید شما ثبت شد. شماره سفارش: #{order}')
                await deliver(context,uid,order,p['name'])
                await log_event(context,f'🛒 خرید با کیف پول\nسفارش: #{order}\nکاربر: {uid}\nمحصول: {p["name"]}\nمبلغ: {amount} تومان')
            else:
                with db() as conn:
                    with conn.cursor() as cur: cur.execute("INSERT INTO orders(user_id,product_id,product_name,amount,payment_method,status,coupon_id) VALUES(%s,%s,%s,%s,'card','pending',%s) RETURNING id",(uid,pid,p['name'],amount,context.user_data.get('coupon_id'))); order=cur.fetchone()['id']
                card=os.getenv('CARD_NUMBER','تنظیم نشده'); holder=os.getenv('CARD_HOLDER','تنظیم نشده')
                await q.message.reply_text(f'سفارش #{order} ثبت شد.\nمبلغ: {amount} تومان\nکارت: {card}\nبه نام: {holder}\nپس از پرداخت، تصویر رسید را ارسال کنید.',reply_markup=back_kb())
                context.user_data.update(flow='order_receipt',order_id=order)
                await log_event(context,f'🛒 سفارش جدید کارت‌به‌کارت\nسفارش #{order}\nکاربر {uid}\nمحصول {p["name"]}\nمبلغ {amount}')
            return
        if data=='wallet:topup': await begin_topup_from_callback(q,context); return
        if data.startswith('topup:') or data.startswith('orderreview:'):
            parts=data.split(':'); kind=parts[0]; rid=int(parts[1]); decision=parts[2]
            if not is_super(uid):
                await q.message.reply_text('⛔ فقط مدیر اصلی اجازه بررسی رسیدها را دارد.'); audit(uid,'unauthorized_receipt_review',f'{kind}:{rid}',decision); return
            if kind=='topup': await review_topup(q,context,rid,decision,uid)
            else: await review_order(q,context,rid,decision,uid)
            return
        if data.startswith('adm:'):
            if not is_admin(uid): await q.message.reply_text('دسترسی ندارید.'); return
            action=data[4:]
            if action=='stats':
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT count(*) n,coalesce(sum(amount),0) total FROM orders WHERE status IN ('approved','delivered')"); sales=cur.fetchone()
                        cur.execute("SELECT count(*) n FROM orders WHERE status='pending'"); pending=cur.fetchone()['n']
                        cur.execute("SELECT count(*) n FROM topups WHERE status='pending'"); top=cur.fetchone()['n']
                        cur.execute('SELECT count(*) n FROM users'); users=cur.fetchone()['n']
                await q.message.reply_text(f"📊 آمار\nکاربران: {users}\nسفارش‌های تأییدشده/تحویل‌شده: {sales['n']}\nدرآمد ثبت‌شده: {sales['total']} تومان\nسفارش‌های در انتظار: {pending}\nشارژهای در انتظار: {top}",reply_markup=admin_kb())
            elif action in ('topups','pendingorders'):
                table='topups' if action=='topups' else 'orders'; status='pending'
                with db() as conn:
                    with conn.cursor() as cur: cur.execute(f"SELECT * FROM {table} WHERE status=%s ORDER BY id LIMIT 30",(status,)); rows=cur.fetchall()
                if not rows: await q.message.reply_text('موردی در انتظار نیست.'); return
                for r in rows:
                    if table=='topups':
                        kb=InlineKeyboardMarkup([[InlineKeyboardButton('تأیید شارژ',callback_data=f"topup:{r['id']}:approve"),InlineKeyboardButton('رد شارژ',callback_data=f"topup:{r['id']}:reject")]])
                        await q.message.reply_photo(r['receipt_file_id'],caption=f"شارژ #{r['id']} | کاربر {r['user_id']} | {r['amount']} تومان",reply_markup=kb)
                    else:
                        kb=InlineKeyboardMarkup([[InlineKeyboardButton('تأیید و تحویل',callback_data=f"orderreview:{r['id']}:approve"),InlineKeyboardButton('رد سفارش',callback_data=f"orderreview:{r['id']}:reject")]])
                        txt=f"سفارش #{r['id']} | کاربر {r['user_id']} | {r['product_name']} | {r['amount']} تومان"
                        if r.get('receipt_file_id'): await q.message.reply_photo(r['receipt_file_id'],caption=txt,reply_markup=kb)
                        else: await q.message.reply_text(txt,reply_markup=kb)
            elif action=='coupon':
                context.user_data['flow']='coupon_create'; await q.message.reply_text('ساخت کد: کد,نوع(percent/fixed),مقدار,حداکثر استفاده یا -,روز اعتبار یا -\nبرای غیرفعال‌سازی: OFF,کد\nبرای مشاهده کدها: LIST')
            elif action=='finduser': context.user_data['flow']='finduser'; await q.message.reply_text('Telegram ID کاربر را وارد کنید:')
            elif action=='wallet': context.user_data['flow']='wallet_admin'; await q.message.reply_text('به شکل زیر وارد کنید: TelegramID,مبلغ مثبت یا منفی,دلیل')
            elif action=='settings': await q.message.reply_text('تنظیمات فعلی کارت از Environment Variables خوانده می‌شود. برای تغییر امن، مقدار CARD_NUMBER یا CARD_HOLDER را در Render تغییر دهید و سرویس را redeploy کنید.')
            elif action=='orders':
                with db() as conn:
                    with conn.cursor() as cur: cur.execute('SELECT id,user_id,product_name,amount,status FROM orders ORDER BY id DESC LIMIT 20'); rows=cur.fetchall()
                await q.message.reply_text('\n'.join([f"#{r['id']} | {r['user_id']} | {r['product_name']} | {r['amount']} | {r['status']}" for r in rows]) or 'سفارشی نیست.')
            elif action=='ports':
                with db() as conn:
                    with conn.cursor() as cur: cur.execute('SELECT * FROM nostalgia_requests ORDER BY id DESC LIMIT 20'); rows=cur.fetchall()
                for r in rows: await q.message.reply_text(f"درخواست پورت #{r['id']}\nکاربر: {r['user_id']}\nبازی: {r['game']}\nپلتفرم: {r['platform']}\nتوضیحات: {r['details']}\nوضعیت: {r['status']}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('در حال بررسی',callback_data=f"portstatus:{r['id']}:reviewing"),InlineKeyboardButton('انجام شد',callback_data=f"portstatus:{r['id']}:done"),InlineKeyboardButton('پاسخ',callback_data=f"portreply:{r['id']}")]]))
            elif action=='support': context.user_data['flow']='support_reply'; await q.message.reply_text('برای پاسخ وارد کنید: TelegramID,متن پاسخ')
            elif action=='broadcast': context.user_data['flow']='broadcast'; await q.message.reply_text('متن پیام همگانی را وارد کنید:')
            elif action=='audit':
                with db() as conn:
                    with conn.cursor() as cur: cur.execute('SELECT * FROM admin_audit ORDER BY id DESC LIMIT 20'); rows=cur.fetchall()
                await q.message.reply_text('\n'.join([f"{r['created_at']:%m-%d %H:%M} | مدیر {r['admin_id']} | {r['action']} | {r['target']} | {r['details']}" for r in rows]) or 'سابقه‌ای ثبت نشده است.')
            return
        if data.startswith('supportreply:'):
            if not is_admin(uid): await q.message.reply_text('دسترسی ندارید.'); return
            context.user_data.update(flow='support_reply_target',support_target=int(data.split(':')[1])); await q.message.reply_text('پاسخ خود را بنویسید:'); return
        if data.startswith('portstatus:'):
            if not is_admin(uid): return
            _,rid,status=data.split(':',2)
            with db() as conn:
                with conn.cursor() as cur: cur.execute('UPDATE nostalgia_requests SET status=%s,updated_at=now() WHERE id=%s RETURNING user_id',(status,int(rid))); row=cur.fetchone()
            if row:
                audit(uid,'port_status',rid,status); await context.bot.send_message(row['user_id'],f'وضعیت درخواست پورت #{rid} به «{status}» تغییر کرد.')
                await q.message.reply_text('وضعیت ثبت شد.'); await log_event(context,f'🎮 تغییر وضعیت درخواست پورت #{rid} به {status} توسط {uid}')
            return
        if data.startswith('portreply:'):
            if not is_admin(uid): return
            context.user_data.update(flow='port_reply',port_id=int(data.split(':')[1])); await q.message.reply_text('پاسخ درخواست را بنویسید:'); return
    except Exception:
        log.exception('Callback handler error')
        await q.message.reply_text('خطایی رخ داد. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.')

async def begin_topup_from_callback(q, context):
    context.user_data['flow']='topup_amount'; await q.message.reply_text('مبلغ شارژ را به تومان وارد کنید:',reply_markup=back_kb())

async def review_topup(q,context,rid,decision,admin_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM topups WHERE id=%s FOR UPDATE',(rid,)); r=cur.fetchone()
            if not r or r['status']!='pending': await q.message.reply_text('این درخواست قبلاً بررسی شده یا وجود ندارد.'); return
            new='approved' if decision=='approve' else 'rejected'
            cur.execute('UPDATE topups SET status=%s,reviewed_by=%s,reviewed_at=now() WHERE id=%s AND status=\'pending\'',(new,admin_id,rid))
            if cur.rowcount!=1: await q.message.reply_text('این درخواست قبلاً بررسی شده است.'); return
            if new=='approved':
                cur.execute('UPDATE users SET wallet=wallet+%s WHERE telegram_id=%s',(r['amount'],r['user_id']))
                cur.execute("INSERT INTO wallet_transactions(user_id,amount,kind,reference,reason,admin_id,idempotency_key) VALUES(%s,%s,'topup',%s,'تأیید شارژ',%s,%s) ON CONFLICT(idempotency_key) DO NOTHING",(r['user_id'],r['amount'],str(rid),admin_id,f'topup-{rid}'))
    audit(admin_id,'topup_review',str(rid),decision)
    await context.bot.send_message(r['user_id'],f"درخواست شارژ #{rid} {('تأیید شد و کیف پول شارژ شد.' if decision=='approve' else 'رد شد.')}" )
    await q.message.reply_text('بررسی ثبت شد.'); await log_event(context,f"{'✅' if decision=='approve' else '❌'} {new} شارژ #{rid} | کاربر {r['user_id']} | {r['amount']} تومان | مدیر {admin_id}")

async def review_order(q,context,rid,decision,admin_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(rid,)); r=cur.fetchone()
            if not r or r['status']!='pending': await q.message.reply_text('این سفارش قبلاً بررسی شده یا وجود ندارد.'); return
            new='approved' if decision=='approve' else 'rejected'
            cur.execute("UPDATE orders SET status=%s,updated_at=now() WHERE id=%s AND status='pending'",(new,rid))
            if cur.rowcount!=1: await q.message.reply_text('این سفارش قبلاً بررسی شده است.'); return
            if new=='approved' and r['coupon_id']:
                cur.execute('INSERT INTO coupon_uses(coupon_id,user_id,order_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(r['coupon_id'],r['user_id'],rid))
                if cur.rowcount: cur.execute('UPDATE coupons SET uses=uses+1 WHERE id=%s',(r['coupon_id'],))
    audit(admin_id,'order_review',str(rid),decision)
    await q.message.reply_text('بررسی سفارش ثبت شد.')
    if decision=='approve': await deliver(context,r['user_id'],rid,r['product_name'])
    else: await context.bot.send_message(r['user_id'],f'سفارش #{rid} رد شد. برای پیگیری با پشتیبانی تماس بگیرید.')
    await log_event(context,f"{'✅' if decision=='approve' else '❌'} سفارش #{rid} {new} | کاربر {r['user_id']} | مدیر {admin_id}")

async def deliver(context,uid,order_id,product_name):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE orders SET status='delivered',delivery_sent=true,updated_at=now() WHERE id=%s AND status IN ('approved','pending') AND delivery_sent=false RETURNING id",(order_id,)); row=cur.fetchone()
    if not row: return
    link=os.getenv('DIRECT_DOWNLOAD_LINK',''); password=os.getenv('UPLOAD_PASSWORD','')
    msg=f'🎉 محصول شما آماده تحویل است!\nسفارش: #{order_id}\nمحصول: {product_name}\nلینک دانلود: {link or "برای دریافت لینک با پشتیبانی تماس بگیرید."}\nرمز دانلود: {password or "تنظیم نشده"}'
    try:
        sent=await context.bot.send_message(uid,msg)
        if DELETE_DELIVERY:
            async def delete_later():
                await asyncio.sleep(30)
                try: await context.bot.delete_message(uid,sent.message_id)
                except TelegramError: log.info('Delivery message could not be deleted (order %s)',order_id)
            asyncio.create_task(delete_later())
    except TelegramError:
        log.exception('Delivery message failed for order %s',order_id)

async def message_handler(update:Update, context:ContextTypes.DEFAULT_TYPE):
    msg=update.effective_message; u=update.effective_user; text=(msg.text or '').strip()
    if text in ('❌ لغو','⬅️ بازگشت','🔄 اتصال مجدد / شروع مجدد'):
        context.user_data.clear()
        if text=='🔄 اتصال مجدد / شروع مجدد': await start(update,context)
        else: await msg.reply_text('عملیات لغو شد.',reply_markup=main_keyboard())
        return
    if text=='🛍 فروشگاه': await show_shop(update,context); return
    if text=='💰 کیف پول': await wallet(update,context); return
    if text=='📦 سفارش‌های من': await orders_for_user(update,context); return
    if text=='🎟 کد تخفیف': context.user_data['flow']='coupon_apply'; await msg.reply_text('کد تخفیف را وارد کنید. سپس محصول را از فروشگاه انتخاب کنید.',reply_markup=back_kb()); return
    if text=='💬 پشتیبانی': context.user_data['flow']='support'; await msg.reply_text('پیام متنی یا تصویر خود را ارسال کنید.',reply_markup=back_kb()); return
    if text=='🎮 درخواست پورت نوستالژی': context.user_data['flow']='port_game'; await msg.reply_text('نام بازی را وارد کنید:',reply_markup=back_kb()); return
    if is_admin(u.id) and text=='پنل مدیریت': await msg.reply_text('پنل مدیریت:',reply_markup=admin_kb()); return
    flow=context.user_data.get('flow')
    try:
        if flow=='topup_amount':
            amount=Decimal(text)
            if amount<=0 or amount>Decimal('1000000000'): raise InvalidOperation()
            context.user_data['topup_amount']=amount; context.user_data['flow']='topup_receipt'
            await msg.reply_text(f'مبلغ {amount} تومان ثبت شد. حالا تصویر رسید را ارسال کنید.'); return
        if flow=='topup_receipt' and msg.photo:
            amount=context.user_data.pop('topup_amount'); file_id=msg.photo[-1].file_id
            with db() as conn:
                with conn.cursor() as cur: cur.execute('INSERT INTO topups(user_id,amount,receipt_file_id) VALUES(%s,%s,%s) RETURNING id',(u.id,amount,file_id)); rid=cur.fetchone()['id']
            context.user_data.clear(); await msg.reply_text(f'درخواست شارژ #{rid} ثبت شد و منتظر بررسی مدیر اصلی است.',reply_markup=main_keyboard())
            kb=InlineKeyboardMarkup([[InlineKeyboardButton('تأیید شارژ',callback_data=f'topup:{rid}:approve'),InlineKeyboardButton('رد شارژ',callback_data=f'topup:{rid}:reject')]])
            try: await context.bot.send_photo(SUPER_ADMIN,file_id,caption=f'درخواست شارژ #{rid}\nکاربر: {u.id}\nمبلغ: {amount} تومان',reply_markup=kb)
            except TelegramError: log.exception('Could not notify super admin of topup')
            await log_event(context,f'🧾 رسید شارژ دریافت شد\n#{rid} | کاربر {u.id} | {amount} تومان'); return
        if flow=='order_receipt' and msg.photo:
            rid=context.user_data.get('order_id'); file_id=msg.photo[-1].file_id
            with db() as conn:
                with conn.cursor() as cur: cur.execute("UPDATE orders SET receipt_file_id=%s WHERE id=%s AND user_id=%s AND status='pending' RETURNING id,amount,product_name",(file_id,rid,u.id)); r=cur.fetchone()
            context.user_data.clear()
            if not r: await msg.reply_text('سفارش در انتظار یافت نشد.'); return
            await msg.reply_text(f'رسید سفارش #{rid} دریافت شد؛ پس از بررسی مدیر اصلی اطلاع می‌دهیم.',reply_markup=main_keyboard())
            kb=InlineKeyboardMarkup([[InlineKeyboardButton('تأیید و تحویل',callback_data=f'orderreview:{rid}:approve'),InlineKeyboardButton('رد سفارش',callback_data=f'orderreview:{rid}:reject')]])
            try: await context.bot.send_photo(SUPER_ADMIN,file_id,caption=f"رسید سفارش #{rid}\nکاربر: {u.id}\nمحصول: {r['product_name']}\nمبلغ: {r['amount']} تومان",reply_markup=kb)
            except TelegramError: log.exception('Could not notify super admin of order receipt')
            await log_event(context,f'🧾 رسید سفارش دریافت شد: #{rid} | کاربر {u.id}'); return
        if flow=='coupon_apply':
            code=text.upper()
            with db() as conn:
                with conn.cursor() as cur: cur.execute("SELECT * FROM coupons WHERE code=%s AND active=true AND (expires_at IS NULL OR expires_at>now()) AND (max_uses IS NULL OR uses<max_uses)",(code,)); cp=cur.fetchone()
            if not cp: await msg.reply_text('کد نامعتبر، منقضی یا غیرفعال است.'); return
            with db() as conn:
                with conn.cursor() as cur: cur.execute('SELECT 1 FROM coupon_uses WHERE coupon_id=%s AND user_id=%s',(cp['id'],u.id)); used=cur.fetchone()
            if used: await msg.reply_text('این کد را قبلاً استفاده کرده‌اید.'); return
            context.user_data.update(coupon_code=code,flow=None); await msg.reply_text('کد ذخیره شد؛ اکنون از فروشگاه محصول را انتخاب کنید.',reply_markup=main_keyboard()); return
        if flow=='port_game': context.user_data.update(port_game=text,flow='port_platform'); await msg.reply_text('پلتفرم موردنظر را وارد کنید (مثلاً PC، Android، کنسول):'); return
        if flow=='port_platform': context.user_data.update(port_platform=text,flow='port_details'); await msg.reply_text('توضیحات اضافی را وارد کنید یا «ندارد» بنویسید:'); return
        if flow=='port_details':
            game=context.user_data['port_game']; platform=context.user_data['port_platform']; details='' if text=='ندارد' else text
            with db() as conn:
                with conn.cursor() as cur: cur.execute('INSERT INTO nostalgia_requests(user_id,game,platform,details) VALUES(%s,%s,%s,%s) RETURNING id',(u.id,game,platform,details)); rid=cur.fetchone()['id']
            context.user_data.clear(); await msg.reply_text(f'درخواست پورت #{rid} ثبت شد.',reply_markup=main_keyboard())
            for aid in {SUPER_ADMIN,PORT_ADMIN,ADMIN3}:
                try: await context.bot.send_message(aid,f'🎮 درخواست پورت #{rid}\nکاربر: {u.id}\nبازی: {game}\nپلتفرم: {platform}\nتوضیحات: {details}')
                except TelegramError: pass
            await log_event(context,f'🎮 درخواست پورت #{rid} از کاربر {u.id}: {game} / {platform}'); return
        if flow=='support':
            file_id=msg.photo[-1].file_id if msg.photo else None; ftype='photo' if file_id else None
            body=text if text else (msg.caption or '')
            with db() as conn:
                with conn.cursor() as cur: cur.execute('INSERT INTO support_messages(user_id,direction,message_text,file_id,file_type) VALUES(%s,\'user_to_admin\',%s,%s,%s) RETURNING id',(u.id,body,file_id,ftype)); sid=cur.fetchone()['id']
            context.user_data.clear(); await msg.reply_text('پیام شما برای پشتیبانی ارسال شد.',reply_markup=main_keyboard())
            for aid in ADMIN_IDS:
                try:
                    kb=InlineKeyboardMarkup([[InlineKeyboardButton('پاسخ به کاربر',callback_data=f'supportreply:{u.id}')]])
                    if file_id: await context.bot.send_photo(aid,file_id,caption=f'پشتیبانی #{sid} از {u.id}\n{body}',reply_markup=kb)
                    else: await context.bot.send_message(aid,f'پشتیبانی #{sid} از {u.id}\n{body}',reply_markup=kb)
                except TelegramError: pass
            await log_event(context,f'💬 پیام پشتیبانی #{sid} از کاربر {u.id}'); return
        if flow=='finduser' and is_admin(u.id):
            target=int(text)
            with db() as conn:
                with conn.cursor() as cur: cur.execute('SELECT * FROM users WHERE telegram_id=%s',(target,)); r=cur.fetchone()
            await msg.reply_text(f"کاربر: {target}\nنام: {r['first_name'] if r else 'یافت نشد'}\nموجودی: {r['wallet'] if r else '-'}")
            audit(u.id,'user_lookup',str(target)); context.user_data.clear(); return
        if flow=='wallet_admin' and is_admin(u.id):
            parts=text.split(',',2)
            if len(parts)!=3: await msg.reply_text('قالب نامعتبر است. TelegramID,مبلغ,دلیل'); return
            target=int(parts[0]); amount=Decimal(parts[1]); reason=parts[2].strip()
            if amount==0 or not reason: await msg.reply_text('مبلغ نباید صفر و دلیل الزامی است.'); return
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT wallet FROM users WHERE telegram_id=%s FOR UPDATE',(target,)); r=cur.fetchone()
                    if not r: await msg.reply_text('کاربر باید ابتدا ربات را /start کند.'); return
                    if r['wallet']+amount<0: await msg.reply_text('موجودی نمی‌تواند منفی شود.'); return
                    cur.execute('UPDATE users SET wallet=wallet+%s WHERE telegram_id=%s',(amount,target))
                    cur.execute("INSERT INTO wallet_transactions(user_id,amount,kind,reason,admin_id,idempotency_key) VALUES(%s,%s,'admin_adjustment',%s,%s,%s)",(target,amount,reason,u.id,f'adj-{u.id}-{time.time_ns()}'))
            audit(u.id,'wallet_adjustment',str(target),f'{amount}: {reason}'); await msg.reply_text('موجودی تغییر کرد.'); await log_event(context,f'💰 تغییر موجودی کاربر {target}: {amount} | دلیل: {reason} | مدیر {u.id}'); return
        if flow=='coupon_create' and is_admin(u.id):
            if text.upper()=='LIST':
                with db() as conn:
                    with conn.cursor() as cur: cur.execute('SELECT code,discount_type,value,uses,max_uses,active,expires_at FROM coupons ORDER BY id DESC LIMIT 30'); cps=cur.fetchall()
                await msg.reply_text('\n'.join([f"{c['code']} | {c['discount_type']} {c['value']} | استفاده {c['uses']}/{c['max_uses'] or '∞'} | فعال: {c['active']} | انقضا: {c['expires_at']}" for c in cps]) or 'کدی ثبت نشده است.')
                return
            if text.upper().startswith('OFF,'):
                code=text.split(',',1)[1].strip().upper()
                with db() as conn:
                    with conn.cursor() as cur: cur.execute('UPDATE coupons SET active=false WHERE code=%s',(code,)); changed=cur.rowcount
                audit(u.id,'coupon_disabled',code,''); await msg.reply_text('کد غیرفعال شد.' if changed else 'کد پیدا نشد.'); await log_event(context,f'🎟 غیرفعال‌سازی کد {code} توسط {u.id}'); return
            p=[x.strip() for x in text.split(',')]
            if len(p)!=5: await msg.reply_text('قالب: کد,percent/fixed,مقدار,حداکثر استفاده یا -,روز اعتبار یا -'); return
            code,typ,val,maxuses,days=p; typ=typ.lower(); value=Decimal(val)
            if typ not in ('percent','fixed') or value<=0 or (typ=='percent' and value>100): await msg.reply_text('نوع یا مقدار تخفیف نامعتبر است.'); return
            mu=None if maxuses=='-' else int(maxuses); d=None if days=='-' else int(days)
            with db() as conn:
                with conn.cursor() as cur: cur.execute("INSERT INTO coupons(code,discount_type,value,max_uses,expires_at,created_by) VALUES(%s,%s,%s,%s,CASE WHEN %s IS NULL THEN NULL ELSE now()+(%s * interval '1 day') END,%s)",(code.upper(),typ,value,mu,d,d,u.id))
            audit(u.id,'coupon_created',code.upper(),f'{typ} {value}'); await msg.reply_text('کد تخفیف ایجاد شد.'); await log_event(context,f'🎟 کد تخفیف {code.upper()} توسط مدیر {u.id}'); context.user_data.clear(); return
        if flow=='broadcast' and is_admin(u.id):
            if len(text)>3500: await msg.reply_text('پیام بیش از حد طولانی است.'); return
            with db() as conn:
                with conn.cursor() as cur: cur.execute('SELECT telegram_id FROM users'); ids=[r['telegram_id'] for r in cur.fetchall()]
            ok=0
            for target in ids:
                try: await context.bot.send_message(target,text); ok+=1
                except TelegramError: pass
            audit(u.id,'broadcast','all',f'{ok}/{len(ids)}'); context.user_data.clear(); await msg.reply_text(f'ارسال تمام شد: {ok} از {len(ids)}'); await log_event(context,f'📣 پیام همگانی توسط {u.id}: {ok}/{len(ids)}'); return
        if flow=='support_reply_target' and is_admin(u.id):
            target=int(context.user_data['support_target']); body=text
            await context.bot.send_message(target,'💬 پاسخ پشتیبانی:\n'+body)
            with db() as conn:
                with conn.cursor() as cur: cur.execute("INSERT INTO support_messages(user_id,direction,message_text,admin_id) VALUES(%s,'admin_to_user',%s,%s)",(target,body,u.id))
            audit(u.id,'support_reply',str(target),body); context.user_data.clear(); await msg.reply_text('پاسخ ارسال شد.'); await log_event(context,f'💬 پاسخ پشتیبانی مدیر {u.id} به کاربر {target}'); return
        if flow=='support_reply' and is_admin(u.id):
            p=text.split(',',1)
            if len(p)!=2: await msg.reply_text('قالب: TelegramID,متن پاسخ'); return
            target=int(p[0]); body=p[1]
            await context.bot.send_message(target,'💬 پاسخ پشتیبانی:\n'+body)
            with db() as conn:
                with conn.cursor() as cur: cur.execute("INSERT INTO support_messages(user_id,direction,message_text,admin_id) VALUES(%s,'admin_to_user',%s,%s)",(target,body,u.id))
            audit(u.id,'support_reply',str(target),body); await msg.reply_text('پاسخ ارسال شد.'); await log_event(context,f'💬 پاسخ پشتیبانی مدیر {u.id} به کاربر {target}'); context.user_data.clear(); return
        if flow=='port_reply' and is_admin(u.id):
            rid=context.user_data['port_id']
            with db() as conn:
                with conn.cursor() as cur: cur.execute('UPDATE nostalgia_requests SET admin_reply=%s,updated_at=now() WHERE id=%s RETURNING user_id',(text,rid)); r=cur.fetchone()
            if r: await context.bot.send_message(r['user_id'],f'پاسخ درخواست پورت #{rid}:\n{text}')
            audit(u.id,'port_reply',str(rid),text); context.user_data.clear(); await msg.reply_text('پاسخ ارسال شد.'); await log_event(context,f'🎮 پاسخ درخواست پورت #{rid} توسط {u.id}'); return
        if is_admin(u.id) and (msg.text or '').startswith('/admin'):
            await msg.reply_text('پنل مدیریت:',reply_markup=admin_kb()); return
        await msg.reply_text('از منوی زیر انتخاب کنید یا /start را بزنید.',reply_markup=main_keyboard())
    except (ValueError, InvalidOperation): await msg.reply_text('ورودی معتبر نیست. لطفاً دوباره بررسی کنید.')
    except Exception:
        log.exception('Message handler error')
        await msg.reply_text('خطایی رخ داد. لطفاً دوباره تلاش کنید.')

async def error_handler(update:object, context:ContextTypes.DEFAULT_TYPE):
    log.error('Unhandled Telegram update error',exc_info=context.error)
    if isinstance(update,Update) and update.effective_user and is_admin(update.effective_user.id):
        try: await update.effective_message.reply_text('خطای داخلی رخ داد؛ جزئیات محرمانه در پیام نمایش داده نمی‌شود.')
        except Exception: pass
    try: await log_event(context,'⚠️ خطای مهم در اجرای ربات؛ جزئیات در لاگ خصوصی سرویس ثبت شد.')
    except Exception: pass

def main():
    if not BOT_TOKEN: raise RuntimeError('متغیر BOT_TOKEN تنظیم نشده است. آن را در Environment Variables سرویس Render قرار دهید.')
    if not DATABASE_URL: raise RuntimeError('متغیر DATABASE_URL تنظیم نشده است. آن را در Environment Variables سرویس Render قرار دهید.')
    init_db()
    application=Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler('start',start))
    application.add_handler(CommandHandler('admin',lambda u,c: None)) if False else None
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
    application.add_error_handler(error_handler)
    log.info('RipperMods bot starting')
    application.run_polling(drop_pending_updates=False, close_loop=False, stop_signals=None)

_bot_thread=None
_bot_error=None
def run_bot_thread():
    global _bot_error
    try: main()
    except Exception as exc:
        _bot_error=exc; log.exception('Bot stopped during startup/runtime')

if __name__=='__main__':
    main()
else:
    # Render Web Service: keep Flask/Gunicorn alive while polling runs in a separate thread.
    _bot_thread=threading.Thread(target=run_bot_thread,name='telegram-bot',daemon=True)
    _bot_thread.start()
