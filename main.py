from flask import Flask
import threading
import os
from dotenv import load_dotenv
load_dotenv()

from google import genai
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

# --- Flask برای زنده نگه داشتن روی Render ---
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)
# --- تنظیمات بات ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
GROUP_ID = -1004453681781

client = genai.Client(api_key=GEMINI_KEY)

PROMPT = """تو یک دستیار سیگنال ترید هستی. این ورودی را به این قالب تمیز تبدیل کن:

📊 سیگنال جدید

💰 ارز:
📈 جهت: LONG / SHORT
🎯 ورود:
🎯 حد سود 1:
🎯 حد سود 2:
🛑 حد ضرر:
⏰ تایم فریم:
"""

async def start(update, context):
    await update.message.reply_text("سلام! سیگنال رو بفرست تا بفرستم توی گروه ✅")

async def handle_text(update, context):
    if update.message.chat.type != "private":
        return
    await update.message.reply_text("دارم تحلیل میکنم... ⏳")
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=[PROMPT + "\n\nمتن سیگنال:\n" + update.message.text]
        )
        await context.bot.send_message(chat_id=GROUP_ID, text=response.text)
        await update.message.reply_text("فرستادم توی گروه ✅")
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")

async def handle_photo(update, context):
    if update.message.chat.type != "private":
        return
    await update.message.reply_text("دارم عکس رو میخونم... ⏳")
    try:
        photo = await update.message.photo[-1].get_file()
        photo_bytes = await photo.download_as_bytearray()
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=[PROMPT, genai.types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg")]
        )
        await context.bot.send_message(chat_id=GROUP_ID, text=response.text)
        await update.message.reply_text("فرستادم توی گروه ✅")
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")

def main():
    request = HTTPXRequest(connect_timeout=30, read_timeout=30, write_timeout=30, pool_timeout=30)
    telegram_app = Application.builder().token(BOT_TOKEN).request(request).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    print("Bot started...")
    telegram_app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    main()