import logging
import os
import asyncio
import datetime
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- تنظیمات ---
TOKEN = "8789507883:AAH1O5TTOoBI46e77lj958dwU8BR1vC8hN4"
ADMIN_MAIN_ID = "1808953401"    
ADMIN_PORT_ID = "1891282471"    

CHANNEL_USERNAME = "@Rippermods" 

CARD_NUMBER = "6219861804455185"
CARD_HOLDER = "علیرضا عباس پور"
BASE_GAME_LINK = "https://dl2.megadl.click/PS4-Hacked/GTAV/CUSA00419/BASE/"
DIRECT_DOWNLOAD_LINK = "https://my.uupload.ir/d/D9axZ" 
UPLOAD_PASSWORD = "mwrippersfinest" 

WELCOME_PHOTO = "https://s50.uupload.ir/files/rippermodsgtavv100/Amir/IMG_20261021_205811_651.jpg"
SUPER_PACK_PHOTO = "https://s50.uupload.ir/files/rippermodsgtavv100/Amir/IMG_20261021_205811_651.jpg"

DISCOUNT_CODE = "Ripper_mods new bot"
DISCOUNT_AMOUNT = 99000 
DISCOUNT_EXPIRY = datetime.datetime.now() + datetime.timedelta(days=3)

VERIFIED_BUYERS = set()

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- وب‌سرور ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- منوها ---
def main_menu_keyboard():
    return [
        [InlineKeyboardButton("🛒 ورود به فروشگاه", callback_data='shop_menu')],
        [InlineKeyboardButton("📞 پشتیبانی", callback_data='support')]
    ]

def shop_menu_keyboard():
    return [
        [InlineKeyboardButton("🎮 خرید پک‌های GTA V", callback_data='menu_products')],
        [InlineKeyboardButton("🏗 سفارش ساخت پک اختصاصی", callback_data='req_build_pack')],
        [InlineKeyboardButton("🕹 سفارش پورت بازی نوستالژی", callback_data='req_port_nostalgia')],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data='back_to_main')]
    ]

def products_keyboard():
    return [
        [InlineKeyboardButton("🎮 سوپر پک", callback_data='buy_pack_super'), InlineKeyboardButton("🇮🇷 پک ایرانیزه", callback_data='buy_pack_irani')],
        [InlineKeyboardButton("📥 دانلود بیس بازی", callback_data='base_game')],
        [InlineKeyboardButton("🔙 بازگشت به فروشگاه", callback_data='shop_menu')]
    ]

# 👈 تابع بررسی عضویت
async def is_user_member(user_id, context: ContextTypes.DEFAULT_TYPE):
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
        return False
    except Exception as e:
        print(f"Error checking membership: {e}")
        return False

# --- استارت ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear() 
    user_id = update.effective_user.id
    
    # 👈 بررسی عضویت فقط همینجا
    if not await is_user_member(user_id, context):
        join_text = (
            "⚠️ **برای استفاده از ربات، ابتدا باید در کانال ما عضو شوید.**\n\n"
            "لطفاً روی دکمه زیر کلیک کنید، عضو کانال شوید و سپس دکمه تایید را بزنید."
        )
        keyboard = [
            [InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/Rippermods")],
            [InlineKeyboardButton("✅ عضو شدم، بررسی کن", callback_data='check_join')]
        ]
        await update.message.reply_text(join_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
        return

    welcome_text = (
        "🎮 **به فروشگاه RipperMods خوش آمدید!**\n\n"
        "ما اینجا هستیم تا تجربه بازی شما رو متفاوت‌تر کنیم. 🚀\n\n"
        "🔹 **خرید پک‌های حرفه‌ای GTA V**\n"
        "🔹 **ساخت پک اختصاصی (دلخواه)**\n"
        "🔹 **پورت بازی‌های نوستالژی**\n\n"
        "👇 برای شروع، روی دکمه‌های زیر کلیک کنید:"
    )
    await update.message.reply_photo(photo=WELCOME_PHOTO, caption=welcome_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(main_menu_keyboard()))

# --- هندلر دکمه‌ها ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    
    # 👈 دکمه بررسی عضویت
    if query.data == 'check_join':
        if await is_user_member(user_id, context):
            welcome_text = (
                "🎮 **به فروشگاه RipperMods خوش آمدید!**\n\n"
                "ما اینجا هستیم تا تجربه بازی شما رو متفاوت‌تر کنیم. 🚀\n\n"
                "🔹 **خرید پک‌های حرفه‌ای GTA V**\n"
                "🔹 **ساخت پک اختصاصی (دلخواه)**\n"
                "🔹 **پورت بازی‌های نوستالژی**\n\n"
                "👇 برای شروع، روی دکمه‌های زیر کلیک کنید:"
            )
            await context.bot.send_photo(chat_id=chat_id, photo=WELCOME_PHOTO, caption=welcome_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(main_menu_keyboard()))
            try: await query.message.delete()
            except: pass
        else:
            await query.answer("❌ شما هنوز عضو کانال نشده‌اید!", show_alert=True)
        return

    if query.data == 'shop_menu':
        await context.bot.send_message(chat_id=chat_id, text="🛒 **به فروشگاه خوش آمدید!**\n\nچه کاری می‌خواهید انجام دهید؟", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(shop_menu_keyboard()))
    
    elif query.data == 'support':
        context.user_data['support_mode'] = True
        await context.bot.send_message(chat_id=chat_id, text="📞 **پشتیبانی**\n\nلطفاً مشکل خود را بنویسید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_main')]]))

    elif query.data == 'back_to_main':
        await context.bot.send_message(chat_id=chat_id, text="لطفاً از منوی اصلی انتخاب کنید:", reply_markup=InlineKeyboardMarkup(main_menu_keyboard()))
    
    elif query.data == 'menu_products':
        await context.bot.send_message(chat_id=chat_id, text="🎮 **پک‌های موجود:**", reply_markup=InlineKeyboardMarkup(products_keyboard()))
    
    elif query.data == 'buy_pack_irani': 
        await query.answer("🚧 در حال آپدیت.", show_alert=True)
    
    elif query.data == 'buy_pack_super': 
        keyboard = [
            [InlineKeyboardButton("🎟 وارد کردن کد تخفیف", callback_data='enter_discount')],
            [InlineKeyboardButton("💳 خرید و پرداخت", callback_data='pay_super')],
            [InlineKeyboardButton("📥 دریافت مجدد لینک", callback_data='request_relink'), InlineKeyboardButton("🔙 بازگشت", callback_data='menu_products')]
        ]
        await context.bot.send_photo(chat_id=chat_id, photo=SUPER_PACK_PHOTO, caption="🎮 **سوپر پک GTA V**\n\n💰 قیمت: 799,000 تومان", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'base_game': 
        await context.bot.send_message(chat_id=chat_id, text=f"📥 **دانلود بیس بازی:**\n\n`{BASE_GAME_LINK}`", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='menu_products')]]))

    elif query.data == 'req_build_pack':
        context.user_data['awaiting_service'] = "ساخت پک اختصاصی GTA V"
        await context.bot.send_message(chat_id=chat_id, text="🏗 **سفارش ساخت پک**\n\nلطفاً جزئیات درخواست خود را ارسال کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data='shop_menu')]]))

    elif query.data == 'req_port_nostalgia':
        context.user_data['awaiting_service'] = "پورت بازی‌های نوستالژی"
        await context.bot.send_message(chat_id=chat_id, text="🕹 **سفارش پورت نوستالژی**\n\nلطفاً نام بازی و کنسول مقصد را ارسال کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data='shop_menu')]]))

    elif query.data == 'enter_discount':
        context.user_data['awaiting_discount'] = True
        await context.bot.send_message(chat_id=chat_id, text="🎟 **لطفاً کد تخفیف خود را ارسال کنید:**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data='back_to_product')]]))

    elif query.data == 'back_to_product':
        keyboard = [
            [InlineKeyboardButton("🎟 وارد کردن کد تخفیف", callback_data='enter_discount')],
            [InlineKeyboardButton("💳 خرید و پرداخت", callback_data='pay_super')],
            [InlineKeyboardButton("📥 دریافت مجدد لینک", callback_data='request_relink'), InlineKeyboardButton("🔙 بازگشت", callback_data='menu_products')]
        ]
        await context.bot.send_photo(chat_id=chat_id, photo=SUPER_PACK_PHOTO, caption="🎮 **سوپر پک GTA V**\n\n💰 قیمت: 799,000 تومان", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'pay_super':
        text = f"شما سوپر پک را انتخاب کردید.\n\n💰 مبلغ: 799,000 تومان\n💳 کارت:\n`{CARD_NUMBER}`\n👤 نام: {CARD_HOLDER}\n\n📸 **عکس رسید** را ارسال کنید."
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به محصول", callback_data='back_to_product')]]))

    elif query.data == 'request_relink':
        user = query.from_user
        if user.id in VERIFIED_BUYERS:
            keyboard = [[InlineKeyboardButton("✅ ارسال لینک", callback_data=f'sendlink_{user.id}')]]
            await context.bot.send_message(chat_id=ADMIN_MAIN_ID, text=f"📥 **درخواست دریافت مجدد لینک**\nاز خریدار تایید شده: {user.first_name} (@{user.username or 'بدون آیدی'})", reply_markup=InlineKeyboardMarkup(keyboard))
            await context.bot.send_message(chat_id=chat_id, text="✅ درخواست شما به ادمین ارسال شد. منتظر تایید باشید.")
        else:
            await context.bot.send_message(chat_id=chat_id, text="❌ **شما مجاز به دریافت لینک نیستید.**\n\nفقط افرادی که قبلاً خریداری کرده‌اند می‌توانند از این بخش استفاده کنند.")

# --- هندلرهای دریافتی ---
async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return
    user = update.effective_user
    
    # 👈 ارسال رسید همراه با اطلاعات کامل به ادمین
    keyboard = [
        [InlineKeyboardButton("✅ تایید", callback_data=f'approve_{user.id}')], 
        [InlineKeyboardButton("❌ رد", callback_data=f'reject_{user.id}')]
    ]
    await context.bot.send_photo(
        chat_id=ADMIN_MAIN_ID, 
        photo=update.message.photo[-1].file_id, 
        caption=f"📥 **رسید جدید**\n\n👤 کاربر: {user.first_name}\n🆔 آیدی عددی: `{user.id}`\n🌐 یوزرنیم: @{user.username or 'بدون آیدی'}\n\n🕒 زمان ارسال: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", 
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await update.message.reply_text("✅ رسید دریافت شد. منتظر تایید باشید.")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if context.user_data.get('support_mode'):
        keyboard = [[InlineKeyboardButton("✍️ پاسخ", callback_data=f'reply_{user.id}')]]
        await context.bot.send_message(chat_id=ADMIN_MAIN_ID, text=f"📩 پشتیبانی از {user.first_name}:\n{update.message.text}", reply_markup=InlineKeyboardMarkup(keyboard))
        await update.message.reply_text("✅ پیام دریافت شد.")
        context.user_data['support_mode'] = False
        
    elif context.user_data.get('awaiting_discount'):
        context.user_data['awaiting_discount'] = False
        if update.message.text.strip() == DISCOUNT_CODE:
            if datetime.datetime.now() <= DISCOUNT_EXPIRY:
                context.user_data['discount_applied'] = True
                await update.message.reply_text(f"✅ کد تخفیف اعمال شد! {DISCOUNT_AMOUNT:,} تومان کسر شد.")
            else:
                await update.message.reply_text("❌ مهلت کد تخفیف تمام شده.")
        else:
            await update.message.reply_text("❌ کد تخفیف نامعتبر است.")
        
        price = 799000 - DISCOUNT_AMOUNT if context.user_data.get('discount_applied') else 799000
        keyboard = [
            [InlineKeyboardButton("🎟 وارد کردن کد تخفیف", callback_data='enter_discount')],
            [InlineKeyboardButton("💳 خرید و پرداخت", callback_data='pay_super')],
            [InlineKeyboardButton("📥 دریافت مجدد لینک", callback_data='request_relink'), InlineKeyboardButton("🔙 بازگشت", callback_data='menu_products')]
        ]
        await update.message.reply_photo(photo=SUPER_PACK_PHOTO, caption=f"🎮 **سوپر پک GTA V**\n\n💰 قیمت: {price:,} تومان", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    elif context.user_data.get('awaiting_service'):
        service = context.user_data.get('awaiting_service')
        context.user_data['awaiting_service'] = False
        target_id = ADMIN_PORT_ID if "پورت" in service else ADMIN_MAIN_ID
        keyboard = [[InlineKeyboardButton("✍️ پاسخ", callback_data=f'reply_{user.id}')]]
        await context.bot.send_message(chat_id=target_id, text=f"🛠 **{service}**\nاز {user.first_name}:\n{update.message.text}", reply_markup=InlineKeyboardMarkup(keyboard))
        await update.message.reply_text(f"✅ درخواست دریافت شد.")
    else:
        await update.message.reply_text("لطفاً از منوی /start استفاده کنید.")

# --- پاسخ ادمین ---
async def admin_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith('reply_'):
        context.user_data['reply_to'] = int(data.replace('reply_', ''))
        await query.edit_message_text(text="✍️ متن پاسخ را بنویسید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data='cancel_reply')]]))
    elif data == 'cancel_reply':
        context.user_data['reply_to'] = None
        await query.edit_message_text(text="❌ لغو شد.")

async def handle_admin_reply_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.get('reply_to')
    if user_id:
        await context.bot.send_message(chat_id=user_id, text=f"📬 **پاسخ:**\n{update.message.text}")
        await update.message.reply_text("✅ ارسال شد.")
        context.user_data['reply_to'] = None

# 👈 تابع تایید و رد ادمین (با ذخیره رسید)
async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith('approve_') or data.startswith('sendlink_'):
        user_id = int(data.split('_')[1])
        
        VERIFIED_BUYERS.add(user_id)
        
        sent_msg = await context.bot.send_message(
            chat_id=user_id, 
            text=f"✅ **لینک:** {DIRECT_DOWNLOAD_LINK}\n🔑 **رمز:** `{UPLOAD_PASSWORD}`\n\n⚠️ ۳۰ ثانیه فرصت دارید.", 
            parse_mode='Markdown'
        )
        await asyncio.sleep(30)
        try: await context.bot.delete_message(chat_id=user_id, message_id=sent_msg.message_id)
        except: pass
        
        # 👈 ذخیره رسید: تغییر کپشن به جای پاک کردن
        try:
            await query.edit_message_caption(
                caption=f"✅ **تایید شده**\n\n🆔 آیدی کاربر: `{user_id}`\n👤 وضعیت: لینک ارسال شد.",
                parse_mode='Markdown',
                reply_markup=None
            )
        except Exception as e:
            print(f"Error updating caption: {e}")
            
    elif data.startswith('reject_'):
        user_id = int(data.replace('reject_', ''))
        await context.bot.send_message(chat_id=user_id, text="❌ رسید شما رد شد.")
        
        # 👈 ذخیره رسید: تغییر کپشن به جای پاک کردن
        try:
            await query.edit_message_caption(
                caption=f"❌ **رد شده**\n\n🆔 آیدی کاربر: `{user_id}`\n👤 وضعیت: رسید رد شد.",
                parse_mode='Markdown',
                reply_markup=None
            )
        except Exception as e:
            print(f"Error updating caption: {e}")

# --- راه‌اندازی ---
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CallbackQueryHandler(admin_action, pattern='^approve_|^reject_|^sendlink_'))
    application.add_handler(CallbackQueryHandler(admin_reply_button, pattern='^reply_|^cancel_reply'))
    application.add_handler(MessageHandler(filters.PHOTO, handle_receipt))
    
    async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get('reply_to'):
            await handle_admin_reply_text(update, context)
        else:
            await handle_text_messages(update, context)
            
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    print("ربات روشن شد...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
