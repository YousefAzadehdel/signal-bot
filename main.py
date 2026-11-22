from flask import Flask
import threading
import os
import re
import time
import json
from dotenv import load_dotenv
load_dotenv()

from google import genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatJoinRequestHandler, ContextTypes, filters
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

REFERRAL_LINK = "https://www.thetruetrade.io/r/37681671"
REFERRAL_CODE = "37681671"

DATA_FILE = "users.json"

def load_users():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {}

def save_users(data):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print("save error:", e)

users_db = load_users()
pending_joins = {}

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
        m_pair = re.search(r'\b([A-Z]{2,10})\s*(?:/USDT|/USD|USDT)?\b', text)
        if not m_pair:
            return text
        pair = m_pair.group(1).upper()
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
            m2 = re.search(r'\b([A-Z]{2,10})(?:/USDT|/USD|USDT)?\b', text)
            if not m2:
                return None
            pair = m2.group(1).upper()
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

# ===== رفرال + تایید دستی - سه زبانه =====

def get_welcome_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔗 ثبت نام / Sign Up / Регистрация", url=REFERRAL_LINK)],
        [InlineKeyboardButton("✅ ثبت نام کردم / I Registered / Я зарегистрировался", callback_data="registered")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard(user_id):
    keyboard = [
        [
            InlineKeyboardButton("✅ تایید کن / Approve / Одобрить", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton("❌ رد کن / Reject / Отклонить", callback_data=f"reject_{user_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_admin(update):
        await update.message.reply_text(
            "سلام ادمین! / Hello Admin! / Привет, админ!\n"
            "سیگنال رو بفرست تا بفرستم توی گروه ✅\n"
            "Send signal, I will forward it to group ✅\n"
            "Отправь сигнал, я перешлю его в группу ✅\n\n"
            "برای گرفتن لینک رفرال بزن /link"
        )
        return

    uid = str(user.id)
    if uid in users_db and users_db[uid].get("registered") and users_db[uid].get("status") != "rejected":
        await update.message.reply_text(
            "تو قبلاً ثبت نامت رو اعلام کردی ✅\n"
            "You already announced your registration ✅\n"
            "Вы уже сообщили о регистрации ✅\n\n"
            "حالا منتظر تایید ادمین باش.\n"
            "Now wait for admin approval.\n"
            "Теперь ждите одобрения админа."
        )
        return

    await update.message.reply_text(
        f"سلام {user.first_name} 👋 / Hello {user.first_name} 👋 / Привет, {user.first_name} 👋\n\n"
        "به ربات سیگنال VIP خوش اومدی!\n"
        "Welcome to VIP Signal Bot!\n"
        "Добро пожаловать в VIP Signal Bot!\n\n"
        "برای ورود به گروه VIP باید اول از لینک زیر توی TrueTrade ثبت نام کنی:\n"
        "To enter VIP group, first register on TrueTrade with link below:\n"
        "Чтобы войти в VIP-группу, сначала зарегистрируйтесь на TrueTrade по ссылке ниже:\n\n"
        f"🔗 {REFERRAL_LINK}\n\n"
        "مراحل:\n"
        "Steps:\n"
        "Шаги:\n"
        "1️⃣ روی دکمه ثبت نام بزن و حسابت رو بساز / Click Register and create account / Нажмите «Регистрация» и создайте аккаунт\n"
        "2️⃣ برگرد اینجا و بزن «ثبت نام کردم» / Come back and press «I Registered» / Вернитесь и нажмите «Я зарегистрировался»\n"
        "3️⃣ ادمین چک می‌کنه و اگه واقعاً ثبت نام کرده باشی تاییدت می‌کنه ✅ / Admin will check and approve if you really registered ✅ / Админ проверит и одобрит, если вы действительно зарегистрировались ✅",
        reply_markup=get_welcome_keyboard()
    )

async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"لینک رفرال تو / Your Referral Link / Ваша реферальная ссылка:\n{REFERRAL_LINK}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    uid = str(user.id)

    if query.data == "registered":
        if user.id in ADMIN_IDS:
            return
        if users_db.get(uid, {}).get("status") == "approved_by_admin":
            await query.edit_message_text(
                "تو قبلاً توسط ادمین تایید شدی ✅\n"
                "You were already approved by admin ✅\n"
                "Вы уже были одобрены админом ✅\n\n"
                "حالا درخواست عضویت گروه رو بده.\n"
                "Now send join request to group.\n"
                "Теперь отправьте запрос на вступление в группу."
            )
            return
        users_db[uid] = {"registered": True, "username": user.username or "", "first_name": user.first_name or "", "status": "waiting_admin"}
        save_users(users_db)
        print(f"User {uid} waiting for admin approval", flush=True)

        await query.edit_message_text(
            "ثبت شد ✅ / Registered ✅ / Зарегистрировано ✅\n\n"
            "درخواستت رفت برای ادمین.\n"
            "Your request sent to admin.\n"
            "Ваш запрос отправлен админу.\n\n"
            "ادمین توی TrueTrade چک می‌کنه که با لینک رفرال ثبت نام کرده باشی.\n"
            "Admin will check on TrueTrade if you registered with referral link.\n"
            "Админ проверит на TrueTrade, зарегистрировались ли вы по реферальной ссылке.\n\n"
            "اگه تایید بشی، بهت خبر میدم و بعد می‌تونی درخواست عضویت گروه رو بدی.\n"
            "If approved, I will notify you and then you can send join request.\n"
            "Если одобрят, я уведомлю вас, и вы сможете отправить запрос на вступление.\n\n"
            "لطفاً صبور باش 🙏 / Please be patient 🙏 / Пожалуйста, наберитесь терпения 🙏"
        )

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🆕 درخواست ورود جدید / New Entry Request / Новый запрос на вход:\n\n"
                         f"👤 {user.first_name} (@{user.username or 'بدون یوزرنیم / No username / Без юзернейма'})\n"
                         f"🆔 ID: {user.id}\n"
                         f"🔗 ادعا می‌کنه با لینک رفرال ثبت نام کرده: {REFERRAL_CODE}\n"
                         f"🔗 Claims registered with referral link: {REFERRAL_CODE}\n"
                         f"🔗 Утверждает, что зарегистрировался по реферальной ссылке: {REFERRAL_CODE}\n\n"
                         f"برو توی پنل TrueTrade چک کن.\n"
                         f"Check in TrueTrade panel.\n"
                         f"Проверьте в панели TrueTrade.",
                    reply_markup=get_admin_keyboard(user.id)
                )
            except Exception as e:
                print("notify admin error:", e)
        return

    if query.data.startswith("approve_") or query.data.startswith("reject_"):
        if user.id not in ADMIN_IDS:
            await query.answer("فقط ادمین می‌تونه این کار رو بکنه / Only admin / Только админ", show_alert=True)
            return

        target_id = query.data.split("_")[1]
        target_data = users_db.get(target_id, {})

        if query.data.startswith("approve_"):
            users_db[target_id] = {**target_data, "status": "approved_by_admin"}
            save_users(users_db)
            await query.edit_message_text(
                f"✅ تایید شد / Approved / Одобрено:\n{target_data.get('first_name','')} (@{target_data.get('username','')}) - ID: {target_id}\n\n"
                f"حالا اگه درخواست عضویت گروه داده باشه، خودم تاییدش می‌کنم.\n"
                f"If he sent join request, I will approve it.\n"
                f"Если он отправил запрос на вступление, я одобрю его."
            )
            if target_id in pending_joins:
                try:
                    chat_id = pending_joins[target_id]
                    await context.bot.approve_chat_join_request(chat_id=chat_id, user_id=int(target_id))
                    del pending_joins[target_id]
                    await context.bot.send_message(chat_id=int(target_id), text="تبریک! ادمین تاییدت کرد و وارد گروه VIP شدی 🎉\nCongrats! Admin approved you and you joined VIP group 🎉\nПоздравляем! Админ одобрил вас, и вы вошли в VIP-группу 🎉")
                except Exception as e:
                    print("auto approve pending error:", e)
            else:
                try:
                    await context.bot.send_message(
                        chat_id=int(target_id),
                        text="تبریک! ادمین ثبت نامت رو تایید کرد ✅\nCongrats! Admin approved your registration ✅\nПоздравляем! Админ одобрил вашу регистрацию ✅\n\nحالا برو روی لینک گروه بزن و درخواست عضویت بده تا وارد بشی.\nNow click group link and send join request to enter.\nТеперь нажмите на ссылку группы и отправьте запрос на вступление."
                    )
                except:
                    pass

        elif query.data.startswith("reject_"):
            users_db[target_id] = {**target_data, "status": "rejected"}
            save_users(users_db)
            await query.edit_message_text(
                f"❌ رد شد / Rejected / Отклонено:\n{target_data.get('first_name','')} (@{target_data.get('username','')}) - ID: {target_id}"
            )
            try:
                await context.bot.send_message(
                    chat_id=int(target_id),
                    text="متاسفانه درخواستت رد شد ❌\nUnfortunately your request was rejected ❌\nК сожалению, ваш запрос был отклонён ❌\n\nادمین توی TrueTrade چک کرد و ثبت نامت با لینک رفرال پیدا نشد.\nAdmin checked on TrueTrade and your registration with referral link was not found.\nАдмин проверил на TrueTrade, и ваша регистрация по реферальной ссылке не найдена.\nلطفاً با لینک درست ثبت نام کن و دوباره تلاش کن:\nPlease register with correct link and try again:\nПожалуйста, зарегистрируйтесь по правильной ссылке и попробуйте снова:\n" + REFERRAL_LINK
                )
            except:
                pass
            if target_id in pending_joins:
                try:
                    await context.bot.decline_chat_join_request(chat_id=pending_joins[target_id], user_id=int(target_id))
                    del pending_joins[target_id]
                except:
                    pass
        return

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    user_id = str(req.from_user.id)
    print(f"Join request from {user_id} - @{req.from_user.username}", flush=True)
    user_data = users_db.get(user_id, {})

    if user_data.get("status") == "approved_by_admin":
        try:
            await req.approve()
            print(f"Approved {user_id} - already approved by admin", flush=True)
            await context.bot.send_message(chat_id=req.from_user.id, text="تبریک! وارد گروه VIP شدی 🎉\nCongrats! You joined VIP group 🎉\nПоздравляем! Вы вошли в VIP-группу 🎉")
        except Exception as e:
            print("approve error:", e)
        return

    pending_joins[user_id] = req.chat.id
    try:
        await context.bot.send_message(
            chat_id=req.from_user.id,
            text="درخواست عضویتت رسید ✅\nYour join request received ✅\nВаш запрос на вступление получен ✅\nمنتظر تایید ادمین باش.\nWait for admin approval.\nДождитесь одобрения админа."
        )
    except:
        pass

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📩 درخواست عضویت جدید توی گروه / New Join Request / Новый запрос на вступление:\n\n"
                     f"👤 {req.from_user.first_name} (@{req.from_user.username or 'بدون یوزرنیم / No username / Без юзернейма'})\n"
                     f"🆔 ID: {user_id}\n"
                     f"وضعیت ثبت نام / Registration status / Статус регистрации: {user_data.get('status','هنوز دکمه ثبت نام کردم رو نزده / Not clicked Registered yet / Еще не нажал «Я зарегистрировался»')}\n\n"
                     f"اگه توی TrueTrade تاییدش کردی، بزن تایید:\n"
                     f"If you approved him on TrueTrade, press Approve:\n"
                     f"Если вы одобрили его на TrueTrade, нажмите «Одобрить»:",
                reply_markup=get_admin_keyboard(int(user_id))
            )
        except Exception as e:
            print("notify join error:", e)

async def handle_text(update, context):
    if update.message.chat.type != "private":
        return
    if not is_admin(update):
        await update.message.reply_text(
            f"برای ورود به گروه VIP اول باید ثبت نام کنی 👇\nTo enter VIP group, first register 👇\nЧтобы войти в VIP-группу, сначала зарегистрируйтесь 👇\n{REFERRAL_LINK}\n\nبعد از ثبت نام بزن /start\nAfter registration press /start\nПосле регистрации нажмите /start"
        )
        return
    txt = update.message.text.strip()
    if len(txt) < 10 or not re.search(r'\d', txt):
        await update.message.reply_text("این سیگنال نیست ❌\nThis is not a signal ❌\nЭто не сигнал ❌")
        return
    await update.message.reply_text("دارم تحلیل میکنم... ⏳\nAnalyzing... ⏳\nАнализирую... ⏳")
    try:
        fast = parse_farsi_signal(txt)
        if fast and is_valid_signal(fast):
            final = fast + FOOTER
            await context.bot.send_message(chat_id=GROUP_ID, text=final)
            await update.message.reply_text("فرستادم توی گروه ✅\nSent to group ✅\nОтправлено в группу ✅")
            return
        raw = generate_with_fallback([PROMPT + "\n\nSignal text:\n" + txt])
        if "NOT_A_SIGNAL" in raw:
            await update.message.reply_text("این سیگنال نیست ❌\nThis is not a signal ❌\nЭто не сигнал ❌")
            return
        clean = normalize_signal(raw)
        if not is_valid_signal(clean):
            await update.message.reply_text("این سیگنال نیست ❌\nThis is not a signal ❌\nЭто не сигнал ❌")
            return
        final = clean + FOOTER
        await context.bot.send_message(chat_id=GROUP_ID, text=final)
        await update.message.reply_text("فرستادم توی گروه ✅\nSent to group ✅\nОтправлено в группу ✅")
    except Exception as e:
        await update.message.reply_text(f"خطا / Error / Ошибка: {e}")

async def handle_photo(update, context):
    if update.message.chat.type != "private":
        return
    if not is_admin(update):
        return
    await update.message.reply_text("دارم عکس رو میخونم... ⏳\nReading photo... ⏳\nЧитаю фото... ⏳")
    try:
        photos = update.message.photo
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
                await update.message.reply_text("فرستادم توی گروه ✅\nSent to group ✅\nОтправлено в группу ✅")
                return
            raw = generate_with_fallback([PROMPT + f"\n\nCaption text (use this too):\n{caption}\n\nAlso read text inside image.", img_part])
        else:
            raw = generate_with_fallback([PROMPT_CHART_ONLY, img_part])

        if "NOT_A_SIGNAL" in raw:
            await update.message.reply_text("این عکس سیگنال نیست ❌\nThis photo is not a signal ❌\nЭто фото не сигнал ❌")
            return
        clean = normalize_signal(raw)
        if not is_valid_signal(clean):
            await update.message.reply_text("نتونستم از عکس سیگنال دربیارم ❌\nCould not extract signal from photo ❌\nНе удалось извлечь сигнал из фото ❌")
            return
        final = clean + FOOTER
        await context.bot.send_message(chat_id=GROUP_ID, text=final)
        await update.message.reply_text("فرستادم توی گروه ✅\nSent to group ✅\nОтправлено в группу ✅")
    except Exception as e:
        await update.message.reply_text(f"خطا / Error / Ошибка: {e}")

def main():
    request = HTTPXRequest(connect_timeout=30, read_timeout=30, write_timeout=30, pool_timeout=30)
    telegram_app = Application.builder().token(BOT_TOKEN).request(request).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("link", link_command))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(ChatJoinRequestHandler(handle_join_request))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    print("Bot started...", flush=True)
    telegram_app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    main()
