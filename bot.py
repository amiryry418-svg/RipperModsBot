import logging
import threading
import os
import random
import asyncio
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- تنظیمات ---
TOKEN = "8789507883:AAH1O5TTOoBI46e77lj958dwU8BR1vC8hN4"

# 👈 آیدی‌های جداگانه برای بخش‌های مختلف
ADMIN_MAIN_ID = "1808953401"    # ادمین اصلی (خرید، رسیدها، پشتیبانی، دریافت مجدد لینک)
ADMIN_PORT_ID = "1891282471"    # ادمین درخواست پورت

CARD_NUMBER = "6219861804455185"
CARD_HOLDER = "علیرضا عباس پور"
BASE_GAME_LINK = "https://dl2.megadl.click/PS4-Hacked/GTAV/CUSA00419/BASE/"

# 👈 لینک و رمز آپلود
DIRECT_DOWNLOAD_LINK = "https://my.uupload.ir/d/D9axZ" 
UPLOAD_PASSWORD = "mwrippersfinest" 

PRODUCTS = {
    "pack_irani": {"name": "پک ایرانیزه", "price": "398,000 تومان", "photo": "https://s31.uupload.ir/files/rippermodsgtavv100/Amir/4/IMG_20261021_205807_285.jpg", "caption": "🎮 پک ایرانیزه GTA V\n\n💰 قیمت: 398,000 تومان", "is_active": False},
    "pack_super": {"name": "سوپر پک", "price": "799,000 تومان", "photo": "https://s50.uupload.ir/files/rippermodsgtavv100/Amir/IMG_20261021_205811_651.jpg", "caption": "🎮 سوپر پک GTA V\n\n💰 قیمت: 799,000 تومان", "is_active": True}
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- وب‌سرور (API) ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}) 

@app.route('/')
def health_check(): return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- توابع ربات ---
def main_menu_keyboard():
    return [
        [InlineKeyboardButton("🎮 سوپر پک", callback_data='buy_pack_super'), InlineKeyboardButton("🇮🇷 پک ایرانیزه", callback_data='buy_pack_irani')],
        [InlineKeyboardButton("📥 دریافت مجدد لینک دانلود", callback_data='request_relink')],
        [InlineKeyboardButton("📡 درخواست پورت", callback_data='request_port')],
        [InlineKeyboardButton("📥 دانلود بیس بازی", callback_data='base_game'), InlineKeyboardButton("📞 پشتیبانی", callback_data='support')],
        [InlineKeyboardButton("📖 راهنمای نصب", callback_data='guide_menu')]
    ]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! به فروشگاه RipperMods خوش آمدید.\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(main_menu_keyboard()))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'buy_pack_irani': 
        await query.answer("🚧 در حال آپدیت.", show_alert=True)
    elif query.data == 'buy_pack_super': 
        await send_product_info(query, 'pack_super')
    elif query.data == 'base_game': 
        await query.edit_message_text(text=f"📥 **دانلود بیس بازی:**\n\n`{BASE_GAME_LINK}`", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]))
    elif query.data == 'support':
        context.user_data['support_mode'] = True
        await query.edit_message_text("📞 **پشتیبانی**\n\nلطفاً مشکل خود را بنویسید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]))
    
    elif query.data == 'request_port': 
        context.user_data['port_mode'] = True
        text = "📡 **درخواست پورت**\n\nلطفاً اطلاعات درخواست خود را در یک پیام ارسال کنید.\nمثال:\n🔹 نوع کنسول (PS4 / PS5)\n🔹 آیدی بازی (CUSA...)\n🔹 توضیحات یا مشکل شما\n\n⬇️ متن خود را ارسال کنید:"
        await query.edit_message_text(text=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]))
    
    elif query.data == 'request_relink':
        user = query.from_user
        keyboard = [[InlineKeyboardButton("✅ ارسال لینک", callback_data=f'sendlink_{user.id}')]]
        # 👈 درخواست دریافت مجدد لینک به ادمین اصلی میره
        await context.bot.send_message(
            chat_id=ADMIN_MAIN_ID, 
            text=f"🔄 **درخواست دریافت مجدد لینک**\nاز: {user.first_name} (@{user.username or 'بدون آیدی'})\n\nآیا تایید می‌کنید؟", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.edit_message_text("✅ درخواست شما برای دریافت مجدد لینک به ادمین ارسال شد. منتظر تایید باشید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]))
    
    elif query.data == 'guide_menu':
        keyboard = [
            [InlineKeyboardButton("🎮 راهنمای PS4", callback_data='guide_ps4'), InlineKeyboardButton("🎮 راهنمای PS5", callback_data='guide_ps5')],
            [InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]
        ]
        await query.edit_message_text("📖 **راهنمای نصب**\n\nلطفاً کنسول خود را انتخاب کنید:", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'guide_ps4':
        text = "🚨 **آموزش نصب پک در PS4**\n\n1️⃣ **اول:** فایل **بیس** رو نصب کنید.\n2️⃣ **دوم:** فایل **پک** رو نصب کنید.\n\n📂 همه پارت‌ها رو دانلود و فقط روی **پارت ۱** Extract بزنید.\n💾 فایل‌های **PKG** رو به فلش (exFAT) کپی کنید.\n⚙️ برید به: **Settings -> Debug settings -> Game Package installer**\nو به ترتیب: **بیس -> پک** رو نصب کنید."
        await query.edit_message_text(text=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='guide_menu')]]))
    elif query.data == 'guide_ps5':
        await query.edit_message_text("📖 **راهنمای نصب PS5**\n\nبه زودی...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='guide_menu')]]))

async def send_product_info(query, product_key):
    product = PRODUCTS[product_key]
    keyboard = [[InlineKeyboardButton("💳 خرید و پرداخت", callback_data=f'pay_{product_key}')], [InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]
    await query.message.reply_photo(photo=product['photo'], caption=product['caption'], reply_markup=InlineKeyboardMarkup(keyboard))
    await query.message.delete()

async def pay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = query.data.replace('pay_', '')
    product = PRODUCTS[product_key]
    text = f"شما {product['name']} را انتخاب کردید.\n\n💰 مبلغ: {product['price']}\n💳 شماره کارت:\n`{CARD_NUMBER}`\n👤 به نام: {CARD_HOLDER}\n\n📸 **عکس رسید** را ارسال کنید."
    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]
    await query.message.delete()
    await context.bot.send_message(chat_id=query.message.chat_id, text=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ لطفاً فقط عکس رسید را ارسال کنید.")
        return
    user = update.effective_user
    keyboard = [[InlineKeyboardButton("✅ تایید", callback_data=f'approve_{user.id}')], [InlineKeyboardButton("❌ رد", callback_data=f'reject_{user.id}')]]
    # 👈 رسید به ادمین اصلی میره
    await context.bot.send_photo(chat_id=ADMIN_MAIN_ID, photo=update.message.photo[-1].file_id, caption=f"رسید جدید از: {user.first_name} (@{user.username or 'بدون آیدی'})", reply_markup=InlineKeyboardMarkup(keyboard))
    await update.message.reply_text("✅ رسید شما دریافت شد. منتظر تایید باشید.")

async def handle_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if context.user_data.get('support_mode'):
        # 👈 پیام پشتیبانی به ادمین اصلی میره
        keyboard = [[InlineKeyboardButton("✍️ پاسخ به کاربر", callback_data=f'reply_{user.id}')]]
        await context.bot.send_message(chat_id=ADMIN_MAIN_ID, text=f"📩 پیام پشتیبانی از: {user.first_name} (@{user.username or 'بدون آیدی'})\n\n{update.message.text}", reply_markup=InlineKeyboardMarkup(keyboard))
        await update.message.reply_text("✅ پیام شما دریافت شد. تیم پشتیبانی پاسخ می‌دهد.")
        context.user_data['support_mode'] = False
        
    elif context.user_data.get('port_mode'):
        # 👈 درخواست پورت به ادمین پورت میره
        keyboard = [[InlineKeyboardButton("✍️ پاسخ به کاربر", callback_data=f'reply_{user.id}')]]
        await context.bot.send_message(
            chat_id=ADMIN_PORT_ID, 
            text=f"📡 **درخواست پورت جدید**\nاز: {user.first_name} (@{user.username or 'بدون آیدی'})\n\n{update.message.text}", 
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await update.message.reply_text("✅ درخواست پورت شما دریافت شد. به زودی توسط ادمین بررسی می‌شود.")
        context.user_data['port_mode'] = False
        
    else:
        await update.message.reply_text("لطفاً از منوی /start استفاده کنید.")

async def admin_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith('reply_'):
        user_id = int(data.replace('reply_', ''))
        context.user_data['reply_to'] = user_id
        await query.edit_message_text(text="✍️ لطفاً متن پاسخ خود را بنویسید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data='cancel_reply')]]))
    elif data == 'cancel_reply':
        context.user_data['reply_to'] = None
        await query.edit_message_text(text="❌ پاسخ لغو شد.")

async def handle_admin_reply_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.get('reply_to')
    if user_id:
        try:
            await context.bot.send_message(chat_id=user_id, text=f"📬 **پاسخ پشتیبانی:**\n\n{update.message.text}", parse_mode='Markdown')
            await update.message.reply_text("✅ پاسخ شما برای کاربر ارسال شد.")
        except Exception as e:
            await update.message.reply_text(f"❌ خطا در ارسال پاسخ: {e}")
        context.user_data['reply_to'] = None

async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith('approve_') or data.startswith('sendlink_'):
        user_id = int(data.split('_')[1])
        
        sent_message = await context.bot.send_message(
            chat_id=user_id, 
            text=(
                f"✅ **لینک دانلود برای شما ارسال شد!**\n\n"
                f"🔗 **لینک:** {DIRECT_DOWNLOAD_LINK}\n"
                f"🔑 **رمز:** `{UPLOAD_PASSWORD}`\n\n"
                f"⚠️ **هشدار مهم:** این پیام و لینک آن، **۳۰ ثانیه** دیگر به صورت خودکار حذف می‌شود! لطفاً همین الان لینک را کپی کنید. برای دریافت مجدد، از منوی ربات «دریافت مجدد لینک» را بزنید."
            ), 
            parse_mode='Markdown'
        )
        
        await asyncio.sleep(30)
        
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=sent_message.message_id)
        except Exception as e:
            print(f"خطا در پاک کردن پیام: {e}")

        await query.edit_message_text(text=f"✅ لینک برای کاربر ارسال شد و پس از ۳۰ ثانیه حذف شد.")
        
    elif data.startswith('reject_'):
        user_id = int(data.replace('reject_', ''))
        await context.bot.send_message(chat_id=user_id, text="❌ رسید شما رد شد.")
        await query.edit_message_caption(caption="❌ رد شد.")

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("لطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(main_menu_keyboard()))

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler, pattern='^buy_|^support|^base_game|^guide_menu|^guide_ps4|^guide_ps5|^request_port|^request_relink'))
    application.add_handler(CallbackQueryHandler(pay_handler, pattern='^pay_'))
    application.add_handler(CallbackQueryHandler(admin_action, pattern='^approve_|^reject_|^sendlink_'))
    application.add_handler(CallbackQueryHandler(admin_reply_button, pattern='^reply_|^cancel_reply'))
    application.add_handler(CallbackQueryHandler(back_to_menu, pattern='^back_to_menu'))
    application.add_handler(MessageHandler(filters.PHOTO, handle_receipt))
    
    async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get('reply_to'):
            await handle_admin_reply_text(update, context)
        else:
            await handle_support_message(update, context)
            
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    print("ربات روشن شد...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
