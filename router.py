import os
import requests
import google.generativeai as genai

# ============ تنظیمات ============
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_KEY") or os.getenv("GEMINI_API_KEY")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# مدل‌های OpenRouter - بهترین‌ها برای هر کار
MODELS = {
    "chat": [
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemini-2.0-flash-001",
        "openai/gpt-4o-mini",
    ],
    "coding": [
        "deepseek/deepseek-chat-v3-0324:free",
        "qwen/qwen-2.5-coder-32b-instruct:free",
        "meta-llama/llama-3.3-70b-instruct:free",
    ],
    "trading": [
        "openai/gpt-4o-mini",
        "anthropic/claude-3.5-haiku",
        "google/gemini-2.0-flash-001",
    ],
    "long_text": [
        "google/gemini-2.0-flash-001",
        "meta-llama/llama-3.3-70b-instruct:free",
    ],
}

# ============ ۱. دسته‌بندی هوشمند ============
def classify_intent(text: str) -> str:
    """می‌فهمه کاربر چی می‌خواد"""
    t = text.lower()

    # کدنویسی
    coding_words = ["کد", "code", "python", "پایتون", "ارور", "error", "bug", "باگ", "تابع", "function", "دیباگ", "اسکریپت", "html", "css", "javascript"]
    if any(w in t for w in coding_words):
        return "coding"

    # ترید و بازار
    trading_words = ["بیت", "btc", "bitcoin", "اتریوم", "eth", "ترید", "trade", "چارت", "chart", "خرید", "فروش", "سیگنال", "بازار", "کریپتو", "فارکس", "دوج", "تتر", "تحلیل"]
    if any(w in t for w in trading_words):
        return "trading"

    # متن طولانی
    if len(text) > 800:
        return "long_text"

    # پیش‌فرض: چت معمولی
    return "chat"

# ============ ۲. صدا زدن OpenRouter ============
def call_openrouter(model: str, messages: list, max_tokens=2000) -> str | None:
    if not OPENROUTER_API_KEY:
        return None
    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://rahavard-bot.onrender.com",
            "X-Title": "RAHAVARD",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=45)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        print(f"OpenRouter fail {model}: {r.status_code} {r.text[:200]}")
        return None
    except Exception as e:
        print(f"OpenRouter error {model}: {e}")
        return None

# ============ ۳. صدا زدن Gemini (همون که الان داری) ============
def call_gemini(messages: list) -> str | None:
    if not GEMINI_API_KEY:
        return None
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.0-flash")
        # تبدیل messages به متن ساده برای Gemini
        prompt = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        res = model.generate_content(prompt)
        return res.text
    except Exception as e:
        print(f"Gemini error: {e}")
        return None

# ============ ۴. تابع اصلی هوشمند ============
def call_smart_ai(user_text: str, history: list = None, system_prompt: str = "You are RAHAVARD, a helpful AI assistant. Answer in Persian unless user asks otherwise.") -> str:
    """
    اینو به جای call_gemini صدا بزن
    خودش بهترین مدل رو انتخاب می‌کنه
    """
    intent = classify_intent(user_text)
    print(f"[ROUTER] Intent: {intent} | Text: {user_text[:50]}")

    # ساخت messages
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        # فقط ۱۰ پیام آخر رو بفرست که کند نشه
        messages += history[-10:]
    messages.append({"role": "user", "content": user_text})

    # ۱. اول مدل‌های OpenRouter رو برای اون دسته امتحان کن
    models_to_try = MODELS.get(intent, MODELS["chat"])
    for model in models_to_try:
        result = call_openrouter(model, messages)
        if result:
            print(f"[ROUTER] ✅ Used: {model}")
            return result

    # ۲. اگه همه OpenRouter ها خراب بودن، برو سراغ Gemini
    print("[ROUTER] OpenRouter failed, trying Gemini...")
    result = call_gemini(messages)
    if result:
        return result

    # ۳. اگه همه خراب بودن
    return "متاسفم، الان همه هوش‌های مصنوعی مشغولن. ۱۰ ثانیه دیگه دوباره تلاش کن 🙏"

# تست سریع
if __name__ == "__main__":
    print(call_smart_ai("سلام، بیت کوین بخرم؟"))
    print(call_smart_ai("این کد پایتون رو درست کن: print('hi'"))
