from flask import Flask
import threading
import os
import re
import time
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

# اگه اولی شلوغ بود، خودکار میره سراغ بعدی
MODELS = ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3-flash"]

client = genai.Client(api_key=GEMINI_KEY)

def generate_with_fallback(contents):
    last_error = None
    for model in MODELS:
        for attempt in range(2):
            try:
                resp = client.models.generate_content(model=model, contents=contents)
                if resp.text:
                    return resp.text
            except Exception as e:
                last_error = e
                err = str(e)
                # اگه 503 یا 404 بود، برو مدل بعدی
                if "503" in err or "404" in err or "UNAVAILABLE" in err or "NOT_FOUND" in err:
                    print(f"Model {model} failed, trying next... {err[:100]}")
                    break
                print(f"Retry {model} attempt {attempt+1}: {err[:100]}")
                time.sleep(2)
                continue
    raise last_error if last_error else Exception("All Gemini models failed")

FOOTER = """

---
⚠️ WARNING / هشدار / ПРЕДУПРЕЖДЕНИЕ

🇮🇷 حد ضرر تمام معاملات باز شما نباید بیشتر از 3% باشد لطفا در انتخاب اهرم دقت فرمایید

🇬🇧 The stop-loss of all your open trades should not be more than 3%. Please be careful when choosing leverage.

🇷🇺 Стоп-лосс всех ваших открытых сделок не должен быть больше 3%. Пожалуйста, будьте осторожны при выборе кредитного плеча.
"""

PROMPT = """You are a trading signal assistant. Convert this input to a clean signal in ENGLISH ONLY.

Format EXACTLY like this - no emojis, no extra lines:

DOGE LONG

Entry : 0.18
TP1 = 0.182
TP2 = 0.183
TP3 = 0.184
SL = 0.17

Rules - VERY IMPORTANT:
- Output MUST be 100% ENGLISH. NEVER output Persian / Arabic letters. Not even one word.
- Translate: فروش = SHORT, خرید / لانگ / لنگ = LONG, ورود = Entry, حد سود / تارگت = TP, حد ضرر = SL, ارز = Pair
- First line: PAIR + SPACE + LONG or SHORT. Example: DOGE LONG, BTC SHORT
- Second line empty, third line: Entry : NUMBER or Entry : By Market
- Then TP1 = ..., TP2 = ... - EXACTLY same number of TPs as source. If source has 2 TPs, output 2. If 5 TPs, output 5. Never invent, never delete.
- Then SL = ...
- Keep numbers exactly as in source.
- Do not add emojis, Timeframe, or any explanation.
"""

# --- تبدیل اعداد فارسی به انگلیسی ---
def to_en_num(s):
    fa = "۰۱۲۳۴۵۶۷۸۹"
    ar = "٠١٢٣٤٥٦٧٨٩"
    for i, ch in enumerate(fa):
        s = s.replace(ch, str(i))
    for i, ch in enumerate(ar):
        s = s.replace(ch, str(i))
    s = s.replace("٫", ".").replace("٬", "").replace("،", "")
    return s

# --- ترجمه کلمه های فارسی به انگلیسی ---
def translate_and_clean(text):
    if not text:
        return text
    text = to_en_num(text)
    replacements = {
        "ورود": "Entry",
        "خرید": "LONG",
        "لانگ": "LONG",
        "لنگ": "LONG",
        "فروش": "SHORT",
        "شورت": "SHORT",
        "حد سود": "TP",
        "حد ضرر": "SL",
        "تارگت": "TP",
        "هدف": "TP",
        "استاپ": "SL",
        "ارز": "Pair",
        "جهت": "Direction",
        "تایم فریم": "Timeframe",
        "اول": "1",
        "دوم": "2",
        "سوم": "3",
        "چهارم": "4",
        "پنجم": "5",
        "ششم": "6",
        "هفتم": "7",
        "هشتم": "8",
    }
    for fa, en in replacements.items():
        text = text.replace(fa, en)
    text = re.sub(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+', '', text)
    lines = [l.strip() for l in text.split('\n')]
    cleaned = []
    for l in lines:
        if l == "" and cleaned and cleaned[-1] == "":
            continue
        cleaned.append(l)
    return '\n'.join(cleaned).strip()

# --- تبدیل سریع متن فارسی بدون جمینای - همه TP ها رو میگیره ---
def parse_farsi_signal(text):
    try:
        text = to_en_num(text)
        m_pair = re.search(r'(?:ارز|Pair|جفت ارز)\s*:\s*([A-Z0-9/]+)', text, re.I)
        if m_pair:
            pair = m_pair.group(1).strip().upper().replace("/USDT", "").replace("/USD", "")
        else:
            m2 = re.search(r'\b(BTC|ETH|SOL|XRP|DOGE|ADA|AVAX|LINK|TON|NOT|XAU|GOLD|BNB|TRX|DOT)\b', text, re.I)
            if not m2:
                return None
            pair = m2.group(1).upper()

        direction = "LONG"
        if re.search(r'(SHORT|شورت|فروش|سل|SELL)', text, re.I):
            direction = "SHORT"

        entry = None
        if re.search(r'(By Market|MARKET|مارکت|لحظه|بازار|فعلی)', text, re.I):
            entry = "By Market"
        else:
            m_entry = re.search(r'(?:ورود|Entry|خرید)\s*[:\-–=]?\s*([0-9]+\.[0-9]+|[0-9]+)', text)
            if m_entry:
                entry = m_entry.group(1)

        tps = []
        for m in re.finditer(r'(?:حد\s*سود|تارگت|هدف|TP|Take Profit)\s*(?:اول|دوم|سوم|چهارم|پنجم|ششم|\d+)?\s*[:\-–=]?\s*([0-9]+\.[0-9]+|[0-9]+)', text, re.I):
            v = m.group(1)
            if v not in tps and v != entry:
                tps.append(v)
        if len(tps) <= 1:
            m_line = re.search(r'(?:حد\s*سود|تارگت).*?[:\-–=]\s*([0-9.,\s/]+)', text, re.I)
            if m_line and "/" in m_line.group(1):
                tps = [x.strip() for x in m_line.group(1).split("/") if x.strip()]

        m_sl = re.search(r'(?:حد\s*ضرر|استاپ|SL|Stop Loss)\s*[:\-–=]?\s*([0-9]+\.[0-9]+|[0-9]+)', text)
        sl = m_sl.group(1) if m_sl else None

        if not entry or not tps:
            return None

        lines = [f"{pair} {direction}", "", f"Entry : {entry}"]
        for i, tp in enumerate(tps, 1):
            lines.append(f"TP{i} = {tp}")
        if sl:
            lines.append(f"SL = {sl}")
        return "\n".join(lines)
    except Exception as e:
        print("farsi parse error:", e)
        return None

async def start(update, context):
    await update.message.reply_text("سلام! سیگنال رو بفرست تا بفرستم توی گروه ✅")

async def handle_text(update, context):
    if update.message.chat.type != "private":
        return
    await update.message.reply_text("دارم تحلیل میکنم... ⏳")
    try:
        fast = parse_farsi_signal(update.message.text)
        if fast:
            final = fast + FOOTER
            await context.bot.send_message(chat_id=GROUP_ID, text=final)
            await update.message.reply_text("فرستادم توی گروه ✅")
            return

        raw = generate_with_fallback([PROMPT + "\n\nSignal text:\n" + update.message.text])
        clean = translate_and_clean(raw)
        final = clean + FOOTER
        await context.bot.send_message(chat_id=GROUP_ID, text=final)
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
        raw = generate_with_fallback([PROMPT, genai.types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg")])
        clean = translate_and_clean(raw)
        final = clean + FOOTER
        await context.bot.send_message(chat_id=GROUP_ID, text=final)
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
