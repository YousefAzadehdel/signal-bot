from flask import Flask
import threading
import os
import re
import time
from dotenv import load_dotenv
load_dotenv()

from google import genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
GROUP_ID = -1004453681781
ADMIN_IDS = [5386696088]

MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash"]

client = genai.Client(api_key=GEMINI_KEY)

def generate_with_fallback(contents):
    last_error = None
    for model in MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=contents)
            if resp.text:
                return resp.text
        except Exception as e:
            last_error = e
            print(f"Model {model} failed: {e}")
            continue
    raise last_error if last_error else Exception("All Gemini models failed")

FOOTER = """

---
⚠️ WARNING / هشدار / ПРЕДУПРЕЖДЕНИЕ

🇮🇷 حد ضرر تمام معاملات باز شما نباید بیشتر از 3% باشد لطفا در انتخاب اهرم دقت فرمایید

🇬🇧 The stop-loss of all your open trades should not be more than 3%. Please be careful when choosing leverage.

🇷🇺 Стоп-лосс всех ваших открытых сделок не должен быть больше 3%. Пожалуйста, будьте осторожны при выборе кредитного плеча.
"""

PROMPT = """You are a trading signal assistant. Convert this to ENGLISH ONLY signal.

There are 3 possible inputs:
1. TEXT ONLY: e.g. ارز: LTC فروش ورود: 45.38 حد سود 1: 45.08 حد ضرر: 45.69
2. PHOTO ONLY: screenshot with chart + text inside image like LTC/USDT Sell Enter Limit: 45.38 TP1: 45.08 SL: 45.69
3. PHOTO + CAPTION: chart image + caption text. Use BOTH. Caption has priority if conflict.

Also: PHOTO ONLY CHART: if no text in image, read the chart. Green box = LONG, Red box = SHORT. Top of box = TP, Bottom = Entry/SL. Read numbers from Y-axis.

Output EXACTLY like this - no emojis, no extra lines:

LTC SHORT

Entry : 45.38
TP1 = 45.08
TP2 = 44.76
TP3 = 44.45
TP4 = 44.13
SL = 45.69

Rules:
- Output MUST be 100% ENGLISH. Never Persian.
- Translate: Sell / فروش = SHORT, Buy / خرید = LONG
- Pair: Read ANY pair from source. Remove /USDT. LTC/USDT -> LTC, ENAUSDT -> ENA. Never use a fixed list.
- Entry: From Enter Limit / Entry / ورود / MARKET. If By Market / SELL MARKET / BUY MARKET write By Market.
- TPs: EXACTLY same number as source. If 4 TPs, output 4. Never invent, never delete.
- SL: From Stop Loss / حد ضرر / STOP. Remove (0.68%) - only number.
- Ignore Forwarded from, Timeframe, emojis.
- Keep numbers exactly as in source.
- If input is NOT a trading signal (greeting, hello, random text), output exactly: NOT_A_SIGNAL
"""

PROMPT_CHART_ONLY = """Read this TradingView chart. Output ONLY:
PAIR LONG/SHORT
Entry : X
TP1 = X
TP2 = X
TP3 = X
TP4 = X
SL = X
Rules:
- Pair: read ANY pair from bottom of image. ENAUSDT -> ENA. Do NOT use a fixed list.
- If text says SELL MARKET / BUY MARKET, Entry = By Market, Direction = SHORT for SELL, LONG for BUY
- Green box = LONG, Red box = SHORT. Read numbers from right Y-axis labels.
- Top of red box = SL, blue lines / green boxes = TPs.
- No explanation, English only.
If this is NOT a trading chart, output exactly: NOT_A_SIGNAL"""

def to_en_num(s):
    fa = "۰۱۲۳۴۵۶۷۸۹"
    ar = "٠١٢٣٤٥٦٧٨٩"
    for i, ch in enumerate(fa):
        s = s.replace(ch, str(i))
    for i, ch in enumerate(ar):
        s = s.replace(ch, str(i))
    s = s.replace("٫", ".").replace("٬", "").replace("،", "")
    return s

def translate_and_clean(text):
    if not text:
        return text
    text = to_en_num(text)
    replacements = {
        "ورود": "Entry", "خرید": "LONG", "لانگ": "LONG", "لنگ": "LONG",
        "فروش": "SHORT", "شورت": "SHORT", "حد سود": "TP", "حد ضرر": "SL",
        "تارگت": "TP", "هدف": "TP", "استاپ": "SL", "ارز": "Pair",
        "جهت": "Direction", "تایم فریم": "Timeframe",
        "اول": "1", "دوم": "2", "سوم": "3", "چهارم": "4", "پنجم": "5", "ششم": "6", "هفتم": "7", "هشتم": "8",
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

def normalize_signal(raw_text):
    try:
        text = translate_and_clean(raw_text)
        text = re.sub(r'\(.*?%.*?\)', '', text)
        text = re.sub(r'\(.*?\)', '', text)
        # هر ارزی رو قبول کن - بدون لیست
        m_pair = re.search(r'\b([A-Z]{2,10})\s*(?:/USDT|/USD|USDT)?\b', text)
        if not m_pair:
            return text
        pair = m_pair.group(1).upper()
        # اگه کلمه اول کلمه کلیدی بود، بعدی رو بگیر - بدون لیست ارز
        for bad in ["ENTRY", "TP", "SL", "LONG", "SHORT", "SELL", "BUY", "SIGNAL", "USDT"]:
            if pair == bad:
                rest = text[m_pair.end():]
                m2 = re.search(r'\b([A-Z]{2,10})\b', rest)
                if m2:
                    pair = m2.group(1).upper()
                break
        direction = "LONG"
        if re.search(r'\b(SHORT|SELL|فروش)\b', text, re.I):
            direction = "SHORT"
        elif re.search(r'\b(LONG|BUY|خرید)\b', text, re.I):
            direction = "LONG"
        entry = None
        if re.search(r'By Market', text, re.I):
            entry = "By Market"
        else:
            m_entry = re.search(r'(?:Entry|Enter Limit|Enter|Limit)\s*[:\-–=]?\s*([0-9]+\.[0-9]+|[0-9]+)', text, re.I)
            if m_entry:
                entry = m_entry.group(1)
        tps = []
        for m in re.finditer(r'TP\s*(\d+)?\s*[:\-–=]?\s*([0-9]+\.[0-9]+|[0-9]+)', text, re.I):
            v = m.group(2)
            if v not in tps and v != entry:
                tps.append(v)
        m_sl = re.search(r'(?:SL|Stop Loss)\s*[:\-–=]?\s*([0-9]+\.[0-9]+|[0-9]+)', text, re.I)
        sl = m_sl.group(1) if m_sl else None
        if not entry or not tps:
            return text
        lines = [f"{pair} {direction}", "", f"Entry : {entry}"]
        for i, tp in enumerate(tps, 1):
            lines.append(f"TP{i} = {tp}")
        if sl:
            lines.append(f"SL = {sl}")
        return "\n".join(lines)
    except Exception as e:
        print("normalize error:", e)
        return raw_text

def parse_farsi_signal(text):
    try:
        text = to_en_num(text)
        m_pair = re.search(r'(?:ارز|Pair|جفت ارز)\s*:\s*([A-Z0-9/]+)', text, re.I)
        if m_pair:
            pair = m_pair.group(1).strip().upper().replace("/USDT", "").replace("/USD", "")
        else:
            # هر ارزی - بدون لیست
            m2 = re.search(r'\b([A-Z]{2,10})(?:/USDT|/USD|USDT)?\b', text)
            if not m2:
                return None
            pair = m2.group(1).upper()
            # اگه کلمه کلیدی بود ردش کن
            if pair in ["LONG", "SHORT", "SELL", "BUY", "ENTRY", "MARKET"]:
                return None
        direction = "LONG"
        if re.search(r'(SHORT|شورت|فروش|سل|SELL)', text, re.I):
            direction = "SHORT"
        entry = None
        if re.search(r'(By Market|MARKET|مارکت|لحظه|بازار|فعلی)', text, re.I):
            entry = "By Market"
        else:
            m_entry = re.search(r'(?:ورود|Entry|خرید|Enter Limit)\s*[:\-–=]?\s*([0-9]+\.[0-9]+|[0-9]+)', text)
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

# --- فیلتر جدید: بدون لیست ارز، فقط ساختار سیگنال ---
def is_valid_signal(text):
    if not text or "NOT_A_SIGNAL" in text:
        return False
    if not re.search(r'\d', text):
        return False
    has_direction = re.search(r'\b(LONG|SHORT)\b', text, re.I)
    has_tp = re.search(r'TP\s*\d*\s*=', text, re.I)
    has_entry = re.search(r'Entry|By Market', text, re.I)
    return bool(has_direction and has_tp and has_entry)

def is_admin(update):
    return update.effective_user and update.effective_user.id in ADMIN_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text("سلام! سیگنال رو بفرست تا بفرستم توی گروه ✅\nمتن، عکس، یا عکس + متن - هر 3 تاش رو می‌خونم")

async def handle_text(update, context):
    if update.message.chat.type != "private":
        return
    if not is_admin(update):
        return
    txt = update.message.text.strip()
    if len(txt) < 10 or not re.search(r'\d', txt):
        await update.message.reply_text("این سیگنال نیست ❌\nسیگنال رو با ارز، ورود، حد سود و حد ضرر بفرست.")
        return
    await update.message.reply_text("دارم تحلیل میکنم... ⏳")
    try:
        fast = parse_farsi_signal(txt)
        if fast and is_valid_signal(fast):
            final = fast + FOOTER
            await context.bot.send_message(chat_id=GROUP_ID, text=final)
            await update.message.reply_text("فرستادم توی گروه ✅")
            return
        raw = generate_with_fallback([PROMPT + "\n\nSignal text:\n" + txt])
        if "NOT_A_SIGNAL" in raw:
            await update.message.reply_text("این سیگنال نیست ❌")
            return
        clean = normalize_signal(raw)
        if not is_valid_signal(clean):
            await update.message.reply_text("این سیگنال نیست ❌\nنتونستم ارز، ورود و حد سود رو پیدا کنم.")
            return
        final = clean + FOOTER
        await context.bot.send_message(chat_id=GROUP_ID, text=final)
        await update.message.reply_text("فرستادم توی گروه ✅")
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")

async def handle_photo(update, context):
    if update.message.chat.type != "private":
        return
    if not is_admin(update):
        return
    await update.message.reply_text("دارم عکس رو میخونم... ⏳")
    try:
        photos = update.message.photo
        # بزرگ‌ترین عکس تا اعداد چارت خونده بشن
        photo_obj = photos[-1]
        photo = await photo_obj.get_file()
        photo_bytes = await photo.download_as_bytearray()
        img_part = genai.types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg")

        caption = update.message.caption or ""
        if caption:
            fast = parse_farsi_signal(caption)
            if fast and is_valid_signal(fast):
                final = fast + FOOTER
                await context.bot.send_message(chat_id=GROUP_ID, text=final)
                await update.message.reply_text("فرستادم توی گروه ✅")
                return
            raw = generate_with_fallback([PROMPT + f"\n\nCaption text (use this too):\n{caption}\n\nAlso read text inside image.", img_part])
        else:
            raw = generate_with_fallback([PROMPT_CHART_ONLY, img_part])

        if "NOT_A_SIGNAL" in raw:
            await update.message.reply_text("این عکس سیگنال نیست ❌")
            return
        clean = normalize_signal(raw)
        if not is_valid_signal(clean):
            await update.message.reply_text("نتونستم از عکس سیگنال دربیارم ❌\nمطمئن شو عکس چارت با اعداد واضحه.")
            return
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
