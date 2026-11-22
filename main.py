import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = -1004453681781
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # آیدی عددی اکانت اصلیت رو توی Render بذار

# حافظه موقت
pending_users = {}

async def make_fresh_invite_link(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        invite = await context.bot.create_chat_invite_link(
            chat_id=GROUP_ID,
            member_limit=1,
            name=f"user_{user_id}"
        )
        logger.info(f"LINK OK for {user_id}: {invite.invite_link}")
        return invite.invite_link
    except Exception as e:
        logger.error(f"LINK FAILED for {user_id}: {e}")
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"❌ خطا در ساخت لینک برای {user_id}:\n{e}")
        except:
            pass
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("✅ ثبت نام کردم", callback_data="registered")]]
    await update.message.reply_text(
        "سلام! به ربات رهاورد خوش اومدی 👋\n\nبرای ورود به گروه VIP روی دکمه زیر بزن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data

    if data == "registered":
        pending_users[user.id] = user
        await query.edit_message_text("✅ درخواستت ثبت شد. منتظر تایید مدیر باش...")

        keyboard = [
            [InlineKeyboardButton("✅ تایید کن", callback_data=f"approve_{user.id}"),
             InlineKeyboardButton("❌ رد کن", callback_data=f"reject_{user.id}")]
        ]
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📩 درخواست جدید:\n\nنام: {user.first_name}\nیوزرنیم: @{user.username}\nآیدی: {user.id}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("approve_"):
        target_id = int(data.split("_")[1])
        if target_id not in pending_users:
            await query.edit_message_text("این کاربر دیگه توی لیست نیست.")
            return

        await query.edit_message_text("در حال ساخت لینک تازه...")
        link = await make_fresh_invite_link(context, target_id)

        if not link:
            await query.edit_message_text("❌ ربات نتونست لینک بسازه. خطا رو برات فرستادم، ببین چی نوشته.")
            return

        await context.bot.send_message(
            chat_id=target_id,
            text=f"🎉 تایید شدی!\n\nبرای ورود به گروه روی لینک زیر بزن (فقط برای تو و فقط ۱ بار):\n\n{link}"
        )
        await query.edit_message_text(f"✅ لینک تازه برای {target_id} فرستاده شد.")
        del pending_users[target_id]

    elif data.startswith("reject_"):
        target_id = int(data.split("_")[1])
        if target_id in pending_users:
            del pending_users[target_id]
        await context.bot.send_message(chat_id=target_id, text="❌ درخواستت رد شد.")
        await query.edit_message_text("❌ رد شد.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
