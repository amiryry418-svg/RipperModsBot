import logging
import secrets
import string
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- تنظیمات ---
# ۱. توکن ربات
TOKEN = "8789507883:AAGrh6L5HXRgfKW18EM1uCYz-mGHLkVXwug"  # 👈 توکن خودت

# ۲. آیدی ادمین (بدون @)
ADMIN_USERNAME = "Rippermw"    

# ۳. اطلاعات پرداخت
CARD_NUMBER = "6219861804455185"
CARD_HOLDER = "علیرضا عباس پور"

# ۴. لینک بیس بازی
BASE_GAME_LINK = "https://dl2.megadl.click/PS4-Hacked/GTAV/CUSA00419/BASE/"

# ۵. آدرس سرور خودت (جایی که فایل‌های PHP و HTML آپلود کردی)

SERVER_URL = "https://rippermods.infinityfreeapp.com/check_password.php" 
HTML_PAGE_URL = "https://rippermods.infinityfreeapp.com/index.html"

# محصولات
PRODUCTS = {
    "pack_irani": {
        "name": "پک ایرانیزه",
        "price": "398,000 تومان",
        "photo": "https://s31.uupload.ir/files/rippermodsgtavv100/Amir/4/IMG_20261021_205807_285.jpg",
        "link": "لینک_دانلود_پک_ایرانیزه_رو_اینجا_بذار", 
        "caption": "🎮 پک ایرانیزه GTA V\n\nشامل ماشین‌های ایرانی (پژو، پراید، سمند و...)\n\n💰 قیمت: 398,000 تومان",
        "is_active": False
    },
    "pack_super": {
        "name": "سوپر پک",
        "price": "799,000 تومان",
        "photo": "https://s50.uupload.ir/files/rippermodsgtavv100/Amir/IMG_20261021_205811_651.jpg",
        "link": HTML_PAGE_URL,  # اینجا لینک صفحه HTML رو گذاشتیم
        "caption": "🎮 سوپر پک GTA V\n\nشامل تمام ماشین‌های لوکس و اسپرت (بوگاتی، لامبورگینی و...)\n\n💰 قیمت: 799,000 تومان",
        "is_active": True
    }
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- تابع ارسال رمز به سرور ---
def send_password_to_server(password):
    """ارسال رمز به سرور برای ذخیره در فایل passwords.txt"""
    try:
        response = requests.post(
            SERVER_URL,
            json={"action": "save", "password": password},
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        print(f"خطا در ارسال به سرور: {e}")
        return False

# --- منوی اصلی ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🇮🇷 پک ایرانیزه", callback_data='buy_pack_irani')],
        [InlineKeyboardButton("🏎️ سوپر پک", callback_data='buy_pack_super')],
        [InlineKeyboardButton("📥 دانلود بیس بازی (الزامی)", callback_data='base_game')],
        [InlineKeyboardButton("📞 پشتیبانی", callback_data='support')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "سلام! به فروشگاه RipperMods خوش آمدید.\nلطفاً یکی از پک‌های زیر را انتخاب کنید:",
        reply_markup=reply_markup
    )

# --- مدیریت دکمه‌ها ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'buy_pack_irani':
        await query.answer("🚧 این پک در حال آپدیت است و فعلاً غیرفعال می‌باشد.", show_alert=True)
    elif query.data == 'buy_pack_super':
        await send_product_info(query, 'pack_super')
    elif query.data == 'base_game':
        await query.edit_message_text(
            text=f"📥 **دانلود بیس بازی (الزامی)**\n\n"
                 f"⚠️ برای نصب هر کدوم از پک‌ها، ابتدا باید فایل **بیس بازی** رو دانلود و نصب کنید.\n\n"
                 f"🔗 لینک دانلود بیس:\n`{BASE_GAME_LINK}`",
            parse_mode='Markdown'
        )
    elif query.data == 'support':
        context.user_data['support_mode'] = True
        await query.edit_message_text(
            "📞 **پشتیبانی**\n\n"
            "لطفاً مشکل یا سوال خود را همینجا بنویسید و ارسال کنید. "
            "پیام شما مستقیماً برای تیم پشتیبانی ارسال می‌شود.",
            parse_mode='Markdown'
        )

async def send_product_info(query, product_key):
    product = PRODUCTS[product_key]
    keyboard = [
        [InlineKeyboardButton("💳 خرید و پرداخت", callback_data=f'pay_{product_key}')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    caption = product['caption'] + "\n\n⚠️ **توجه:** برای استفاده از این پک، باید بیس بازی رو هم دانلود کنید."
    
    await query.message.reply_photo(
        photo=product['photo'],
        caption=caption,
        reply_markup=reply_markup
    )
    await query.message.delete()

# --- صفحه پرداخت ---
async def pay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    product_key = query.data.replace('pay_', '')
    product = PRODUCTS[product_key]
    
    text = (
        f"شما {product['name']} را انتخاب کردید.\n\n"
        f"💰 مبلغ: {product['price']}\n"
        f"💳 لطفاً مبلغ را به شماره کارت زیر واریز کنید:\n"
        f"`{CARD_NUMBER}`\n"
        f"👤 به نام: {CARD_HOLDER}\n\n"
        f"📸 سپس **عکس رسید** را همینجا ارسال کنید.\n\n"
        f"⚠️ **یادآوری:** دانلود بیس بازی الزامی است."
    )
    
    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text=text, parse_mode='Markdown', reply_markup=reply_markup)

# --- دریافت رسید ---
async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ لطفاً فقط عکس رسید را ارسال کنید.")
        return

    user = update.effective_user
    caption = f"رسید جدید از: {user.first_name} (@{user.username or 'بدون آیدی'})\n\n"
    
    keyboard = [
        [InlineKeyboardButton("✅ تایید و ارسال لینک", callback_data=f'approve_{user.id}')],
        [InlineKeyboardButton("❌ رد", callback_data=f'reject_{user.id}')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_photo(
        chat_id=f"@{ADMIN_USERNAME}",
        photo=update.message.photo[-1].file_id,
        caption=caption,
        reply_markup=reply_markup
    )
    
    await update.message.reply_text("✅ رسید شما دریافت شد. پس از تایید، لینک دانلود برای شما ارسال می‌شود.")

# --- دریافت پیام‌های پشتیبانی ---
async def handle_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('support_mode'):
        user = update.effective_user
        text = update.message.text
        
        await context.bot.send_message(
            chat_id=f"@{ADMIN_USERNAME}",
            text=f"📩 پیام پشتیبانی جدید از: {user.first_name} (@{user.username or 'بدون آیدی'})\n\n{text}"
        )
        
        await update.message.reply_text("✅ پیام شما دریافت شد. تیم پشتیبانی به زودی پاسخ می‌دهد.")
        context.user_data['support_mode'] = False
    else:
        await update.message.reply_text("لطفاً از منوی /start استفاده کنید.")

# --- تایید یا رد توسط ادمین ---
async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith('approve_'):
        user_id = int(data.replace('approve_', ''))
        
        # تولید رمز تصادفی ۸ کاراکتری
        alphabet = string.ascii_letters + string.digits
        password = ''.join(secrets.choice(alphabet) for _ in range(8))
        
        # ارسال رمز به سرور
        if send_password_to_server(password):
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ پرداخت شما تایید شد!\n\n"
                     f"🔑 **رمز دسترسی شما:** `{password}`\n\n"
                     f"🔗 **لینک دانلود:**\n{HTML_PAGE_URL}\n\n"
                     f"⚠️ در صفحه باز شده، رمز بالا را وارد کنید. "
                     f"(این رمز فقط یک بار قابل استفاده است)",
                parse_mode='Markdown'
            )
            await query.edit_message_caption(caption="✅ تایید شد و رمز برای کاربر ارسال گردید.")
        else:
            await query.edit_message_caption(caption="❌ خطا در ارسال رمز به سرور. لطفاً دوباره تلاش کنید.")
            
    elif data.startswith('reject_'):
        user_id = int(data.replace('reject_', ''))
        await context.bot.send_message(chat_id=user_id, text="❌ رسید شما رد شد. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.")
        await query.edit_message_caption(caption="❌ رد شد.")

# --- بازگشت به منو ---
async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🇮🇷 پک ایرانیزه", callback_data='buy_pack_irani')],
        [InlineKeyboardButton("🏎️ سوپر پک", callback_data='buy_pack_super')],
        [InlineKeyboardButton("📥 دانلود بیس بازی (الزامی)", callback_data='base_game')],
        [InlineKeyboardButton("📞 پشتیبانی", callback_data='support')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("لطفاً یکی از پک‌های زیر را انتخاب کنید:", reply_markup=reply_markup)

def main():
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