import logging
import threading
import os
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- تنظیمات ---
TOKEN = "8789507883:AAH1O5TTOoBI46e77lj958dwU8BR1vC8hN4"
ADMIN_USERNAME = "8854352160"    # 👈 آیدی عددی ادمین
CARD_NUMBER = "6219861804455185"
CARD_HOLDER = "علیرضا عباس پور"
BASE_GAME_LINK = "https://dl2.megadl.click/PS4-Hacked/GTAV/CUSA00419/BASE/"
HTML_PAGE_URL = "https://amiryry418-svg.github.io/RipperModsBot/"

PASSWORD_POOL = [
    "RipA1b2", "RipC3d4", "RipE5f6", "RipG7h8", "RipI9j0",
    "ModA1b2", "ModC3d4", "ModE5f6", "ModG7h8", "ModI9j0",
    "GTAa1b2", "GTAc3d4", "GTAe5f6", "GTAg7h8", "GTAi9j0",
    "ShopA1b2", "ShopC3d4", "ShopE5f6", "ShopG7h8", "ShopI9j0",
    "VipA1b2", "VipC3d4", "VipE5f6", "VipG7h8", "VipI9j0",
    "ProA1b2", "ProC3d4", "ProE5f6", "ProG7h8", "ProI9j0",
    "FastA1b2", "FastC3d4", "FastE5f6", "FastG7h8", "FastI9j0",
    "CoolA1b2", "CoolC3d4", "CoolE5f6", "CoolG7h8", "CoolI9j0",
    "StarA1b2", "StarC3d4", "StarE5f6", "StarG7h8", "StarI9j0",
    "GoldA1b2", "GoldC3d4", "GoldE5f6", "GoldG7h8", "GoldI9j0"
]

PRODUCTS = {
    "pack_irani": {
        "name": "پک ایرانیزه",
        "price": "398,000 تومان",
        "photo": "https://s31.uupload.ir/files/rippermodsgtavv100/Amir/4/IMG_20261021_205807_285.jpg",
        "caption": "🎮 پک ایرانیزه GTA V\n\nشامل ماشین‌های ایرانی (پژو، پراید، سمند و...)\n\n💰 قیمت: 398,000 تومان",
        "is_active": False
    },
    "pack_super": {
        "name": "سوپر پک",
        "price": "799,000 تومان",
        "photo": "https://s50.uupload.ir/files/rippermodsgtavv100/Amir/IMG_20261021_205811_651.jpg",
        "caption": "🎮 سوپر پک GTA V\n\nشامل تمام ماشین‌های لوکس و اسپرت (بوگاتی، لامبورگینی و...)\n\n💰 قیمت: 799,000 تومان",
        "is_active": True
    }
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- منوی اصلی ---
def main_menu_keyboard():
    return [
        [InlineKeyboardButton("🎮 سوپر پک", callback_data='buy_pack_super'), InlineKeyboardButton("🇮🇷 پک ایرانیزه", callback_data='buy_pack_irani')],
        [InlineKeyboardButton("📥 دانلود بیس بازی", callback_data='base_game'), InlineKeyboardButton("📞 پشتیبانی", callback_data='support')]
    ]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 👈 عضویت اجباری کاملاً حذف شد
    await update.message.reply_text("سلام! به فروشگاه RipperMods خوش آمدید.\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(main_menu_keyboard()))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'buy_pack_irani':
        await query.answer("🚧 این پک در حال آپدیت است.", show_alert=True)
    elif query.data == 'buy_pack_super':
        await send_product_info(query, 'pack_super')
    elif query.data == 'base_game':
        await query.edit_message_text(text=f"📥 **دانلود بیس بازی (الزامی)**\n\n🔗 لینک:\n`{BASE_GAME_LINK}`", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]))
    elif query.data == 'support':
        context.user_data['support_mode'] = True
        await query.edit_message_text("📞 **پشتیبانی**\n\nلطفاً مشکل خود را بنویسید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]))

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
    
    text = (
        f"شما {product['name']} را انتخاب کردید.\n\n"
        f"💰 مبلغ: {product['price']}\n"
        f"💳 شماره کارت:\n`{CARD_NUMBER}`\n"
        f"👤 به نام: {CARD_HOLDER}\n\n"
        f"📸 **عکس رسید** را ارسال کنید."
    )
    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_menu')]]
    await query.message.delete()
    await context.bot.send_message(chat_id=query.message.chat_id, text=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ لطفاً فقط عکس رسید را ارسال کنید.")
        return
    user = update.effective_user
    keyboard = [[InlineKeyboardButton("✅ تایید", callback_data=f'approve_{user.id}')], [InlineKeyboardButton("❌ رد", callback_data=f'reject_{user.id}')]]
    await context.bot.send_photo(
        chat_id=ADMIN_USERNAME, 
        photo=update.message.photo[-1].file_id, 
        caption=f"رسید جدید از: {user.first_name} (@{user.username or 'بدون آیدی'})", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await update.message.reply_text("✅ رسید شما دریافت شد. منتظر تایید باشید.")

async def handle_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('support_mode'):
        user = update.effective_user
        await context.bot.send_message(chat_id=ADMIN_USERNAME, text=f"📩 پیام پشتیبانی از: {user.first_name} (@{user.username or 'بدون آیدی'})\n\n{update.message.text}")
        await update.message.reply_text("✅ پیام شما دریافت شد.")
        context.user_data['support_mode'] = False
    else:
        await update.message.reply_text("لطفاً از منوی /start استفاده کنید.")

async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith('approve_'):
        user_id = int(data.replace('approve_', ''))
        if PASSWORD_POOL:
            password = PASSWORD_POOL.pop(0)
        else:
            await query.edit_message_caption(caption="❌ لیست رمزها تموم شده!")
            return
        
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ پرداخت تایید شد!\n\n🔑 **رمز شما:** `{password}`\n\n🔗 **لینک:**\n{HTML_PAGE_URL}",
            parse_mode='Markdown'
        )
        await query.edit_message_caption(caption=f"✅ تایید شد. رمز: {password}")
        
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
    application.add_handler(CallbackQueryHandler(button_handler, pattern='^buy_|^support|^base_game'))
    application.add_handler(CallbackQueryHandler(pay_handler, pattern='^pay_'))
    application.add_handler(CallbackQueryHandler(admin_action, pattern='^approve_|^reject_'))
    application.add_handler(CallbackQueryHandler(back_to_menu, pattern='^back_to_menu'))
    application.add_handler(MessageHandler(filters.PHOTO, handle_receipt))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_support_message))

    print("ربات روشن شد...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
