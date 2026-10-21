import logging
import secrets
import string
import requests
import threading
import os
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- تنظیمات ---
TOKEN = "8789507883:AAGrh6L5HXRgfKW18EM1uCYz-mGHLkVXwug"  # 👈 توکن ربات
ADMIN_USERNAME = "Rippermw"
CARD_NUMBER = "6219861804455185"
CARD_HOLDER = "علیرضا عباس پور"
BASE_GAME_LINK = "https://dl2.megadl.click/PS4-Hacked/GTAV/CUSA00419/BASE/"

# 👇 آدرس هاست خودت رو اینجا بذار (بدون تغییر بمونه)
SERVER_URL = "https://rippermods.infinityfreeapp.com/check_password.php" 
HTML_PAGE_URL = "https://rippermods.infinityfreeapp.com/index.html"

PRODUCTS = {
    "pack_irani": {
        "name": "پک ایرانیزه",
        "price": "398,000 تومان",
        "photo": "https://s31.uupload.ir/files/rippermodsgtavv100/Amir/4/IMG_20261021_205807_285.jpg",
        "link": "",
        "caption": "🎮 پک ایرانیزه GTA V\n\nشامل ماشین‌های ایرانی (پژو، پراید، سمند و...)\n\n💰 قیمت: 398,000 تومان",
        "is_active": False
    },
    "pack_super": {
        "name": "سوپر پک",
        "price": "799,000 تومان",
        "photo": "https://s50.uupload.ir/files/rippermodsgtavv100/Amir/IMG_20261021_205811_651.jpg",
        "link": HTML_PAGE_URL,
        "caption": "🎮 سوپر پک GTA V\n\nشامل تمام ماشین‌های لوکس و اسپرت (بوگاتی، لامبورگینی و...)\n\n💰 قیمت: 799,000 تومان",
        "is_active": True
    }
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- وب‌سرور برای Render ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- توابع ربات ---
def send_password_to_server(password):
    try:
        response = requests.post(SERVER_URL, json={"action": "save", "password": password}, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"خطا: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🇮🇷 پک ایرانیزه", callback_data='buy_pack_irani')],
        [InlineKeyboardButton("🏎️ سوپر پک", callback_data='buy_pack_super')],
        [InlineKeyboardButton("📥 دانلود بیس بازی (الزامی)", callback_data='base_game')],
        [InlineKeyboardButton("📞 پشتیبانی", callback_data='support')]
    ]
    await update.message.reply_text("سلام! به فروشگاه RipperMods خوش آمدید.\nلطفاً یکی از پک‌های زیر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'buy_pack_irani':
        await query.answer("🚧 این پک در حال آپدیت است و فعلاً غیرفعال می‌باشد.", show_alert=True)
    elif query.data == 'buy_pack_super':
        await send_product_info(query, 'pack_super')
    elif query.data == 'base_game':
        await query.edit_message_text(text=f"📥 **دانلود بیس بازی (الزامی)**\n\n⚠️ برای نصب، ابتدا بیس بازی را دانلود کنید.\n\n🔗 لینک:\n`{BASE_GAME_LINK}`", parse_mode='Markdown')
    elif query.data == 'support':
        context.user_data['support_mode'] = True
        await query.edit_message_text("📞 **پشتیبانی**\n\nلطفاً مشکل یا سوال خود را بنویسید:", parse_mode='Markdown')

async def send_product_info(query, product_key):
    product = PRODUCTS[product_key]
    keyboard = [[InlineKeyboardButton("💳 خرید و پرداخت", callback_data=f'pay_{product_key}')], [InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]
    await query.message.reply_photo(photo=product['photo'], caption=product['caption'] + "\n\n⚠️ **توجه:** دانلود بیس بازی الزامی است.", reply_markup=InlineKeyboardMarkup(keyboard))
    await query.message.delete()

async def pay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = query.data.replace('pay_', '')
    product = PRODUCTS[product_key]
    text = (f"شما {product['name']} را انتخاب کردید.\n\n💰 مبلغ: {product['price']}\n💳 شماره کارت:\n`{CARD_NUMBER}`\n👤 به نام: {CARD_HOLDER}\n\n📸 **عکس رسید** را ارسال کنید.\n\n⚠️ دانلود بیس بازی الزامی است.")
    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]
    await query.edit_message_text(text=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ لطفاً فقط عکس رسید را ارسال کنید.")
        return
    user = update.effective_user
    keyboard = [[InlineKeyboardButton("✅ تایید و ارسال لینک", callback_data=f'approve_{user.id}')], [InlineKeyboardButton("❌ رد", callback_data=f'reject_{user.id}')]]
    await context.bot.send_photo(chat_id=f"@{ADMIN_USERNAME}", photo=update.message.photo[-1].file_id, caption=f"رسید جدید از: {user.first_name} (@{user.username or 'بدون آیدی'})", reply_markup=InlineKeyboardMarkup(keyboard))
    await update.message.reply_text("✅ رسید شما دریافت شد. پس از تایید، لینک ارسال می‌شود.")

async def handle_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('support_mode'):
        user = update.effective_user
        await context.bot.send_message(chat_id=f"@{ADMIN_USERNAME}", text=f"📩 پیام پشتیبانی از: {user.first_name} (@{user.username or 'بدون آیدی'})\n\n{update.message.text}")
        await update.message.reply_text("✅ پیام شما دریافت شد. تیم پشتیبانی پاسخ می‌دهد.")
        context.user_data['support_mode'] = False
    else:
        await update.message.reply_text("لطفاً از منوی /start استفاده کنید.")

async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith('approve_'):
        user_id = int(data.replace('approve_', ''))
        alphabet = string.ascii_letters + string.digits
        password = ''.join(secrets.choice(alphabet) for _ in range(8))
        if send_password_to_server(password):
            await context.bot.send_message(chat_id=user_id, text=f"✅ پرداخت تایید شد!\n\n🔑 **رمز شما:** `{password}`\n\n🔗 **لینک:**\n{HTML_PAGE_URL}\n\n⚠️ رمز فقط یک بار قابل استفاده است.", parse_mode='Markdown')
            await query.edit_message_caption(caption="✅ تایید شد و رمز ارسال گردید.")
        else:
            await query.edit_message_caption(caption="❌ خطا در ارسال به سرور.")
    elif data.startswith('reject_'):
        user_id = int(data.replace('reject_', ''))
        await context.bot.send_message(chat_id=user_id, text="❌ رسید شما رد شد.")
        await query.edit_message_caption(caption="❌ رد شد.")

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🇮🇷 پک ایرانیزه", callback_data='buy_pack_irani')],
        [InlineKeyboardButton("🏎️ سوپر پک", callback_data='buy_pack_super')],
        [InlineKeyboardButton("📥 دانلود بیس بازی (الزامی)", callback_data='base_game')],
        [InlineKeyboardButton("📞 پشتیبانی", callback_data='support')]
    ]
    await query.edit_message_text("لطفاً یکی از پک‌های زیر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    # راه‌اندازی وب‌سرور برای Render در پس‌زمینه
    threading.Thread(target=run_flask, daemon=True).start()
    
    # شروع ربات تلگرام
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler, pattern='^buy_|^support|^base_game'))
    application.add_handler(CallbackQueryHandler(pay_handler, pattern='^pay_'))
    application.add_handler(CallbackQueryHandler(admin_action, pattern='^approve_|^reject_'))
    application.add_handler(CallbackQueryHandler(back_to_menu, pattern='^back_to_menu'))
    
    application.add_handler(MessageHandler(filters.PHOTO, handle_receipt))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_support_message))

    print("ربات روشن شد...")
    application.run_polling()

if __name__ == '__main__':
    main()
