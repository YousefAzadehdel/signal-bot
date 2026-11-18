from flask import Flask
import threading
import os
import re
import time
from dotenv import load_dotenv
load_dotenv()

from google import genai
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
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

REF_LINK = "https://www.thetruetrade.io/r/37681671"
GROUP_LINK = "https://t.me/+JBTuusEfwc4zZTZi"
used_uids = set()

MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
client = genai.Client(api_key=GEMINI_KEY, http_options={"api_version": "v1beta"})

def generate_with_fallback(contents):
    last_error = None
    for model in MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=contents)
            if resp.text:
                return resp.text
        except Exception as e:
            last_error = e
            continue
    raise last_error if last_error else Exception("All Gemini models failed")

FOOTER = """

---
⚠️ WARNING / هشدار / ПРЕДУПРЕЖДЕНИЕ

🇮🇷 حد ضرر تمام معاملات باز شما نباید بیشتر از 3% از کل دارایتان باشد. لطفا در انتخاب اهرم دقت فرمایید

🇬🇧 The stop-loss of all your open trades should not be more than 3% of your total capital. Please be careful when choosing leverage.

🇷🇺 Стоп-лосс всех ваших открытых сделок не должен превышать 3% от вашего общего капитала. Пожалуйста, будьте осторожны при выборе кредитного плеча.
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
- Pair: Remove /USDT. LTC/USDT -> LTC
- Entry: From Enter Limit / Entry / ورود. If By Market write By Market.
- TPs: EXACTLY same number as source. If 4 TPs, output 4. Never invent, never delete.
- SL: From Stop Loss / حد ضرر. Remove (0.68%) - only number.
- Ignore chart, Forwarded from, Timeframe, emojis.
- Keep numbers exactly as in source.
"""

PROMPT_CHART_ONLY = """You are a TradingView chart reader. This image is a CHART ONLY, no text signal. Read price levels from RIGHT SIDE Y-axis.
Green box on top = LONG, Red box on top = SHORT.
HORIZONTAL LINES color DOES NOT MATTER - BLACK, BLUE, GREEN, YELLOW, ORANGE, PURPLE, WHITE all count.
SIGNAL line = thick line + has PRICE LABEL BOX on right side. GRID line = thin, faint, NO label. IGNORE grid lines.
Entry = dotted line price / current price. TPs = signal lines on profit side, closest to farthest. SL = opposite side red box edge.
Example:
XRP LONG
Entry : 0.9440
TP1 = 0.9594
TP2 = 0.9771
TP3 = 0.9945
TP4 = 1.0129
SL = 0.9260
Rules: Copy numbers EXACTLY from right side labels. Never guess. 100% ENGLISH. No explanation."""

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
        m_pair = re.search(r'\b([A-Z]{2,10})\s*(?:/USDT|/USD|USDT)?\b', text)
        if not m_pair:
            return text
        pair = m_pair.group(1).upper()
        for bad in ["ENTRY", "TP", "SL", "LONG", "SHORT", "SELL", "BUY", "SIGNAL", "USDT"]:
            if pair == bad:
                m2 = re.search(r'\b(BTC|ETH|SOL|XRP|DOGE|ADA|AVAX|LINK|TON|NOT|LTC|XAU|GOLD|BNB|TRX|DOT|PEPE|SHIB)\b', text, re.I)
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
            m2 = re.search(r'\b(BTC|ETH|SOL|XRP|DOGE|ADA|AVAX|LINK|TON|NOT|XAU|GOLD|BNB|TRX|DOT|LTC)\b', text, re.I)
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

def is_admin(update):
    return update.effective_user and update.effective_user.id in ADMIN_IDS

WELCOME_TEXT = (
    "🔗 لینک ثبت‌نام من:\n"
    f"{REF_LINK}\n\n"
    "🇮🇷 آیا با این لینک ثبت‌نام انجام دادید؟\n"
    "🇬🇧 Did you register with this link?\n"
    "🇷🇺 Вы зарегистрировались по этой ссылке?\n"
)

def get_yes_no_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله / Yes / Да", callback_data="ref_yes"),
         InlineKeyboardButton("❌ خیر / No / Нет", callback_data="ref_no")]
    ])

async def send_ref_message(update, context):
    if update.message:
        await update.message.reply_text(WELCOME_TEXT, reply_markup=get_yes_no_keyboard())

async def ref_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "ref_yes":
        context.user_data["waiting_uid"] = True
        await query.message.reply_text(
            "🇮🇷 لطفا UID خودت رو توی صرافی بفرست (فقط عدد)\n"
            "🇬🇧 Please send your UID in the exchange (only number)\n"
            "🇷🇺 Пожалуйста, отправьте ваш UID на бирже (только цифры)"
        )
    elif query.data == "ref_no":
        await query.message.reply_text(
            "🇮🇷 اول با لینک بالا ثبت‌نام کن، بعد دوباره بیا و بله رو بزن\n"
            "🇬🇧 First register with the link above, then come back and press Yes\n"
            "🇷🇺 Сначала зарегистрируйтесь по ссылке выше, затем вернитесь и нажмите Да",
            reply_markup=get_yes_no_keyboard()
        )

async def handle_user_uid(update, context):
    if not context.user_data.get("waiting_uid"):
        await send_ref_message(update, context)
        return
    text = update.message.text.strip() if update.message.text else ""
    if not text.isdigit():
        await update.message.reply_text("🇮🇷 فقط عدد بفرست!\n🇬🇧 Send only numbers!\n🇷🇺 Отправляйте только цифры!")
        return
    if text in used_uids:
        await update.message.reply_text("🇮🇷 این UID قبلا استفاده شده!\n🇬🇧 This UID was already used!\n🇷🇺 Этот UID уже был использован!")
        return
    used_uids.add(text)
    context.user_data["waiting_uid"] = False
    await update.message.reply_text(
        "✅ تایید شد! / Confirmed! / Подтверждено!\n\n"
        "🇮🇷 این لینک گروه سیگنال:\n🇬🇧 Here is the signal group link:\n🇷🇺 Вот ссылка на группу сигналов:\n\n"
        f"{GROUP_LINK}"
    )
    await context.bot.send_message(chat_id=ADMIN_IDS[0], text=f"🔔 کاربر جدید تایید شد (خودکار)\n\n👤 {update.effective_user.first_name} @{update.effective_user.username}\n🔢 UID: {text}\n👥 ID: {update.effective_user.id}\n\nتوی پنل رفرال چک کن.")

async def start(update, context):
    if is_admin(update):
        await update.message.reply_text("سلام! سیگنال رو بفرست تا بفرستم توی گروه ✅\nمتن، عکس، یا عکس + متن - هر 3 تاش رو می‌خونم")
    else:
        await send_ref_message(update, context)

async def handle_text(update, context):
    if update.message.chat.type != "private":
        return
    if not is_admin(update):
        await handle_user_uid(update, context)
        return

    text = update.message.text.strip()

    # --- فیلتر جدید: اگه سیگنال نبود، نفرست توی گروه ---
    signal_keywords = ["ارز", "ورود", "حد سود", "حد ضرر", "تارگت", "Entry", "entry", "TP", "SL", "LONG", "SHORT", "خرید", "فروش", "لانگ", "شورت", "BTC", "ETH", "SOL", "XRP", "DOGE", "LTC", "XAU", "GOLD", "BNB"]
    has_number = any(c.isdigit() for c in text)
    is_signal = has_number and any(k in text for k in signal_keywords)

    if not is_signal:
        await update.message.reply_text("سلام! 👋\nسیگنال رو بفرست تا بفرستم توی گروه ✅\n(این پیام چون سیگنال نبود، توی گروه نرفت)")
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
        clean = normalize_signal(raw)
        final = clean + FOOTER
        await context.bot.send_message(chat_id=GROUP_ID, text=final)
        await update.message.reply_text("فرستادم توی گروه ✅")
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")

async def handle_photo(update, context):
    if update.message.chat.type != "private":
        return
    if not is_admin(update):
        await send_ref_message(update, context)
        return
    await update.message.reply_text("دارم عکس رو میخونم... ⏳")
    try:
        photo = await update.message.photo[-1].get_file()
        photo_bytes = await photo.download_as_bytearray()
        img_part = genai.types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg")
        caption = update.message.caption or ""
        if caption:
            fast = parse_farsi_signal(caption)
            if fast:
                final = fast + FOOTER
                await context.bot.send_message(chat_id=GROUP_ID, text=final)
                await update.message.reply_text("فرستادم توی گروه ✅")
                return
            raw = generate_with_fallback([PROMPT + f"\n\nCaption text (use this too):\n{caption}\n\nAlso read text inside image.", img_part])
        else:
            raw = generate_with_fallback([PROMPT_CHART_ONLY, img_part])
        clean = normalize_signal(raw)
        final = clean + FOOTER
        await context.bot.send_message(chat_id=GROUP_ID, text=final)
        await update.message.reply_text("فرستادم توی گروه ✅")
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")

def main():
    request = HTTPXRequest(connect_timeout=30, read_timeout=30, write_timeout=30, pool_timeout=30)
    telegram_app = Application.builder().token(BOT_TOKEN).request(request).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(ref_button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    print("Bot started...")
    telegram_app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    main()
