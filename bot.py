import os
import re
import json
import time
import sqlite3
import logging
import asyncio
import datetime as dt
import threading
from typing import Optional

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# RipperMods Telegram Shop Bot
# Python 3.10+ | python-telegram-bot 20+
# Keep secrets in environment variables, never in this file.
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("rippermods-bot")

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_MAIN_ID = int(os.getenv("ADMIN_MAIN_ID", "1808953401"))
ADMIN_PORT_ID = int(os.getenv("ADMIN_PORT_ID", "1891282471"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@Rippermods")

CARD_NUMBER = os.getenv("CARD_NUMBER", "SET_CARD_NUMBER")
CARD_HOLDER = os.getenv("CARD_HOLDER", "SET_CARD_HOLDER")
BASE_GAME_LINK = os.getenv("BASE_GAME_LINK", "SET_BASE_GAME_LINK")
DIRECT_DOWNLOAD_LINK = os.getenv("DIRECT_DOWNLOAD_LINK", "SET_DIRECT_DOWNLOAD_LINK")
UPLOAD_PASSWORD = os.getenv("UPLOAD_PASSWORD", "SET_UPLOAD_PASSWORD")

WELCOME_PHOTO = os.getenv(
    "WELCOME_PHOTO",
    "https://s50.uupload.ir/files/rippermodsgtavv100/Amir/IMG_20261021_205811_651.jpg",
)
SUPER_PACK_PHOTO = os.getenv("SUPER_PACK_PHOTO", WELCOME_PHOTO)

DISCOUNT_CODE = os.getenv("DISCOUNT_CODE", "Ripper_mods new bot").strip()
DISCOUNT_AMOUNT = int(os.getenv("DISCOUNT_AMOUNT", "99000"))
# Fixed date prevents the promotion from restarting every time the bot restarts.
# Change this value in the hosting environment when you run a new promotion.
DISCOUNT_EXPIRY_TEXT = os.getenv("DISCOUNT_EXPIRY", "2026-10-26T23:59:00")
try:
    DISCOUNT_EXPIRY = dt.datetime.fromisoformat(DISCOUNT_EXPIRY_TEXT)
except ValueError:
    raise RuntimeError("DISCOUNT_EXPIRY must look like 2026-10-26T23:59:00")

SUPER_PACK_PRICE = int(os.getenv("SUPER_PACK_PRICE", "799000"))
DB_PATH = os.getenv("DB_PATH", "rippermods.sqlite3")
PORT = int(os.getenv("PORT", "8080"))

app = Flask(__name__)


@app.get("/")
def health_check():
    return "RipperMods bot is running.", 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)


# ----------------------------- Database -----------------------------

def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS buyers (
                user_id INTEGER PRIMARY KEY,
                approved_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                product TEXT NOT NULL,
                amount INTEGER NOT NULL,
                receipt_file_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS discount_uses (
                user_id INTEGER PRIMARY KEY,
                used_at TEXT NOT NULL
            )
        """)
        conn.commit()


def is_verified_buyer(user_id: int) -> bool:
    with db_connect() as conn:
        return conn.execute(
            "SELECT 1 FROM buyers WHERE user_id = ?", (user_id,)
        ).fetchone() is not None


def add_verified_buyer(user_id: int):
    with db_connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO buyers(user_id, approved_at) VALUES(?, ?)",
            (user_id, dt.datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()


def create_order(user, file_id: str, amount: int) -> int:
    with db_connect() as conn:
        cur = conn.execute(
            """INSERT INTO orders
               (user_id, username, first_name, product, amount, receipt_file_id, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                user.id,
                user.username,
                user.first_name,
                "سوپر پک GTA V",
                amount,
                file_id,
                dt.datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_order(order_id: int):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def mark_order_reviewed(order_id: int, status: str, admin_id: int):
    with db_connect() as conn:
        cur = conn.execute(
            """UPDATE orders
               SET status = ?, reviewed_at = ?, reviewed_by = ?
               WHERE id = ? AND status = 'pending'""",
            (
                status,
                dt.datetime.now().isoformat(timespec="seconds"),
                admin_id,
                order_id,
            ),
        )
        conn.commit()
        return cur.rowcount == 1


def discount_already_used(user_id: int) -> bool:
    with db_connect() as conn:
        return conn.execute(
            "SELECT 1 FROM discount_uses WHERE user_id = ?", (user_id,)
        ).fetchone() is not None


def mark_discount_used(user_id: int):
    with db_connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO discount_uses(user_id, used_at) VALUES(?, ?)",
            (user_id, dt.datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()


# ----------------------------- UI -----------------------------

def main_menu_keyboard():
    return [
        [InlineKeyboardButton("🛒 ورود به فروشگاه", callback_data="shop_menu")],
        [InlineKeyboardButton("📞 پشتیبانی", callback_data="support")],
    ]


def shop_menu_keyboard():
    return [
        [InlineKeyboardButton("🎮 خرید پک‌های GTA V", callback_data="menu_products")],
        [InlineKeyboardButton("🏗 سفارش ساخت پک اختصاصی", callback_data="req_build_pack")],
        [InlineKeyboardButton("🕹 سفارش پورت بازی نوستالژی", callback_data="req_port_nostalgia")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_main")],
    ]


def products_keyboard():
    return [
        [
            InlineKeyboardButton("🎮 سوپر پک", callback_data="buy_pack_super"),
            InlineKeyboardButton("🇮🇷 پک ایرانیزه", callback_data="buy_pack_irani"),
        ],
        [InlineKeyboardButton("📥 دانلود بیس بازی", callback_data="base_game")],
        [InlineKeyboardButton("🔙 بازگشت به فروشگاه", callback_data="shop_menu")],
    ]


def product_keyboard():
    return [
        [InlineKeyboardButton("🎟 وارد کردن کد تخفیف", callback_data="enter_discount")],
        [InlineKeyboardButton("💳 خرید و پرداخت", callback_data="pay_super")],
        [
            InlineKeyboardButton("📥 دریافت مجدد لینک", callback_data="request_relink"),
            InlineKeyboardButton("🔙 بازگشت", callback_data="menu_products"),
        ],
    ]


def product_caption(user_data):
    price = SUPER_PACK_PRICE
    if user_data.get("discount_applied"):
        price = max(0, SUPER_PACK_PRICE - DISCOUNT_AMOUNT)
    return f"🎮 **سوپر پک GTA V**\n\n💰 قیمت: {price:,} تومان"


async def show_product(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_data):
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=SUPER_PACK_PHOTO,
        caption=product_caption(user_data),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(product_keyboard()),
    )


async def is_user_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(
            chat_id=CHANNEL_USERNAME, user_id=user_id
        )
        return member.status in ("member", "administrator", "creator")
    except Exception:
        logger.exception("Could not check channel membership for user %s", user_id)
        return False


async def send_welcome(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎮 **به فروشگاه RipperMods خوش آمدید!**\n\n"
        "ما اینجا هستیم تا تجربه بازی شما رو متفاوت‌تر کنیم. 🚀\n\n"
        "🔹 **خرید پک‌های حرفه‌ای GTA V**\n"
        "🔹 **ساخت پک اختصاصی (دلخواه)**\n"
        "🔹 **پورت بازی‌های نوستالژی**\n\n"
        "👇 برای شروع، روی دکمه‌های زیر کلیک کنید:"
    )
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=WELCOME_PHOTO,
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(main_menu_keyboard()),
    )


# ----------------------------- Commands -----------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    if not user:
        return
    if not await is_user_member(user.id, context):
        keyboard = [
            [InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/Rippermods")],
            [InlineKeyboardButton("✅ عضو شدم، بررسی کن", callback_data="check_join")],
        ]
        await update.effective_message.reply_text(
            "⚠️ **برای استفاده از ربات، ابتدا باید در کانال ما عضو شوید.**\n\n"
            "عضو کانال شوید و سپس دکمه تأیید را بزنید.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return
    await send_welcome(update.effective_chat.id, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text(
        "عملیات لغو شد. برای نمایش منو /start را بزنید."
    )


# ----------------------------- Callback handlers -----------------------------

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    chat_id = query.message.chat_id if query.message else user.id
    data = query.data

    if data == "check_join":
        if await is_user_member(user.id, context):
            try:
                await query.message.delete()
            except Exception:
                pass
            await send_welcome(chat_id, context)
        else:
            await query.answer(
                "❌ هنوز عضویت شما تأیید نشده است.", show_alert=True
            )
        return

    if data == "shop_menu":
        await context.bot.send_message(
            chat_id, "🛒 **به فروشگاه خوش آمدید!**\n\nچه کاری می‌خواهید انجام دهید؟",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(shop_menu_keyboard()),
        )
    elif data == "support":
        context.user_data["support_mode"] = True
        context.user_data.pop("awaiting_service", None)
        context.user_data.pop("awaiting_discount", None)
        context.user_data.pop("awaiting_receipt", None)
        await context.bot.send_message(
            chat_id,
            "📞 **پشتیبانی**\n\nپیام خود را ارسال کنید. برای لغو /cancel را بزنید.",
            parse_mode="Markdown",
        )
    elif data == "back_to_main":
        context.user_data.clear()
        await context.bot.send_message(
            chat_id, "لطفاً از منوی اصلی انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(main_menu_keyboard()),
        )
    elif data == "menu_products":
        await context.bot.send_message(
            chat_id, "🎮 **پک‌های موجود:**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(products_keyboard()),
        )
    elif data == "buy_pack_irani":
        await context.bot.send_message(
            chat_id,
            "🇮🇷 **پک ایرانیزه RipperMods**\\n\\n🚧 این بخش به‌زودی فعال می‌شود. منتظر خبرهای بعدی باشید!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 بازگشت به پک‌ها", callback_data="menu_products")]]
            ),
        )
    elif data == "buy_pack_super":
        await show_product(chat_id, context, context.user_data)
    elif data == "base_game":
        if BASE_GAME_LINK.startswith("http"):
            text = f"📥 **دانلود بیس بازی:**\n\n{BASE_GAME_LINK}"
        else:
            text = "لینک بیس بازی هنوز توسط مدیر تنظیم نشده است."
        await context.bot.send_message(
            chat_id, text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 بازگشت", callback_data="menu_products")]]
            ),
        )
    elif data == "req_build_pack":
        context.user_data["awaiting_service"] = "ساخت پک اختصاصی GTA V"
        context.user_data.pop("support_mode", None)
        await context.bot.send_message(
            chat_id,
            "🏗 **سفارش ساخت پک**\n\nجزئیات درخواست خود را ارسال کنید. برای لغو /cancel را بزنید.",
            parse_mode="Markdown",
        )
    elif data == "req_port_nostalgia":
        context.user_data["awaiting_service"] = "پورت بازی‌های نوستالژی"
        context.user_data.pop("support_mode", None)
        await context.bot.send_message(
            chat_id,
            "🕹 **سفارش پورت نوستالژی**\n\nنام بازی و کنسول مقصد را ارسال کنید. برای لغو /cancel را بزنید.",
            parse_mode="Markdown",
        )
    elif data == "enter_discount":
        context.user_data["awaiting_discount"] = True
        context.user_data.pop("support_mode", None)
        await context.bot.send_message(
            chat_id, "🎟 کد تخفیف را ارسال کنید. برای لغو /cancel را بزنید:"
        )
    elif data == "pay_super":
        if not CARD_NUMBER or CARD_NUMBER == "SET_CARD_NUMBER":
            await context.bot.send_message(
                chat_id, "⚠️ اطلاعات پرداخت هنوز توسط مدیر تنظیم نشده است."
            )
            return
        price = max(
            0,
            SUPER_PACK_PRICE - DISCOUNT_AMOUNT
            if context.user_data.get("discount_applied")
            else SUPER_PACK_PRICE,
        )
        context.user_data["awaiting_receipt"] = {"amount": price, "product": "super"}
        context.user_data.pop("awaiting_discount", None)
        await context.bot.send_message(
            chat_id,
            f"🛒 **خرید سوپر پک GTA V**\n\n"
            f"💰 مبلغ قابل پرداخت: **{price:,} تومان**\n"
            f"💳 شماره کارت:\n`{CARD_NUMBER}`\n"
            f"👤 نام صاحب کارت: {CARD_HOLDER}\n\n"
            "پس از پرداخت، تصویر رسید را همین‌جا ارسال کنید.\n"
            "رسیدها توسط مدیر بررسی می‌شوند. برای لغو /cancel را بزنید.",
            parse_mode="Markdown",
        )
    elif data == "request_relink":
        if not is_verified_buyer(user.id):
            await context.bot.send_message(
                chat_id,
                "❌ دسترسی دریافت مجدد لینک برای شما ثبت نشده است. اگر خرید کرده‌اید، با پشتیبانی تماس بگیرید.",
            )
            return
        await context.bot.send_message(
            ADMIN_MAIN_ID,
            f"📥 **درخواست لینک مجدد**\n"
            f"کاربر: {user.first_name}\n"
            f"آیدی: `{user.id}`\n"
            f"یوزرنیم: @{user.username or 'ندارد'}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📨 ارسال لینک", callback_data=f"sendlink_{user.id}")]]
            ),
        )
        await context.bot.send_message(chat_id, "✅ درخواست شما برای مدیر ارسال شد.")
    else:
        logger.warning("Unknown callback data: %s", data)


# ----------------------------- Admin callbacks -----------------------------

ADMIN_IDS = {ADMIN_MAIN_ID, ADMIN_PORT_ID}


async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin_id = query.from_user.id
    data = query.data or ""

    if admin_id not in ADMIN_IDS:
        await query.answer("⛔ اجازه انجام این کار را ندارید.", show_alert=True)
        return
    await query.answer()

    if data.startswith("approve_") or data.startswith("reject_"):
        if admin_id != ADMIN_MAIN_ID:
            await query.answer("⛔ بررسی رسید فقط در اختیار ادمین اصلی است.", show_alert=True)
            return
        try:
            order_id = int(data.split("_", 1)[1])
        except ValueError:
            await query.message.reply_text("شناسه سفارش نامعتبر است.")
            return

        order = get_order(order_id)
        if not order:
            await query.message.reply_text("سفارش پیدا نشد.")
            return
        if order["status"] != "pending":
            await query.message.reply_text(
                f"این سفارش قبلاً بررسی شده است. وضعیت: {order['status']}"
            )
            return

        if data.startswith("approve_"):
            if not mark_order_reviewed(order_id, "approved", admin_id):
                await query.message.reply_text("این سفارش قبلاً توسط مدیر دیگری بررسی شده است.")
                return
            add_verified_buyer(order["user_id"])
            try:
                await context.bot.send_message(
                    chat_id=order["user_id"],
                    text=(
                        f"✅ **پرداخت سفارش #{order_id} تأیید شد.**\n\n"
                        f"📥 لینک دانلود: {DIRECT_DOWNLOAD_LINK}\n"
                        f"🔑 رمز فایل: `{UPLOAD_PASSWORD}`\n\n"
                        "این اطلاعات را در اختیار دیگران قرار ندهید."
                    ),
                    parse_mode="Markdown",
                )
                await query.message.reply_text(
                    f"✅ سفارش #{order_id} تأیید شد و اطلاعات برای کاربر ارسال شد."
                )
            except Exception:
                logger.exception("Failed sending delivery to user %s", order["user_id"])
                await query.message.reply_text(
                    f"⚠️ سفارش تأیید شد، اما ارسال پیام به کاربر ناموفق بود. آیدی: {order['user_id']}"
                )
        else:
            if not mark_order_reviewed(order_id, "rejected", admin_id):
                await query.message.reply_text("این سفارش قبلاً بررسی شده است.")
                return
            try:
                await context.bot.send_message(
                    order["user_id"],
                    f"❌ رسید سفارش #{order_id} تأیید نشد. لطفاً با پشتیبانی تماس بگیرید.",
                )
            except Exception:
                logger.exception("Could not notify user about rejected order")
            await query.message.reply_text(f"❌ سفارش #{order_id} رد شد.")

    elif data.startswith("sendlink_"):
        if admin_id != ADMIN_MAIN_ID:
            await query.answer("⛔ ارسال لینک مجدد فقط در اختیار ادمین اصلی است.", show_alert=True)
            return
        try:
            target_user_id = int(data.split("_", 1)[1])
        except ValueError:
            await query.message.reply_text("آیدی کاربر نامعتبر است.")
            return
        if not is_verified_buyer(target_user_id):
            await query.message.reply_text("این کاربر در فهرست خریداران تأییدشده نیست.")
            return
        try:
            await context.bot.send_message(
                target_user_id,
                f"📥 لینک دانلود مجدد:\n{DIRECT_DOWNLOAD_LINK}\n🔑 رمز فایل: `{UPLOAD_PASSWORD}`",
                parse_mode="Markdown",
            )
            await query.message.reply_text("✅ لینک مجدداً برای خریدار ارسال شد.")
        except Exception:
            logger.exception("Could not resend download link")
            await query.message.reply_text("❌ ارسال لینک ناموفق بود.")

    elif data.startswith("reply_"):
        try:
            target_user_id = int(data.split("_", 1)[1])
        except ValueError:
            await query.message.reply_text("آیدی کاربر نامعتبر است.")
            return
        context.application.bot_data.setdefault("admin_reply_targets", {})[admin_id] = target_user_id
        await query.message.reply_text(
            f"✍️ پاسخ خود را در همین چت ارسال کنید.\n"
            f"مقصد: {target_user_id}\nبرای لغو /cancelreply را بزنید."
        )


async def cancel_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    targets = context.application.bot_data.setdefault("admin_reply_targets", {})
    if update.effective_user.id in targets:
        targets.pop(update.effective_user.id, None)
        await update.effective_message.reply_text("پاسخ لغو شد.")
    else:
        await update.effective_message.reply_text("پاسخ فعالی برای لغو ندارید.")


# ----------------------------- Incoming messages -----------------------------

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pending = context.user_data.get("awaiting_receipt")
    if not user or not pending:
        await update.effective_message.reply_text(
            "این عکس به‌عنوان رسید ثبت نشد. برای پرداخت، ابتدا از منوی محصول گزینه خرید و پرداخت را بزنید."
        )
        return

    photo = update.effective_message.photo
    if not photo:
        return
    amount = int(pending["amount"])
    order_id = create_order(user, photo[-1].file_id, amount)
    context.user_data.pop("awaiting_receipt", None)
    context.user_data.pop("discount_applied", None)

    keyboard = [
        [InlineKeyboardButton("✅ تأیید پرداخت", callback_data=f"approve_{order_id}")],
        [InlineKeyboardButton("❌ رد رسید", callback_data=f"reject_{order_id}")],
        [InlineKeyboardButton("✍️ پاسخ به کاربر", callback_data=f"reply_{user.id}")],
    ]
    caption = (
        f"📥 **رسید جدید — سفارش #{order_id}**\n\n"
        f"👤 کاربر: {user.first_name}\n"
        f"🆔 آیدی: `{user.id}`\n"
        f"🌐 یوزرنیم: @{user.username or 'بدون یوزرنیم'}\n"
        f"🛒 محصول: سوپر پک GTA V\n"
        f"💰 مبلغ ثبت‌شده: {amount:,} تومان\n"
        f"🕒 زمان: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_MAIN_ID,
            photo=photo[-1].file_id,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        logger.exception("Could not send receipt to main admin")
        await update.effective_message.reply_text(
            f"⚠️ رسید شما ثبت شد (سفارش #{order_id})، اما ارسال آن به مدیر ناموفق بود. لطفاً با پشتیبانی تماس بگیرید."
        )
        return
    await update.effective_message.reply_text(
        f"✅ رسید دریافت شد و برای بررسی ارسال شد. شماره سفارش شما: #{order_id}"
    )


async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.text:
        return
    text = message.text.strip()

    # Admin replies take priority, but only for the two configured admins.
    targets = context.application.bot_data.setdefault("admin_reply_targets", {})
    if user.id in ADMIN_IDS and user.id in targets:
        target_user_id = targets.pop(user.id)
        try:
            await context.bot.send_message(
                target_user_id, f"📬 **پاسخ پشتیبانی:**\n\n{text}", parse_mode="Markdown"
            )
            await message.reply_text("✅ پاسخ برای کاربر ارسال شد.")
        except Exception:
            logger.exception("Admin reply failed")
            await message.reply_text("❌ ارسال پاسخ ناموفق بود.")
        return

    if context.user_data.get("support_mode"):
        context.user_data.pop("support_mode", None)
        keyboard = [[InlineKeyboardButton("✍️ پاسخ", callback_data=f"reply_{user.id}")]]
        try:
            await context.bot.send_message(
                ADMIN_MAIN_ID,
                f"📩 **پیام پشتیبانی**\nاز: {user.first_name}\n"
                f"آیدی: `{user.id}`\nیوزرنیم: @{user.username or 'ندارد'}\n\n{text}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            await message.reply_text("✅ پیام شما برای پشتیبانی ارسال شد.")
        except Exception:
            logger.exception("Could not forward support message")
            await message.reply_text("❌ ارسال پیام ناموفق بود. لطفاً کمی بعد دوباره تلاش کنید.")
        return

    if context.user_data.get("awaiting_discount"):
        context.user_data.pop("awaiting_discount", None)
        now = dt.datetime.now()
        if text.casefold() != DISCOUNT_CODE.casefold():
            await message.reply_text("❌ کد تخفیف نامعتبر است.")
        elif now > DISCOUNT_EXPIRY:
            await message.reply_text("❌ مهلت کد تخفیف تمام شده است.")
        else:
            context.user_data["discount_applied"] = True
            await message.reply_text(
                f"✅ تخفیف {DISCOUNT_AMOUNT:,} تومانی اعمال شد. تا پایان مهلت کد می‌توانید از آن استفاده کنید."
            )
        await show_product(update.effective_chat.id, context, context.user_data)
        return

    if context.user_data.get("awaiting_service"):
        service = context.user_data.pop("awaiting_service")
        target_id = ADMIN_PORT_ID if "پورت" in service else ADMIN_MAIN_ID
        reply_role = "port" if target_id == ADMIN_PORT_ID else "main"
        keyboard = [[InlineKeyboardButton("✍️ پاسخ", callback_data=f"reply_{user.id}_{reply_role}")]]
        try:
            await context.bot.send_message(
                target_id,
                f"🛠 **{service}**\nاز: {user.first_name}\n"
                f"آیدی: `{user.id}`\nیوزرنیم: @{user.username or 'ندارد'}\n\n{text}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            await message.reply_text("✅ درخواست شما دریافت شد و برای تیم مربوطه ارسال شد.")
        except Exception:
            logger.exception("Could not forward service request")
            await message.reply_text("❌ ارسال درخواست ناموفق بود. لطفاً دوباره تلاش کنید.")
        return

    await message.reply_text("برای نمایش منو /start را بزنید.")


async def handle_non_photo_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_receipt"):
        await update.effective_message.reply_text(
            "لطفاً تصویر رسید را به‌صورت عکس ارسال کنید؛ برای لغو /cancel را بزنید."
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled bot error: %s", context.error, exc_info=context.error)


def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. Set the new token as an environment variable before starting."
        )
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("cancelreply", cancel_admin_reply))

    # Specific callback handlers must be registered before the catch-all handler.
    application.add_handler(
        CallbackQueryHandler(admin_action, pattern=r"^(approve_|reject_|sendlink_|reply_)")
    )
    application.add_handler(CallbackQueryHandler(button_handler))

    application.add_handler(MessageHandler(filters.PHOTO, handle_receipt))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages)
    )
    application.add_handler(
        MessageHandler(
            (filters.Document.ALL | filters.VIDEO | filters.ANIMATION) & ~filters.COMMAND,
            handle_non_photo_receipt,
        )
    )
    application.add_error_handler(error_handler)

    logger.info("RipperMods bot is starting")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
