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

CARD_NUMBER = "6219861804455185"
CARD_HOLDER = "علیرضا عباس پور"
BASE_GAME_LINK = "https://dl2.megadl.click/PS4-Hacked/GTAV/CUSA00419/BASE/"
DIRECT_DOWNLOAD_LINK = "https://my.uupload.ir/d/D9axZ" 
UPLOAD_PASSWORD = "mwrippersfinest" 

WELCOME_PHOTO = "https://s50.uupload.ir/files/rippermodsgtavv100/Amir/IMG_20261021_205811_651.jpg"

DISCOUNT_CODE = "Ripper_mods new bot"
DISCOUNT_AMOUNT = 99000 
DISCOUNT_EXPIRY = datetime.datetime.now() + datetime.timedelta(days=3)

PRODUCTS = {
    "pack_irani": {"name": "پک ایرانیزه", "price": 398000, "photo": "https://s31.uupload.ir/files/rippermodsgtavv100/Amir/4/IMG_20261021_205807_285.jpg"},
    "pack_super": {"name": "سوپر پک", "price": 799000, "photo": "https://s50.uupload.ir/files/rippermodsgtavv100/Amir/IMG_20261021_205811_651.jpg"}
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- وب‌سرور (برای زنده موندن در Render) ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- توابع کمکی برای ساخت دکمه‌ها ---
def main_menu_keyboard():
    return [
        [InlineKeyboardButton("🛒 ورود به فروشگاه", callback_data='shop_menu')],
        [InlineKeyboardButton("📥 دریافت مجدد لینک دانلود", callback_data='request_relink')],
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

# --- استارت ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear() 
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
    user = query.from_user
    
    # 👈 اینجا باگ عکس حل شده. برای دکمه‌های زیر عکس، پیام جدید میفرستیم.
    if query.data == 'shop_menu':
        await query.message.reply_text("🛒 **به فروشگاه خوش آمدید!**\n\nچه کاری می‌خواهید انجام دهید؟", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(shop_menu_keyboard()))
    elif query.data == 'request_relink':
        keyboard = [[InlineKeyboardButton("✅ ارسال لینک", callback_data=f'sendlink_{user.id}')]]
        await context.bot.send_message(chat_id=ADMIN_MAIN_ID, text=f"🔄 **درخواست لینک مجدد**\nاز: {user.first_name}", reply_markup=InlineKeyboardMarkup(keyboard))
        await query.message.reply_text("✅ درخواست شما به ادمین ارسال شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_main')]]))
    elif query.data == 'support':
        context.user_data['support_mode'] = True
        await query.message.reply_text("📞 **پشتیبانی**\n\nلطفاً مشکل خود را بنویسید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_main')]]))

    # 👈 از اینجا به بعد پیام‌ها متنی هستن و با edit کار می‌کنن
    elif query.data == 'back_to_main':
        await query.edit_message_text("لطفاً از منوی اصلی انتخاب کنید:", reply_markup=InlineKeyboardMarkup(main_menu_keyboard()))
    elif query.data == 'menu_products':
        await query.edit_message_text("🎮 **پک‌های موجود:**", reply_markup=InlineKeyboardMarkup(products_keyboard()))
    elif query.data == 'buy_pack_irani': 
        await query.answer("🚧 در حال آپدیت.", show_alert=True)
    elif query.data == 'buy_pack_super': 
        await send_product_info(query, context, 'pack_super')
    elif query.data == 'base_game': 
        await query.edit_message_text(text=f"📥 **دانلود بیس بازی:**\n\n`{BASE_GAME_LINK}`", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='menu_products')]]))
    
    elif query.data == 'req_build_pack':
        context.user_data['awaiting_service'] = "ساخت پک اختصاصی GTA V"
        await query.edit_message_text("🏗 **سفارش ساخت پک**\n\nلطفاً جزئیات درخواست خود را ارسال کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data='shop_menu')]]))
    elif query.data == 'req_port_nostalgia':
        context.user_data['awaiting_service'] = "پورت بازی‌های نوستالژی"
        await query.edit_message_text("🕹 **سفارش پورت نوستالژی**\n\nلطفاً نام بازی و کنسول مقصد را ارسال کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data='shop_menu')]]))

# --- توابع محصولات و تخفیف ---
async def send_product_info(query, context, product_key):
    product = PRODUCTS[product_key]
    context.user_data['current_product'] = product_key
    price_text = f"{product['price']:,} تومان"
    caption = f"🎮 {product['name']} GTA V\n\n💰 قیمت: {price_text}"
    
    keyboard = [
        [InlineKeyboardButton("🎟 وارد کردن کد تخفیف", callback_data='enter_discount')],
        [InlineKeyboardButton("💳 خرید و پرداخت", callback_data=f'pay_{product_key}')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='menu_products')]
    ]
    await query.message.reply_photo(photo=product['photo'], caption=caption, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def enter_discount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['awaiting_discount'] = True
    await query.edit_message_text("🎟 **لطفاً کد تخفیف خود را ارسال کنید:**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data='back_to_product')]]))

async def process_discount_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['awaiting_discount'] = False
    if update.message.text.strip() == DISCOUNT_CODE:
        if datetime.datetime.now() <= DISCOUNT_EXPIRY:
            context.user_data['discount_applied'] = True
            await update.message.reply_text(f"✅ کد تخفیف اعمال شد! {DISCOUNT_AMOUNT:,} تومان کسر شد.")
        else:
            await update.message.reply_text("❌ مهلت کد تخفیف تمام شده.")
    else:
        await update.message.reply_text("❌ کد تخفیف نامعتبر است.")
    
    product_key = context.user_data.get('current_product', 'pack_super')
    product = PRODUCTS[product_key]
    price_text = f"~~{product['price']:,} تومان~~ **{product['price'] - DISCOUNT_AMOUNT:,} تومان**" if context.user_data.get('discount_applied') else f"{product['price']:,} تومان"
    caption = f"🎮 {product['name']} GTA V\n\n💰 قیمت: {price_text}"
    keyboard = [
        [InlineKeyboardButton("🎟 وارد کردن کد تخفیف", callback_data='enter_discount')],
        [InlineKeyboardButton("💳 خرید و پرداخت", callback_data=f'pay_{product_key}')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='menu_products')]
    ]
    await update.message.reply_photo(photo=product['photo'], caption=caption, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def back_to_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = context.user_data.get('current_product', 'pack_super')
    product = PRODUCTS[product_key]
    price_text = f"~~{product['price']:,} تومان~~ **{product['price'] - DISCOUNT_AMOUNT:,} تومان**" if context.user_data.get('discount_applied') else f"{product['price']:,} تومان"
    caption = f"🎮 {product['name']} GTA V\n\n💰 قیمت: {price_text}"
    keyboard = [
        [InlineKeyboardButton("🎟 وارد کردن کد تخفیف", callback_data='enter_discount')],
        [InlineKeyboardButton("💳 خرید و پرداخت", callback_data=f'pay_{product_key}')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='menu_products')]
    ]
    await query.message.reply_photo(photo=product['photo'], caption=caption, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def pay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = query.data.replace('pay_', '')
    product = PRODUCTS[product_key]
    price_text = f"~~{product['price']:,} تومان~~ **{product['price'] - DISCOUNT_AMOUNT:,} تومان**" if context.user_data.get('discount_applied') else f"{product['price']:,} تومان"
    text = f"شما {product['name']} را انتخاب کردید.\n\n💰 مبلغ: {price_text}\n💳 کارت:\n`{CARD_NUMBER}`\n👤 نام: {CARD_HOLDER}\n\n📸 **عکس رسید** را ارسال کنید."
    keyboard = [[InlineKeyboardButton("🔙 بازگشت به محصول", callback_data='back_to_product')]]
    await query.edit_message_text(text=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

# --- هندلرهای دریافتی ---
async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return
    user = update.effective_user
    keyboard = [[InlineKeyboardButton("✅ تایید", callback_data=f'approve_{user.id}')], [InlineKeyboardButton("❌ رد", callback_data=f'reject_{user.id}')]]
    await context.bot.send_photo(chat_id=ADMIN_MAIN_ID, photo=update.message.photo[-1].file_id, caption=f"رسید جدید از: {user.first_name}", reply_markup=InlineKeyboardMarkup(keyboard))
    await update.message.reply_text("✅ رسید دریافت شد. منتظر تایید باشید.")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if context.user_data.get('support_mode'):
        keyboard = [[InlineKeyboardButton("✍️ پاسخ", callback_data=f'reply_{user.id}')]]
        await context.bot.send_message(chat_id=ADMIN_MAIN_ID, text=f"📩 پشتیبانی از {user.first_name}:\n{update.message.text}", reply_markup=InlineKeyboardMarkup(keyboard))
        await update.message.reply_text("✅ پیام دریافت شد.")
        context.user_data['support_mode'] = False
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

async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith('approve_') or data.startswith('sendlink_'):
        user_id = int(data.split('_')[1])
        sent_msg = await context.bot.send_message(chat_id=user_id, text=f"✅ **لینک:** {DIRECT_DOWNLOAD_LINK}\n🔑 **رمز:** `{UPLOAD_PASSWORD}`\n\n⚠️ ۳۰ ثانیه فرصت دارید.", parse_mode='Markdown')
        await asyncio.sleep(30)
        try: await context.bot.delete_message(chat_id=user_id, message_id=sent_msg.message_id)
        except: pass
        await query.edit_message_text("✅ لینک ارسال شد.")
    elif data.startswith('reject_'):
        user_id = int(data.replace('reject_', ''))
        await context.bot.send_message(chat_id=user_id, text="❌ رد شد.")
        await query.edit_message_caption(caption="❌ رد شد.")

# --- راه‌اندازی ---
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CallbackQueryHandler(enter_discount_handler, pattern='^enter_discount$'))
    application.add_handler(CallbackQueryHandler(back_to_product, pattern='^back_to_product$'))
    application.add_handler(CallbackQueryHandler(pay_handler, pattern='^pay_'))
    application.add_handler(CallbackQueryHandler(admin_action, pattern='^approve_|^reject_|^sendlink_'))
    application.add_handler(CallbackQueryHandler(admin_reply_button, pattern='^reply_|^cancel_reply'))
    application.add_handler(MessageHandler(filters.PHOTO, handle_receipt))
    
    async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get('reply_to'):
            await handle_admin_reply_text(update, context)
        elif context.user_data.get('awaiting_discount'):
            await process_discount_code(update, context)
        else:
            await handle_text_messages(update, context)
            
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    print("ربات روشن شد...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
