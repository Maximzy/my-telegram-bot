import sqlite3, uuid, logging, threading, os, re, json, urllib.request, urllib.parse, random, asyncio, time, collections, secrets, hmac as _hmac_mod, hashlib as _hashlib_mod
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, KeyboardButton, LabeledPrice
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, PreCheckoutQueryHandler

# --- НАЛАШТУВАННЯ ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
# Strip accidental "TELEGRAM_BOT_TOKEN " prefix if user pasted the var name too
if TOKEN.upper().startswith("TELEGRAM_BOT_TOKEN"):
    TOKEN = TOKEN[len("TELEGRAM_BOT_TOKEN"):].strip()
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set!")
_env_admin_pwd = os.environ.get("ADMIN_PASSWORD", "").strip()
if not _env_admin_pwd:
    logging.warning("⚠️  ADMIN_PASSWORD не задан в env — використовується значення за замовчуванням. Встановіть секрет ADMIN_PASSWORD!")
    _env_admin_pwd = "NezukoAdmin"
ADMIN_PASSWORD = _env_admin_pwd
PAYMENT_CARD = os.environ.get("PAYMENT_CARD", "4874070020367247")
MY_ID = int(os.environ.get("OWNER_ID", "1440236609"))
SHOP_TAG = os.environ.get("SHOP_TAG", "@NezukoUCShop")
STARS_RATE_DEFAULT = float(os.environ.get("STARS_RATE", "0.73"))
STARS_RATE = STARS_RATE_DEFAULT
PREMIUM_PACKS_BASE = [
    {"id": "prem_3m",  "label": "Telegram Premium 3 міс",  "price": 530},
    {"id": "prem_6m",  "label": "Telegram Premium 6 міс",  "price": 700},
    {"id": "prem_12m", "label": "Telegram Premium 12 міс", "price": 1250},
]

def get_setting(key: str, default: str = "") -> str:
    row = db_query_one("SELECT value FROM settings WHERE key=?", (key,))
    return row[0] if row else default

def set_setting(key: str, value: str):
    db_exec("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?,?,?)",
            (key, value, created_at_now()))

def get_stars_rate() -> float:
    v = get_setting("stars_rate")
    try:
        return float(v) if v else STARS_RATE_DEFAULT
    except Exception:
        return STARS_RATE_DEFAULT

def get_premium_packs() -> list:
    result = []
    for p in PREMIUM_PACKS_BASE:
        v = get_setting(f"{p['id']}_price")
        price = int(v) if v and v.isdigit() else p["price"]
        result.append({**p, "price": price})
    return result

logging.basicConfig(level=logging.INFO)

# Если задана DATA_DIR (например, Railway Volume /data), база хранится там
_data_dir = os.environ.get("DATA_DIR", "")
if _data_dir:
    os.makedirs(_data_dir, exist_ok=True)
    DB_PATH = os.path.join(_data_dir, "bot.db")
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
db_lock = threading.Lock()
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=FULL")
conn.commit()

def db_exec(sql, params=()):
    with db_lock:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur

def db_query(sql, params=()):
    with db_lock:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

def db_query_one(sql, params=()):
    with db_lock:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()

# --- ІНІЦІАЛІЗАЦІЯ ТАБЛИЦЬ ---
def run_migrations(connection):
    c = connection.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS orders (id TEXT, user TEXT, pack TEXT, status TEXT, chat_id INTEGER, player_id TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE IF NOT EXISTS reviews (user TEXT, text TEXT)")
    c.execute("PRAGMA table_info(orders)")
    _oc = [r[1] for r in c.fetchall()]
    for _col in ["created_at", "completed_at", "amount", "payment", "player_nick"]:
        if _col not in _oc:
            c.execute(f"ALTER TABLE orders ADD COLUMN {_col} TEXT")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_id ON orders(id)")
    c.execute("DROP INDEX IF EXISTS idx_reviews_unique")
    c.execute("DROP INDEX IF EXISTS idx_reviews_user")
    c.execute("CREATE TABLE IF NOT EXISTS referrals (referrer_id INTEGER, referred_id INTEGER PRIMARY KEY, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS ref_discounts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, bonus_type TEXT, bonus_value INTEGER, uses_left INTEGER DEFAULT -1, total_uses INTEGER DEFAULT -1, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS user_bonuses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, bonus_type TEXT, bonus_value INTEGER, used INTEGER DEFAULT 0, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS used_promo_codes (user_id INTEGER, code TEXT, PRIMARY KEY (user_id, code))")
    c.execute("PRAGMA table_info(promo_codes)")
    _pc_cols = [r[1] for r in c.fetchall()]
    for _col, _def in [("uses_left","INTEGER DEFAULT -1"),("total_uses","INTEGER DEFAULT -1"),("secret","INTEGER DEFAULT 0"),("min_uc","INTEGER DEFAULT 0"),("max_uc","INTEGER DEFAULT 0")]:
        if _col not in _pc_cols:
            c.execute(f"ALTER TABLE promo_codes ADD COLUMN {_col} {_def}")
    c.execute("PRAGMA table_info(user_bonuses)")
    _ub_cols = [r[1] for r in c.fetchall()]
    for _col, _def in [("min_uc","INTEGER DEFAULT 0"),("max_uc","INTEGER DEFAULT 0")]:
        if _col not in _ub_cols:
            c.execute(f"ALTER TABLE user_bonuses ADD COLUMN {_col} {_def}")
    c.execute("CREATE TABLE IF NOT EXISTS user_achievements (user_id INTEGER, achievement_id TEXT, granted_at TEXT, PRIMARY KEY (user_id, achievement_id))")
    c.execute("CREATE TABLE IF NOT EXISTS user_points (user_id INTEGER PRIMARY KEY, points INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS user_points_tx (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, delta INTEGER, reason TEXT, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS wheel_data (user_id INTEGER PRIMARY KEY, last_free_spin TEXT, consecutive_losses INTEGER DEFAULT 0, paid_spin_count INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS pending_wheel_spins (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, created_at TEXT, status TEXT DEFAULT 'pending', prize_id TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS user_profile (user_id INTEGER PRIMARY KEY, first_seen TEXT, last_seen TEXT, consecutive_days INTEGER DEFAULT 0, last_login_date TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS price_overrides (pack_name TEXT PRIMARY KEY, price INTEGER, updated_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS points_price_overrides (item_id TEXT PRIMARY KEY, cost INTEGER, updated_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY, reason TEXT, banned_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS cart (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, pack TEXT, added_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, chat_id INTEGER, username TEXT, category TEXT, message TEXT, status TEXT DEFAULT 'open', admin_reply TEXT, created_at TEXT, replied_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS ticket_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER, sender TEXT, message TEXT, created_at TEXT)")
    c.execute("PRAGMA table_info(tickets)")
    _tkt_cols = [r[1] for r in c.fetchall()]
    if "rating" not in _tkt_cols:
        c.execute("ALTER TABLE tickets ADD COLUMN rating INTEGER")
    c.execute("CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, message TEXT, read INTEGER DEFAULT 0, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS ai_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, message TEXT, topic TEXT, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS security_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, ip TEXT, path TEXT, event TEXT, detail TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS admin_action_log (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, action TEXT, detail TEXT, ts TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS fake_pay_log (user_id INTEGER PRIMARY KEY, count INTEGER DEFAULT 0, last_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS donations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, amount INTEGER, method TEXT, status TEXT DEFAULT 'pending', created_at TEXT)")
    connection.commit()

run_migrations(conn)

logging.info(f"База даних: {DB_PATH}")

# --- ТОВАРИ ---
PACKS = {
    "30 UC - 19 грн": 19, "60 UC - 40 грн": 40, "120 UC - 78 грн": 78,
    "180 UC - 111 грн": 111, "325 UC - 195 грн": 195, "660 UC - 389 грн": 389,
    "1800 UC - 960 грн": 960, "3800 UC - 1909 грн": 1909, "8100 UC - 3840 грн": 3840,
    "16200 UC - 7599 грн": 7599, "24300 UC - 11399 грн": 11399, "32400 UC - 15399 грн": 15399,
    "40500 UC - 18999 грн": 18999, "81000 UC - 37900 грн": 37900
}
PRIME_PACKS = {
    "👑 Prime 1 Місяць - 45 грн": 45, "👑 Prime 3 Місяця - 130 грн": 130,
    "👑 Prime 6 Місяців - 250 грн": 250, "👑 Prime 12 Місяців - 500 грн": 500
}
PRIME_PLUS_PACKS = {
    "👑 Prime Plus 1 Місяць - 410 грн": 410, "👑 Prime Plus 3 Місяці - 1200 грн": 1200,
    "👑 Prime Plus 6 Місяців - 2400 грн": 2400, "👑 Prime Plus 12 Місяців - 4730 грн": 4730
}
RISE_PACKS = {
    "⭐️ Набір Підйом 1 (170 UC + 900 AG) - 39 грн": 39,
    "⭐️ Набір Підйом 2 (180 UC + 9 міні матеріалів) - 119 грн": 119,
    "⭐️ Набір Підйом 3 (300 UC + 79 міні емблем) - 199 грн": 199,
}
TG_GIFTS = {
    "🐣 Пасхальний": 50,
    "🎉 1 Квітня": 50,
    "🍀 Патрика": 50,
    "🌸 8 Березня": 50,
    "❤️ Валентина": 50,
    "💝 Серце Валентина": 50,
    "🧸 Новорічний": 50,
    "🎄 Ялинка Новорічна": 50,
}
ALL_PACKS = {**PACKS, **PRIME_PACKS, **PRIME_PLUS_PACKS, **RISE_PACKS, **TG_GIFTS}
SMALL_UC = set(list(PACKS.keys())[:6])
MEDIUM_UC = set(list(PACKS.keys())[6:9])

BONUS_TYPES = {
    "free_uc_30":       "🎁 30 UC безкоштовно на акаунт",
    "free_uc_60":       "🎁 60 UC безкоштовно на акаунт",
    "discount_small_5": "Знижка 5% на малі UC паки (30–660 UC)",
    "discount_small_4": "Знижка 4% на малі UC паки (30–660 UC)",
    "discount_small_3": "Знижка 3% на малі UC паки (30–660 UC)",
    "discount_small_2": "Знижка 2% на малі UC паки (30–660 UC)",
    "discount_small_1": "Знижка 1% на малі UC паки (30–660 UC)",
    "discount_medium_2":"Знижка 2% на середні UC паки (1800–8100 UC)",
    "discount_medium_1":"Знижка 1% на середні UC паки (1800–8100 UC)",
    "points_50":        "🪙 50 балів",
    "points_100":       "🪙 100 балів",
    "points_200":       "💰 200 балів",
    "points_500":       "💰 500 балів",
    "extra_spin":       "🎰 Повторний прокрут рулетки",
}

# --- ДОСЯГНЕННЯ ---
ACHIEVEMENTS = {
    "novice":       {"emoji":"🟢","name":"Новачок",             "desc":"Ти зробив перший крок у світ UC 😄",                  "hint":"Зробити першу покупку.",                      "manual":0},
    "regular":      {"emoji":"🔥","name":"Постійник",            "desc":"Ти вже своя людина в шопі.",                          "hint":"Зробити 5 покупок.",                          "manual":0},
    "vip":          {"emoji":"👑","name":"VIP Клієнт",           "desc":"Ти занадто часто тут з'являєшся 😄",                 "hint":"Зробити 15 покупок.",                         "manual":0},
    "whale":        {"emoji":"🐋","name":"Кит",                  "desc":"Nezuko тебе любить.",                                 "hint":"Витратити 1000 грн в боті.",                  "manual":0},
    "night_owl":    {"emoji":"🌙","name":"Нічний житель",        "desc":"Справжні донатери не сплять вночі.",                 "hint":"Зробити замовлення після 00:00 до 06:00.",    "manual":0},
    "early_bird":   {"emoji":"☀️","name":"Ранній гравець",       "desc":"Поки всі сплять — ти вже фармиш UC.",                "hint":"Зробити покупку о 07:00–08:00.",              "manual":0},
    "lucky":        {"emoji":"🎰","name":"Улюбленець удачі",     "desc":"Колесо фортуни сьогодні було на твоєму боці.",       "hint":"Вибити рідкісний приз із колеса.",            "manual":0},
    "unlucky":      {"emoji":"💀","name":"Невдаха",              "desc":"Іноді удача йде у відпустку…",                       "hint":"3 рази підряд програти в колесі.",            "manual":0},
    "bonus_hunter": {"emoji":"🎁","name":"Мисливець за бонусами","desc":"Ти не пропускаєш жодного бонусу.",                  "hint":"Активувати 5 промокодів.",                    "manual":0},
    "secret_seeker":{"emoji":"🕵️","name":"Шукач секретів",      "desc":"Ти вмієш знаходити те, чого інші не бачать.",        "hint":"Знайти прихований промокод.",                 "manual":0},
    "recruiter":    {"emoji":"📢","name":"Рекламщик",            "desc":"Ти приводиш нових людей у шоп.",                     "hint":"Запросити 10 друзів.",                        "manual":0},
    "magnet":       {"emoji":"🧲","name":"Магніт для людей",     "desc":"Люди приходять за твоїм посиланням знову і знову.",  "hint":"Запросити 20 друзів.",                        "manual":0},
    "loyal":        {"emoji":"🧡","name":"Вірний клієнт",        "desc":"Ти залишаєшся з шопом довгий час.",                  "hint":"Бути зареєстрованим більше місяця.",          "manual":0},
    "daily7":       {"emoji":"📆","name":"Щоденний",             "desc":"Ти майже живеш у боті 😄",                           "hint":"Заходити 7 днів підряд.",                     "manual":0},
    "flash":        {"emoji":"⚡","name":"Швидкий як флешка",    "desc":"Ти оформлюєш замовлення швидше за всіх.",            "hint":"Зробити покупку одразу після входу в міні апп.","manual":0},
    "legend":       {"emoji":"🏆","name":"Легенда шопу",         "desc":"Тебе вже знають усі.",                               "hint":"Отримати Топ 1 в таблиці лідерів.",           "manual":0},
    "pubg_fan":     {"emoji":"🎮","name":"PUBG Fan",             "desc":"UC Shop від Nezuko вже частина твого життя.",         "hint":"Зробити 3 замовлення UC.",                    "manual":0},
    "uc_addict":    {"emoji":"🔥","name":"UC Залежний",          "desc":"Схоже, без UC ти вже не можеш 😄",                   "hint":"Купити UC 5 разів за один день.",             "manual":0},
    "precise":      {"emoji":"🎯","name":"Точний постріл",       "desc":"Ти активуєш промокоди швидше за інших.",             "hint":"Встигнути використати лімітований промокод.", "manual":0},
    "saver":        {"emoji":"🧠","name":"Хитрий",               "desc":"Ти вмієш економити.",                                "hint":"Використати 10 знижок.",                      "manual":0},
    "gambler":      {"emoji":"🎲","name":"Азартний",             "desc":"Ти занадто любиш колесо фортуни.",                   "hint":"Прокрутити платне колесо 20 разів.",          "manual":0},
    "jackpot":      {"emoji":"💎","name":"Джекпот",              "desc":"Найрідкісніша удача.",                               "hint":"Вибити найрідкісніший приз в колесі.",        "manual":0},
    "rocket":       {"emoji":"🚀","name":"Ракета",               "desc":"Ти дуже швидко ростеш.",                             "hint":"Зробити 5 покупок за 1 годину.",              "manual":0},
    "trusted":      {"emoji":"🛡️","name":"Довірений",           "desc":"Адміністрація тобі довіряє.",                        "hint":"Призначається адміном.",                      "manual":1},
    "risky":        {"emoji":"😈","name":"Ризиковий",            "desc":"Ти любиш випробовувати удачу.",                      "hint":"Купити платне колесо 10 разів.",              "manual":0},
    "old":          {"emoji":"❤️","name":"Олд",                 "desc":"Ти з шопом ще з давніх часів.",                      "hint":"Один із найперших користувачів.",             "manual":1},
    "secret_ach":   {"emoji":"🔐","name":"Секретне досягнення",  "desc":"????",                                               "hint":"Приховано 😄",                                "manual":0},
    "collector":    {"emoji":"📦","name":"Колекціонер",          "desc":"Ти зібрав безліч досягнень.",                        "hint":"Отримати 20 досягнень.",                      "manual":0},
    "tester":       {"emoji":"🧪","name":"Тестер",               "desc":"Ти бачив функції раніше за інших.",                  "hint":"Призначається адміном.",                      "manual":1},
    "danger":       {"emoji":"☢️","name":"Небезпечний донатер", "desc":"Твій баланс лякає оточуючих 😄",                    "hint":"Витратити 10000 грн у боті.",                 "manual":0},
    "partner":      {"emoji":"🌐","name":"Партнер",              "desc":"Ти дуже допоміг власнику магазина.",                 "hint":"Призначається адміном.",                      "manual":1},
}

POINTS_SHOP = [
    {"id":"uc30",       "name":"🎁 30 UC безкоштовно",      "cost":1500, "bonus_type":"free_uc_30"},
    {"id":"uc60",       "name":"🎁 60 UC безкоштовно",      "cost":3000, "bonus_type":"free_uc_60"},
    {"id":"disc_s1",    "name":"Знижка 1% (малі паки)",     "cost":500,  "bonus_type":"discount_small_1"},
    {"id":"disc_s2",    "name":"Знижка 2% (малі паки)",     "cost":1000, "bonus_type":"discount_small_2"},
    {"id":"disc_m1",    "name":"Знижка 1% (середні паки)",  "cost":750,  "bonus_type":"discount_medium_1"},
    {"id":"disc_m2",    "name":"Знижка 2% (середні паки)",  "cost":1500, "bonus_type":"discount_medium_2"},
    {"id":"extra_spin", "name":"Повторний прокрут рулетки", "cost":300,  "bonus_type":"extra_spin"},
]

# --- ПАКЕТИ ЗІРОК (Telegram Stars → бали) ---
STARS_PACKAGES = [
    {"id": "stars_50",  "stars": 50,  "points": 500,  "label": "50 ⭐ → 500 балів"},
    {"id": "stars_100", "stars": 100, "points": 1100, "label": "100 ⭐ → 1100 балів (+10%)"},
    {"id": "stars_250", "stars": 250, "points": 3000, "label": "250 ⭐ → 3000 балів (+20%)"},
    {"id": "stars_500", "stars": 500, "points": 6500, "label": "500 ⭐ → 6500 балів (+30%)"},
]

FREE_WHEEL_PRIZES = [
    {"id":"nothing", "name":"Нічого",      "weight":80, "type":"nothing",              "value":0,   "rarity":"common"},
    {"id":"disc12",  "name":"Знижка 1-2%", "weight":10, "type":"random_discount_small","value":0,   "rarity":"rare"},
    {"id":"pts50",   "name":"50 балів",    "weight":5,  "type":"points",               "value":50,  "rarity":"rare"},
    {"id":"pts100",  "name":"100 балів",   "weight":4,  "type":"points",               "value":100, "rarity":"epic"},
    {"id":"pts200",  "name":"200 балів",   "weight":1,  "type":"points",               "value":200, "rarity":"legendary"},
]

PAID_WHEEL_PRIZES = [
    {"id":"nothing", "name":"Нічого",   "weight":25, "type":"nothing",    "value":0,   "rarity":"common"},
    {"id":"uc30",    "name":"30 UC",    "weight":25, "type":"free_uc_30", "value":30,  "rarity":"rare"},
    {"id":"uc60",    "name":"60 UC",    "weight":25, "type":"free_uc_60", "value":60,  "rarity":"epic"},
    {"id":"pts500",  "name":"500 балів","weight":25, "type":"points",     "value":500, "rarity":"legendary"},
]

# --- КЛАВІАТУРИ ---
MAIN_KB = [
    ["🛍 Магазин"],
    ["🏆 Топ донатерів", "🏅 Досягнення"],
    ["🎁 Промокод", "👥 Реферал"],
    ["⭐ Бали за зірки"],
    ["📋 Мої замовлення", "📄 Політика"],
    ["💖 Підтримати бота", "🆘 Підтримка"],
    ["⚙️ Адмін"]
]

DONATE_AMOUNTS = [20, 50, 100, 200, 500]

def get_main_kb(uid):
    kb = list(MAIN_KB)
    extras = []
    if db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type='free_uc_60' AND used=0 LIMIT 1", (uid,)):
        extras.append("🎁 60 UC Free")
    if db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type='free_uc_30' AND used=0 LIMIT 1", (uid,)):
        extras.append("🎁 30 UC Free")
    if extras:
        kb = [extras] + kb
    # Додаємо кнопку Mini App якщо домен відомий
    _domain = (
        os.getenv("BOT_DOMAIN") or
        os.getenv("REPLIT_DEV_DOMAIN") or
        (os.getenv("REPLIT_DOMAINS", "").split(",")[0].strip() or None) or
        os.getenv("RAILWAY_PUBLIC_DOMAIN") or
        os.getenv("RAILWAY_STATIC_URL", "").replace("https://","").replace("http://","").strip("/") or
        ""
    )
    if _domain:
        kb = [["🌸 Відкрити Mini App"]] + kb
    return kb

SHOP_KB = ReplyKeyboardMarkup(
    [["💸 Купити UC"], ["👑 Prime", "👑 Prime Plus"], ["⭐️ Набори Підйом"], ["🎁 Старі подарки Telegram"], ["🔙 Назад"]],
    resize_keyboard=True
)
ADMIN_KB = [
    ["📦 Замовлення"],
    ["🌟 Відгуки"],
    ["📊 Статистика"],
    ["🎁 Промокоди"],
    ["🚪 Вийти"]
]

user_states = {}
admin_last_seen = 0.0

# ── RATE LIMITING & BRUTE-FORCE PROTECTION ────────────────────────────────────
_rl_lock = threading.Lock()
_rl_buckets: dict = collections.defaultdict(list)          # key -> [timestamps]
_rl_admin_fails: dict = collections.defaultdict(list)      # ip  -> [timestamps]
_rl_admin_lockout: dict = {}                               # ip  -> lockout_until
MAX_POST_BYTES = 512 * 1024  # 512 KB hard limit per request

# Brute-force protection for Telegram admin password
_tg_admin_fails: dict = collections.defaultdict(list)     # uid -> [timestamps]
_tg_admin_lockout: dict = {}                              # uid -> lockout_until
TG_ADMIN_MAX_FAILS = 3       # max wrong attempts
TG_ADMIN_LOCKOUT_SEC = 1800  # 30 min lockout after max fails

# 2FA OTP storage: uid -> {code, expires_at}
_admin_otp: dict = {}
ADMIN_OTP_TTL = 300  # 5 minutes

# Admin session activity tracker: uid -> last_activity timestamp
_admin_last_activity: dict = {}
ADMIN_SESSION_TTL = 1800  # 30 min inactivity = auto logout

# Fake payment abuse tracker: uid -> [timestamps]
_fake_pay_attempts: dict = collections.defaultdict(list)
FAKE_PAY_MAX = 3
FAKE_PAY_WINDOW = 3600  # within 1 hour

def _generate_otp() -> str:
    return str(secrets.randbelow(900000) + 100000)

def _admin_touch(uid: int):
    """Update admin session activity timestamp."""
    _admin_last_activity[uid] = time.time()

def _admin_session_valid(uid: int) -> bool:
    """Return True if admin session is still active (not expired)."""
    last = _admin_last_activity.get(uid, 0)
    return (time.time() - last) < ADMIN_SESSION_TTL

def _admin_logout(uid: int):
    """Expire admin session."""
    _admin_last_activity.pop(uid, None)
    _admin_otp.pop(uid, None)

def log_admin_action(admin_id: int, action: str, detail: str = ""):
    """Log admin action to DB."""
    try:
        db_exec("INSERT INTO admin_action_log (admin_id, action, detail, ts) VALUES (?,?,?,?)",
                (admin_id, action[:128], detail[:512], created_at_now()))
    except Exception:
        pass

def _check_fake_pay(uid: int) -> bool:
    """Track fake payment attempts. Returns True if user should be autobanned."""
    now = time.time()
    attempts = _fake_pay_attempts[uid]
    attempts[:] = [t for t in attempts if now - t < FAKE_PAY_WINDOW]
    attempts.append(now)
    return len(attempts) >= FAKE_PAY_MAX

def _check_suspicious_player_id(player_id: str, current_uid: int) -> bool:
    """Return True if player_id was used by 3+ different Telegram accounts."""
    rows = db_query("SELECT DISTINCT chat_id FROM orders WHERE player_id=? AND chat_id != ?", (player_id, current_uid))
    return len(rows) >= 2  # 2 others + current = 3+ total

def _tg_admin_check(uid: int, password: str) -> tuple:
    """Telegram admin password check with brute-force lockout.
    Returns (ok: bool, error: str)."""
    now = time.time()
    with _rl_lock:
        if uid in _tg_admin_lockout and now < _tg_admin_lockout[uid]:
            wait_min = int((_tg_admin_lockout[uid] - now) / 60) + 1
            return False, f"🚫 Забагато спроб. Заблоковано на {wait_min} хв."
        fails = _tg_admin_fails[uid]
        fails[:] = [t for t in fails if now - t < 3600]
    ok = _hmac_mod.compare_digest(str(password), ADMIN_PASSWORD)
    with _rl_lock:
        if ok:
            _tg_admin_fails[uid] = []
            if uid in _tg_admin_lockout:
                del _tg_admin_lockout[uid]
        else:
            _tg_admin_fails[uid].append(time.time())
            count = len(_tg_admin_fails[uid])
            if count >= TG_ADMIN_MAX_FAILS:
                _tg_admin_lockout[uid] = time.time() + TG_ADMIN_LOCKOUT_SEC
                _tg_admin_fails[uid] = []
                logging.warning(f"[SECURITY] Telegram admin brute-force lockout: uid={uid}")
                return False, f"🚫 Перевищено ліміт спроб. Заблоковано на 30 хвилин."
            remaining = TG_ADMIN_MAX_FAILS - count
            logging.warning(f"[SECURITY] Wrong Telegram admin password: uid={uid}, remaining={remaining}")
            return False, f"❌ Невірний пароль. Залишилось спроб: {remaining}"
    return True, ""

def _rl_allow(key: str, max_calls: int, window_sec: int) -> bool:
    """Sliding-window rate limiter. Returns True if request is allowed."""
    now = time.time()
    with _rl_lock:
        bucket = _rl_buckets[key]
        bucket[:] = [t for t in bucket if now - t < window_sec]
        if len(bucket) >= max_calls:
            return False
        bucket.append(now)
        return True

def _rl_admin_check(ip: str, password: str) -> tuple:
    """Admin password check with brute-force lockout.
    Returns (ok: bool, error: str)."""
    now = time.time()
    with _rl_lock:
        if ip in _rl_admin_lockout and now < _rl_admin_lockout[ip]:
            wait = int(_rl_admin_lockout[ip] - now)
            return False, f"IP заблоковано. Зачекайте {wait} сек."
        fails = _rl_admin_fails[ip]
        fails[:] = [t for t in fails if now - t < 300]
        if len(fails) >= 5:
            _rl_admin_lockout[ip] = now + 900  # 15-хвилинне блокування
            _rl_admin_fails[ip] = []
            logging.warning(f"[SECURITY] Admin brute-force lockout: {ip}")
            return False, "Забагато невдалих спроб. IP заблоковано на 15 хвилин."

    ok = _hmac_mod.compare_digest(str(password), ADMIN_PASSWORD)
    with _rl_lock:
        if ok:
            _rl_admin_fails[ip] = []
        else:
            _rl_admin_fails[ip].append(time.time())
            remaining = max(0, 5 - len(_rl_admin_fails[ip]))
            logging.warning(f"[SECURITY] Failed admin login from {ip}, remaining attempts: {remaining}")
            return False, f"Невірний пароль. Залишилось спроб: {remaining}"
    return True, ""

def _get_client_ip(handler) -> str:
    """Extract real client IP, considering proxy headers."""
    xff = handler.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return handler.client_address[0] if handler.client_address else "unknown"

_ip_blacklist: dict = {}   # ip -> blacklisted_until timestamp
_ip_violation_count: dict = collections.defaultdict(int)

def _sec_log(ip: str, path: str, event: str, detail: str = ""):
    """Log a security event to DB and stderr."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db_exec("INSERT INTO security_log (ts, ip, path, event, detail) VALUES (?,?,?,?,?)",
                (ts, ip[:64], path[:128], event[:64], detail[:256]))
    except Exception:
        pass
    logging.warning(f"[SECURITY] {event} | ip={ip} path={path} | {detail}")

def _ip_is_blocked(ip: str) -> bool:
    """Return True if IP is currently blacklisted."""
    until = _ip_blacklist.get(ip, 0)
    return time.time() < until

def _ip_violation(ip: str, path: str, reason: str):
    """Record a violation; auto-blacklist after 20 violations in 10 min."""
    with _rl_lock:
        _ip_violation_count[ip] += 1
        count = _ip_violation_count[ip]
    _sec_log(ip, path, "VIOLATION", f"#{count} — {reason}")
    if count >= 20:
        with _rl_lock:
            _ip_blacklist[ip] = time.time() + 3600  # 1-hour block
            _ip_violation_count[ip] = 0
        _sec_log(ip, path, "IP_BLACKLISTED", f"Auto-blacklisted after {count} violations")

_CTRL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

def _sanitize(text: str, max_len: int = 500) -> str:
    """Strip control characters and trim to max length."""
    return _CTRL_CHARS_RE.sub("", str(text))[:max_len]

def _valid_player_id(pid: str) -> bool:
    """PUBG Mobile player IDs are 5–16 digit numbers."""
    return bool(re.fullmatch(r'\d{5,16}', pid.strip()))

def _rl_cleanup_worker():
    """Periodically purge stale entries from rate-limit buckets to prevent memory leaks."""
    while True:
        time.sleep(600)
        cutoff = time.time() - 3600
        with _rl_lock:
            stale_keys = [k for k, v in _rl_buckets.items() if not v or v[-1] < cutoff]
            for k in stale_keys:
                del _rl_buckets[k]
            stale_ips = [ip for ip, until in _ip_blacklist.items() if time.time() > until]
            for ip in stale_ips:
                del _ip_blacklist[ip]
            stale_fails = [ip for ip, v in _rl_admin_fails.items() if not v]
            for ip in stale_fails:
                del _rl_admin_fails[ip]
            stale_violations = [ip for ip, c in _ip_violation_count.items() if c == 0]
            for ip in stale_violations:
                del _ip_violation_count[ip]

threading.Thread(target=_rl_cleanup_worker, daemon=True).start()

def is_admin_online():
    return (time.time() - admin_last_seen) < 120

def _ai_ticket_auto_reply(ticket_id, category, user_chat_id, delay=50):
    """Wait `delay` seconds, then reply with AI if admin is still offline and ticket unanswered."""
    def _run():
        time.sleep(delay)
        if is_admin_online():
            return
        if not GEMINI_API_KEY:
            return
        try:
            ticket = db_query_one("SELECT status FROM tickets WHERE id=?", (ticket_id,))
            if not ticket or ticket[0] in ("closed", "answered"):
                return
            msgs = db_query("SELECT sender, message FROM ticket_messages WHERE ticket_id=? ORDER BY id", (ticket_id,))
            history_text = "\n".join([f"{'Користувач' if m[0]=='user' else 'Підтримка'}: {m[1]}" for m in msgs])
            prompt = (
                f"Ти — AI-помічник підтримки магазину UC Shop (PUBG Mobile). "
                f"Відповідай ЛИШЕ українською мовою. Будь ввічливим і корисним. "
                f"Категорія тікету: {category}.\n"
                f"Листування:\n{history_text}\n\n"
                f"Дай коротку корисну відповідь від імені підтримки. "
                f"Якщо питання складне — скажи що менеджер відповість найближчим часом."
            )
            messages = [{"role": "system", "content": AI_SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
            response = _gemini_api_call(messages, max_tokens=400)
            if not response or response.startswith("❌"):
                return
            ai_note = response + "\n\n_(🤖 Автовідповідь AI — менеджер підтвердить найближчим часом)_"
            db_exec("INSERT INTO ticket_messages (ticket_id, sender, message, created_at) VALUES (?,?,?,?)",
                    (ticket_id, "ai", ai_note, created_at_now()))
            db_exec("UPDATE tickets SET status='answered', admin_reply=?, replied_at=? WHERE id=?",
                    (ai_note, created_at_now(), ticket_id))
            push_notification(user_chat_id, "ticket_reply", f"🤖 AI відповів на тікет #{ticket_id}: {response[:80]}")
            try:
                msg_txt = f"🤖 AI-відповідь по тікету #{ticket_id} [{category}]:\n\n{response}\n\n_(Менеджер підтвердить найближчим часом)_"
                p = urllib.parse.urlencode({"chat_id": user_chat_id, "text": msg_txt}).encode()
                urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=p), timeout=5)
            except Exception as e:
                logging.warning(f"ai ticket notify error: {e}")
        except Exception as e:
            logging.warning(f"_ai_ticket_auto_reply error: {e}")
    threading.Thread(target=_run, daemon=True).start()

POLICY_HTML = ""

MINIAPP_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "miniapp.html")

def _load_miniapp_html():
    try:
        with open(MINIAPP_HTML_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "<h1>Mini App не знайдено</h1>"

_ALLOWED_ORIGINS = {
    "https://web.telegram.org",
    "https://t.me",
}

def _cors_origin(handler) -> str:
    origin = handler.headers.get("Origin", "")
    if not origin or origin == "null":
        return "null"
    own = _get_domain()
    if own and origin in (f"https://{own}", f"http://{own}"):
        return origin
    if origin in _ALLOWED_ORIGINS:
        return origin
    return "null"

def _json_response(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", _cors_origin(handler))
    handler.send_header("Vary", "Origin")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)

def _html_response(handler, html):
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "SAMEORIGIN")
    handler.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
    handler.send_header("Content-Security-Policy",
        "default-src 'self' https://telegram.org https://*.telegram.org; "
        "script-src 'self' 'unsafe-inline' https://telegram.org https://*.telegram.org; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' https:")
    handler.end_headers()
    handler.wfile.write(body)

def _send_tg_message(chat_id, text):
    try:
        params = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=params)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logging.warning(f"_send_tg_message failed: {e}")

def push_notification(user_id, ntype, message):
    try:
        db_exec("INSERT INTO notifications (user_id, type, message, created_at) VALUES (?,?,?,?)",
                (user_id, ntype, message, created_at_now()))
    except Exception as e:
        logging.warning(f"push_notification error: {e}")

def _notify_admin_order(order_id, pack, player_id, amount, user_id, username, mix_packs_list=None, player_nick=None):
    try:
        user_label_str = f"@{username}" if username else str(user_id)
        rise_marker = "⭐️ НАБІР ПІДЙОМ\n" if "Набір Підйом" in pack else ""
        if mix_packs_list:
            from collections import Counter
            counts = Counter(mix_packs_list)
            pack_lines = "\n".join([
                f"  • {p} × {cnt} = {get_pack_price(p) * cnt} грн"
                if cnt > 1 else f"  • {p} — {get_pack_price(p)} грн"
                for p, cnt in counts.items()
            ])
            pack_info = f"🎮 МІК UC ({len(mix_packs_list)} пак{'и' if len(mix_packs_list) > 1 else ''}):\n{pack_lines}"
        else:
            pack_info = f"🎁 {pack}"
        nick_line = f"\n🪪 Нік: {player_nick}" if player_nick else ""
        text = (f"💰 ОПЛАТА (Mini App)!\n{rise_marker}🆔 {order_id}\n👤 {user_label_str}\n{pack_info}\n🎮 ID: {player_id}{nick_line}\n💵 Сума: {amount} грн")
        ok_btn = json.dumps({"inline_keyboard": [[
            {"text": "✅ Готово", "callback_data": f"ok_{order_id}"},
            {"text": "❌ Відхилити", "callback_data": f"no_{order_id}"}
        ]]})
        params = urllib.parse.urlencode({"chat_id": MY_ID, "text": text, "reply_markup": ok_btn}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=params)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logging.warning(f"Не вдалося повідомити адміна: {e}")

def _notify_admin_ticket(ticket_id, user_id, username, category, message):
    try:
        user_label_str = f"@{username}" if username else str(user_id)
        text = (f"🎫 ТІКЕТ #{ticket_id}\n📂 Категорія: {category}\n"
                f"👤 {user_label_str} (ID: {user_id})\n\n💬 Повідомлення:\n{message}")
        reply_btn = json.dumps({"inline_keyboard": [[
            {"text": "✏️ Відповісти", "callback_data": f"tkt_reply_{ticket_id}"}
        ]]})
        params = urllib.parse.urlencode({"chat_id": MY_ID, "text": text, "reply_markup": reply_btn}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=params)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logging.warning(f"ticket notify error: {e}")

# --- ДОСЯГНЕННЯ + БАЛИ: ХЕЛПЕРИ ---
def add_points(user_id, delta, reason=""):
    db_exec("INSERT OR IGNORE INTO user_points (user_id, points) VALUES (?,0)", (user_id,))
    db_exec("UPDATE user_points SET points=points+? WHERE user_id=?", (delta, user_id))
    db_exec("INSERT INTO user_points_tx (user_id, delta, reason, created_at) VALUES (?,?,?,?)",
            (user_id, delta, reason, created_at_now()))

def get_points(user_id):
    r = db_query_one("SELECT points FROM user_points WHERE user_id=?", (user_id,))
    return r[0] if r else 0

def grant_achievement(user_id, ach_id):
    if ach_id not in ACHIEVEMENTS:
        return False
    existing = db_query_one("SELECT 1 FROM user_achievements WHERE user_id=? AND achievement_id=?", (user_id, ach_id))
    if existing:
        return False
    db_exec("INSERT OR IGNORE INTO user_achievements (user_id, achievement_id, granted_at) VALUES (?,?,?)",
            (user_id, ach_id, created_at_now()))
    ach = ACHIEVEMENTS[ach_id]
    total = db_query_one("SELECT COUNT(*) FROM user_achievements WHERE achievement_id=?", (ach_id,))[0]
    total_users = db_query_one("SELECT COUNT(DISTINCT user_id) FROM user_profile")[0] or 1
    pct = round(total / total_users * 100, 1)
    msg = (f"🏅 Нове досягнення!\n{ach['emoji']} {ach['name']}\n{ach['desc']}\n\n"
           f"👥 Отримали: {total} гравців ({pct}% від усіх)")
    _send_tg_message(user_id, msg)
    count = db_query_one("SELECT COUNT(*) FROM user_achievements WHERE user_id=?", (user_id,))[0]
    if count >= 20:
        grant_achievement(user_id, "collector")
    return True

def update_user_profile(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    profile = db_query_one("SELECT first_seen, last_login_date, consecutive_days FROM user_profile WHERE user_id=?", (user_id,))
    if not profile:
        db_exec("INSERT OR IGNORE INTO user_profile (user_id, first_seen, last_seen, consecutive_days, last_login_date) VALUES (?,?,?,1,?)",
                (user_id, created_at_now(), created_at_now(), today))
    else:
        last_date = profile[1] or ""
        cons = profile[2] or 0
        if last_date != today:
            try:
                last_dt = datetime.strptime(last_date, "%Y-%m-%d")
                diff = (datetime.now().date() - last_dt.date()).days
                cons = cons + 1 if diff == 1 else 1
            except:
                cons = 1
            db_exec("UPDATE user_profile SET last_seen=?, consecutive_days=?, last_login_date=? WHERE user_id=?",
                    (created_at_now(), cons, today, user_id))
        else:
            db_exec("UPDATE user_profile SET last_seen=? WHERE user_id=?", (created_at_now(), user_id))

def check_achievements(user_id):
    update_user_profile(user_id)
    profile = db_query_one("SELECT first_seen, consecutive_days FROM user_profile WHERE user_id=?", (user_id,))
    if not profile:
        return
    cons = profile[1] or 0
    if cons >= 7:
        grant_achievement(user_id, "daily7")
    try:
        first = datetime.strptime(profile[0][:10], "%Y-%m-%d")
        if (datetime.now() - first).days >= 30:
            grant_achievement(user_id, "loyal")
    except: pass

    done_orders = db_query("SELECT pack, created_at, amount FROM orders WHERE chat_id=? AND status='done'", (user_id,))
    done_count = len(done_orders)
    if done_count >= 1: grant_achievement(user_id, "novice")
    if done_count >= 5: grant_achievement(user_id, "regular")
    if done_count >= 15: grant_achievement(user_id, "vip")

    total_spent = sum(get_pack_price(r[0]) for r in done_orders)
    if total_spent >= 1000: grant_achievement(user_id, "whale")
    if total_spent >= 10000: grant_achievement(user_id, "danger")

    for pack, cat_val, _ in done_orders:
        try:
            hour = int(cat_val[11:13])
            if 0 <= hour < 6: grant_achievement(user_id, "night_owl")
            if 7 <= hour < 9: grant_achievement(user_id, "early_bird")
        except: pass

    uc_orders = [r for r in done_orders if "UC" in r[0] and "Prime" not in r[0]]
    if len(uc_orders) >= 3: grant_achievement(user_id, "pubg_fan")

    from collections import Counter
    day_counts = Counter()
    for _, c_at, _ in done_orders:
        day_counts[(c_at or "")[:10]] += 1
    if any(v >= 5 for v in day_counts.values()):
        grant_achievement(user_id, "uc_addict")

    # Rocket: 5 purchases in 1 hour
    if done_count >= 5:
        times = sorted([r[1] for r in done_orders if r[1]])
        for i in range(len(times) - 4):
            try:
                t0 = datetime.strptime(times[i], "%Y-%m-%d %H:%M:%S")
                t4 = datetime.strptime(times[i+4], "%Y-%m-%d %H:%M:%S")
                if (t4 - t0).total_seconds() <= 3600:
                    grant_achievement(user_id, "rocket"); break
            except: pass

    refs = db_query_one("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user_id,))
    ref_count = refs[0] if refs else 0
    if ref_count >= 10: grant_achievement(user_id, "recruiter")
    if ref_count >= 20: grant_achievement(user_id, "magnet")

    promo_count = db_query_one("SELECT COUNT(*) FROM used_promo_codes WHERE user_id=?", (user_id,))
    if promo_count and promo_count[0] >= 5: grant_achievement(user_id, "bonus_hunter")

    disc_count = db_query_one("SELECT COUNT(*) FROM user_bonuses WHERE user_id=? AND used=1 AND bonus_type LIKE 'discount%'", (user_id,))
    if disc_count and disc_count[0] >= 10: grant_achievement(user_id, "saver")

    wheel = db_query_one("SELECT consecutive_losses, paid_spin_count FROM wheel_data WHERE user_id=?", (user_id,))
    if wheel:
        if wheel[0] >= 3: grant_achievement(user_id, "unlucky")
        if wheel[1] >= 20: grant_achievement(user_id, "gambler")
        if wheel[1] >= 10: grant_achievement(user_id, "risky")

    top = db_query("SELECT chat_id FROM (SELECT chat_id, SUM(COALESCE(CAST(amount AS INTEGER),0)) as total FROM orders WHERE status='done' GROUP BY chat_id ORDER BY total DESC LIMIT 1)")
    if top and top[0][0] == user_id:
        grant_achievement(user_id, "legend")

def spin_wheel_random(prizes):
    total = sum(p["weight"] for p in prizes)
    r = random.randint(1, total)
    cumulative = 0
    for prize in prizes:
        cumulative += prize["weight"]
        if r <= cumulative:
            return prize
    return prizes[-1]

def deliver_wheel_prize(user_id, username, prize):
    ptype = prize["type"]
    if ptype == "points":
        add_points(user_id, prize["value"], "Колесо фортуни")
        grant_achievement(user_id, "lucky")
        _send_tg_message(user_id, f"🎰 Колесо фортуни: {prize['name']}!\n+{prize['value']} балів нараховано!")
    elif ptype == "free_uc_30":
        db_exec("INSERT INTO user_bonuses (user_id, bonus_type, bonus_value, used, created_at) VALUES (?,?,?,0,?)",
                (user_id, "free_uc_30", 30, created_at_now()))
        grant_achievement(user_id, "lucky")
        if prize.get("rarity") == "legendary":
            grant_achievement(user_id, "jackpot")
        _send_tg_message(user_id, f"🎰 Колесо фортуни: 30 UC! Кнопка з'явиться в меню 🎁")
    elif ptype == "free_uc_60":
        db_exec("INSERT INTO user_bonuses (user_id, bonus_type, bonus_value, used, created_at) VALUES (?,?,?,0,?)",
                (user_id, "free_uc_60", 60, created_at_now()))
        grant_achievement(user_id, "lucky")
        if prize.get("rarity") == "legendary":
            grant_achievement(user_id, "jackpot")
        _send_tg_message(user_id, f"🎰 Колесо фортуни: 60 UC! Кнопка з'явиться в меню 🎁")
    elif ptype == "random_discount_small":
        pct = random.choice([1, 2])
        bt = f"discount_small_{pct}"
        db_exec("INSERT INTO user_bonuses (user_id, bonus_type, bonus_value, used, created_at) VALUES (?,?,?,0,?)",
                (user_id, bt, pct, created_at_now()))
        grant_achievement(user_id, "lucky")
        _send_tg_message(user_id, f"🎰 Колесо фортуни: Знижка {pct}% на малі UC паки!")
    elif ptype == "nothing":
        db_exec("INSERT OR IGNORE INTO wheel_data (user_id) VALUES (?)", (user_id,))
        db_exec("UPDATE wheel_data SET consecutive_losses=consecutive_losses+1 WHERE user_id=?", (user_id,))
        check_achievements(user_id)
        _send_tg_message(user_id, "🎰 Колесо фортуни: Нічого не випало. Спробуй наступного разу!")
        return
    # Reset consecutive losses on win
    db_exec("INSERT OR IGNORE INTO wheel_data (user_id) VALUES (?)", (user_id,))
    db_exec("UPDATE wheel_data SET consecutive_losses=0 WHERE user_id=?", (user_id,))

# --- HTTP ОБРОБНИК ---
class PolicyHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", _cors_origin(self))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        query = self.path[len(path)+1:] if "?" in self.path else ""
        params = dict(urllib.parse.parse_qsl(query))
        ip = _get_client_ip(self)

        if _ip_is_blocked(ip):
            self.send_response(403); self.end_headers(); return
        _STATIC_IMG_SET = {"/favicon.ico","/nezuko.png","/nezuko_bg.png","/uc_icon.png","/prime_crown.png","/points_coin.png","/nezuko_love.png","/crate_icon.png","/nezuko_pubg_banner.png","/nezuko_tg_gifts_banner.png","/gift_easter.png","/gift_april.png","/gift_patrick.png","/gift_march8.png","/gift_valentine.png","/gift_loveu.png","/gift_xmas_bear.png","/gift_xmas_tree.png"}
        if path not in _STATIC_IMG_SET:
            if not _rl_allow(f"ip-get:{ip}", 200, 60):
                self.send_response(429); self.end_headers(); return

        if path == "/favicon.ico":
            self.send_response(204); self.end_headers(); return

        _STATIC_IMAGES = {
            "nezuko_bg.png","nezuko.png","uc_icon.png","prime_crown.png",
            "points_coin.png","nezuko_love.png","crate_icon.png",
            "nezuko_pubg_banner.png","nezuko_tg_gifts_banner.png",
            "gift_easter.png","gift_april.png","gift_patrick.png","gift_march8.png",
            "gift_valentine.png","gift_loveu.png","gift_xmas_bear.png","gift_xmas_tree.png",
        }
        if path in ("/nezuko.png", "/nezuko_bg.png", "/uc_icon.png", "/prime_crown.png", "/points_coin.png", "/nezuko_love.png", "/crate_icon.png") or path.lstrip("/") in _STATIC_IMAGES:
            fname = path.lstrip("/")
            img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attached_assets", fname)
            if os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404); self.end_headers()
            return

        if path in ("/", ""):
            self.send_response(302)
            self.send_header("Location", "/app")
            self.end_headers()
            return

        if path == "/app":
            _html_response(self, _load_miniapp_html()); return

        if path == "/api/admin/heartbeat":
            if _ip_is_blocked(ip):
                _json_response(self, {"ok": False}, 403); return
            if not _rl_allow(f"heartbeat:{ip}", 30, 60):
                _json_response(self, {"ok": False}, 429); return
            global admin_last_seen
            admin_last_seen = time.time()
            _json_response(self, {"ok": True}); return

        if path == "/api/orders":
            user_id = int(params.get("user_id", 0))
            if not user_id:
                _json_response(self, {"orders": []}); return
            rows = db_query(
                "SELECT id, pack, status, player_id, created_at, amount, player_nick FROM orders WHERE chat_id=? ORDER BY rowid DESC LIMIT 20",
                (user_id,)
            )
            orders = [{"id": r[0], "pack": r[1], "status": r[2], "player_id": r[3],
                       "created_at": (r[4] or "")[:16], "amount": r[5] or "?", "player_nick": r[6] or ""} for r in rows]
            _json_response(self, {"orders": orders}); return

        if path == "/api/all-orders":
            user_id = int(params.get("user_id", 0))
            if not user_id:
                _json_response(self, {"orders": []}); return
            rows = db_query(
                "SELECT id, pack, status, player_id, created_at, amount, player_nick FROM orders WHERE chat_id=? ORDER BY rowid DESC",
                (user_id,)
            )
            orders = [{"id": r[0], "pack": r[1], "status": r[2], "player_id": r[3],
                       "created_at": (r[4] or "")[:16], "amount": r[5] or "?", "player_nick": r[6] or ""} for r in rows]
            _json_response(self, {"orders": orders}); return

        if path == "/api/bonuses":
            user_id = int(params.get("user_id", 0))
            bonuses_raw = db_query("SELECT bonus_type, bonus_value, min_uc, max_uc FROM user_bonuses WHERE user_id=? AND used=0", (user_id,))
            ref_disc = db_query("SELECT id FROM ref_discounts WHERE user_id=?", (user_id,))
            # For custom discounts keep each entry separate (need min_uc/max_uc), group the rest
            result = []
            counts = {}
            for bt, bv, min_uc, max_uc in bonuses_raw:
                if bt.startswith("discount_custom_"):
                    pct = 0
                    try: pct = int(bt.split("_")[2])
                    except: pass
                    label = f"💸 Знижка {pct}% ({min_uc}–{max_uc} UC)"
                    result.append({"type": bt, "name": label, "count": 1, "min_uc": min_uc or 0, "max_uc": max_uc or 0})
                else:
                    counts[bt] = counts.get(bt, 0) + 1
            for bt, cnt in counts.items():
                result.append({"type": bt, "name": BONUS_TYPES.get(bt, bt), "count": cnt})
            if ref_disc:
                result.append({"type": "ref_discount", "name": "Реферальна знижка 1%", "count": len(ref_disc)})
            _json_response(self, {"bonuses": result}); return

        if path == "/api/top":
            rows = db_query(
                "SELECT user, chat_id, SUM(CAST(COALESCE(amount,0) AS INTEGER)) as total "
                "FROM orders WHERE status='done' GROUP BY chat_id ORDER BY total DESC LIMIT 10"
            )
            top = []
            for i, r in enumerate(rows):
                name = f"@{r[0]}" if r[0] else f"ID {r[1]}"
                top.append({"rank": i+1, "name": name, "total": r[2]})
            _json_response(self, {"top": top}); return

        if path == "/api/achievements":
            user_id = int(params.get("user_id", 0))
            earned_rows = db_query("SELECT achievement_id, granted_at FROM user_achievements WHERE user_id=?", (user_id,))
            earned = {r[0]: r[1] for r in earned_rows}
            result = []
            for aid, ach in ACHIEVEMENTS.items():
                total = db_query_one("SELECT COUNT(*) FROM user_achievements WHERE achievement_id=?", (aid,))[0]
                total_users = db_query_one("SELECT COUNT(DISTINCT user_id) FROM user_profile")[0] or 1
                pct = round(total / total_users * 100, 1)
                result.append({
                    "id": aid, "emoji": ach["emoji"], "name": ach["name"],
                    "desc": ach["desc"] if aid in earned else "???",
                    "hint": ach["hint"], "manual": ach["manual"],
                    "earned": aid in earned, "granted_at": (earned.get(aid) or "")[:10],
                    "count": total, "percent": pct
                })
            _json_response(self, {"achievements": result}); return

        if path == "/api/profile":
            user_id = int(params.get("user_id", 0))
            if not user_id:
                _json_response(self, {"ok": False}); return
            update_user_profile(user_id)
            profile = db_query_one("SELECT first_seen, last_seen, consecutive_days FROM user_profile WHERE user_id=?", (user_id,))
            done_orders = db_query("SELECT pack, amount FROM orders WHERE chat_id=? AND status='done'", (user_id,))
            total_orders = len(done_orders)
            total_spent = sum(get_pack_price(r[0]) for r in done_orders)
            total_uc = 0
            for pack, _ in done_orders:
                m = re.search(r"(\d+)\s*UC", pack)
                if m: total_uc += int(m.group(1))
            points = get_points(user_id)
            ach_count = db_query_one("SELECT COUNT(*) FROM user_achievements WHERE user_id=?", (user_id,))[0]
            _json_response(self, {
                "ok": True, "user_id": user_id,
                "first_seen": (profile[0] if profile else "")[:10],
                "consecutive_days": (profile[2] if profile else 0),
                "total_orders": total_orders, "total_spent": total_spent,
                "total_uc": total_uc, "points": points, "achievements": ach_count
            }); return

        if path == "/api/points":
            user_id = int(params.get("user_id", 0))
            pts = get_points(user_id)
            _json_response(self, {"ok": True, "points": pts}); return

        if path == "/api/points/shop":
            overrides = {r[0]: r[1] for r in db_query("SELECT item_id, cost FROM points_price_overrides")}
            items = [dict(i, cost=overrides.get(i["id"], i["cost"])) for i in POINTS_SHOP]
            _json_response(self, {"ok": True, "items": items}); return

        if path == "/api/online-count":
            cutoff = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            count = db_query_one("SELECT COUNT(*) FROM user_profile WHERE last_seen >= ?", (cutoff,))
            _json_response(self, {"online": count[0] if count else 0}); return

        if path == "/api/wheel/status":
            user_id = int(params.get("user_id", 0))
            wheel = db_query_one("SELECT last_free_spin FROM wheel_data WHERE user_id=?", (user_id,))
            can_spin = True
            seconds_left = 0
            if wheel and wheel[0]:
                try:
                    last = datetime.strptime(wheel[0], "%Y-%m-%d %H:%M:%S")
                    diff = (datetime.now() - last).total_seconds()
                    if diff < 86400:
                        can_spin = False
                        seconds_left = int(86400 - diff)
                except: pass
            has_extra = bool(db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type='extra_spin' AND used=0 LIMIT 1", (user_id,)))
            _json_response(self, {
                "can_spin_free": can_spin or has_extra,
                "has_extra_spin": has_extra,
                "seconds_left": seconds_left,
                "prizes": FREE_WHEEL_PRIZES,
                "paid_prizes": PAID_WHEEL_PRIZES
            }); return

        if path == "/api/prices":
            result = {}
            for pack, base_price in ALL_PACKS.items():
                override = db_query_one("SELECT price FROM price_overrides WHERE pack_name=?", (pack,))
                result[pack] = override[0] if override else base_price
            _json_response(self, {"ok": True, "prices": result}); return

        if path == "/api/tg-config":
            _json_response(self, {
                "ok": True,
                "shop_tag": SHOP_TAG,
                "stars_rate": get_stars_rate(),
                "payment_card": PAYMENT_CARD,
                "premium_packs": get_premium_packs()
            }); return

        if path == "/api/reviews":
            rows = db_query("SELECT rowid, user, text FROM reviews ORDER BY rowid DESC LIMIT 20")
            reviews = [{"id": r[0], "user": r[1], "text": r[2]} for r in rows]
            _json_response(self, {"ok": True, "reviews": reviews}); return

        if path == "/api/referral-stats":
            uid = int(params.get("user_id", 0))
            refs = db_query("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (uid,))
            discounts = db_query("SELECT COUNT(*) FROM ref_discounts WHERE user_id=?", (uid,))
            invited = refs[0][0] if refs else 0
            disc_count = discounts[0][0] if discounts else 0
            _json_response(self, {"ok": True, "invited": invited, "discounts": disc_count}); return

        if path == "/api/admin/orders":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            rows = db_query("SELECT id, user, pack, player_id, chat_id, created_at, amount, player_nick FROM orders WHERE status='pending' ORDER BY rowid DESC")
            orders = [{"id": r[0], "user": f"@{r[1]}" if r[1] else str(r[4]), "pack": r[2],
                       "player_id": r[3], "chat_id": r[4], "created_at": (r[5] or "")[:16], "amount": r[6] or "?", "player_nick": r[7] or ""} for r in rows]
            _json_response(self, {"ok": True, "orders": orders}); return

        if path == "/api/admin/stats":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            done = db_query_one("SELECT COUNT(*) FROM orders WHERE status='done'")[0]
            canceled = db_query_one("SELECT COUNT(*) FROM orders WHERE status='canceled'")[0]
            pending = db_query_one("SELECT COUNT(*) FROM orders WHERE status='pending'")[0]
            total_users = db_query_one("SELECT COUNT(DISTINCT user_id) FROM user_profile")[0] or 0
            total_sum = get_done_sum()
            today_sum = get_done_sum(today_only=True)
            open_tickets = db_query_one("SELECT COUNT(*) FROM tickets WHERE status IN ('open','answered','waiting')")[0]
            ai_today = db_query_one("SELECT COUNT(*) FROM ai_logs WHERE created_at LIKE ?", (created_at_now()[:10] + '%',))[0] or 0
            ai_total = db_query_one("SELECT COUNT(*) FROM ai_logs")[0] or 0
            _json_response(self, {"ok": True, "done": done, "canceled": canceled, "pending": pending,
                                  "total_sum": total_sum, "today_sum": today_sum, "users": total_users,
                                  "open_tickets": open_tickets, "ai_today": ai_today, "ai_total": ai_total}); return

        if path == "/api/admin/action-log":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            rows = db_query("SELECT id, admin_id, action, detail, ts FROM admin_action_log ORDER BY id DESC LIMIT 100")
            logs = [{"id": r[0], "admin_id": r[1], "action": r[2], "detail": r[3], "ts": (r[4] or "")[:16]} for r in rows]
            _json_response(self, {"ok": True, "logs": logs}); return

        if path == "/api/admin/donations":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            rows = db_query("SELECT id, user_id, username, amount, method, status, created_at FROM donations ORDER BY id DESC LIMIT 50")
            donations = [{"id": r[0], "user_id": r[1], "user": f"@{r[2]}" if r[2] else str(r[1]),
                          "amount": r[3], "method": r[4], "status": r[5], "created_at": (r[6] or "")[:16]} for r in rows]
            _json_response(self, {"ok": True, "donations": donations}); return

        if path == "/api/admin/ai_stats":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            total = db_query_one("SELECT COUNT(*) FROM ai_logs")[0] or 0
            today = db_query_one("SELECT COUNT(*) FROM ai_logs WHERE created_at LIKE ?", (created_at_now()[:10] + '%',))[0] or 0
            topics = db_query("SELECT topic, COUNT(*) as cnt FROM ai_logs GROUP BY topic ORDER BY cnt DESC LIMIT 6")
            topic_list = [{"topic": r[0], "count": r[1]} for r in topics]
            _json_response(self, {"ok": True, "total": total, "today": today, "topics": topic_list}); return

        if path == "/api/admin/admins":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            rows = db_query("SELECT id FROM admins")
            admins = [{"id": r[0]} for r in rows]
            _json_response(self, {"ok": True, "admins": admins, "owner_id": MY_ID}); return

        if path == "/api/admin/promos":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            rows = db_query("SELECT code, bonus_type, bonus_value, uses_left, total_uses, created_at, secret FROM promo_codes ORDER BY rowid DESC")
            promos = [{"code": r[0], "bonus_type": r[1], "bonus_label": BONUS_TYPES.get(r[1], r[1]),
                       "bonus_value": r[2], "uses_left": r[3], "total_uses": r[4],
                       "created_at": (r[5] or "")[:16], "secret": bool(r[6])} for r in rows]
            _json_response(self, {"ok": True, "promos": promos, "bonus_types": BONUS_TYPES}); return

        if path == "/api/admin/prices":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            result = {}
            for pack, base_price in ALL_PACKS.items():
                override = db_query_one("SELECT price FROM price_overrides WHERE pack_name=?", (pack,))
                result[pack] = {"current": override[0] if override else base_price, "base": base_price}
            _json_response(self, {"ok": True, "prices": result}); return

        if path == "/api/admin/tg-prices":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            packs = []
            for p in PREMIUM_PACKS_BASE:
                v = get_setting(f"{p['id']}_price")
                price = int(v) if v and v.isdigit() else p["price"]
                packs.append({"id": p["id"], "label": p["label"], "price": price, "base": p["price"]})
            _json_response(self, {
                "ok": True,
                "stars_rate": get_stars_rate(),
                "stars_rate_default": STARS_RATE_DEFAULT,
                "premium_packs": packs
            }); return

        if path == "/api/admin/reviews":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            rows = db_query("SELECT rowid, user, text FROM reviews ORDER BY rowid DESC LIMIT 30")
            reviews = [{"id": r[0], "user": r[1], "text": r[2]} for r in rows]
            _json_response(self, {"ok": True, "reviews": reviews}); return

        if path == "/api/admin/find":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            query = params.get("order_id", "").strip()
            if not query:
                _json_response(self, {"ok": False, "error": "Вкажи ID замовлення"}); return
            query_upper = query.upper()
            query_lower = query.lower().lstrip("@")
            sel = "SELECT id, user, pack, status, chat_id, player_id, created_at, amount, player_nick FROM orders"
            rows = (
                db_query(f"{sel} WHERE id=?", (query_upper,)) or
                db_query(f"{sel} WHERE player_id=?", (query,)) or
                db_query(f"{sel} WHERE LOWER(user)=?", (query_lower,)) or
                db_query(f"{sel} WHERE id LIKE ? OR player_id LIKE ? OR LOWER(user) LIKE ? ORDER BY rowid DESC LIMIT 10",
                         (f"%{query_upper}%", f"%{query}%", f"%{query_lower}%"))
            )
            if not rows:
                _json_response(self, {"ok": False, "error": "Замовлення не знайдено"}); return
            orders = [{"id": r[0], "user": f"@{r[1]}" if r[1] else str(r[4]),
                       "pack": r[2], "status": r[3], "chat_id": r[4],
                       "player_id": r[5], "created_at": (r[6] or "")[:16], "amount": r[7] or "?", "player_nick": r[8] or ""} for r in rows]
            result = orders[0] if len(orders) == 1 else orders
            _json_response(self, {"ok": True, "order": result, "orders": orders, "count": len(orders)}); return

        if path == "/api/admin/pending-wheels":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            rows = db_query("SELECT id, user_id, username, created_at FROM pending_wheel_spins WHERE status='pending' ORDER BY id DESC")
            spins = [{"id": r[0], "user_id": r[1],
                      "user": f"@{r[2]}" if r[2] else str(r[1]),
                      "created_at": (r[3] or "")[:16]} for r in rows]
            _json_response(self, {"ok": True, "spins": spins, "prizes": PAID_WHEEL_PRIZES}); return

        if path == "/api/admin/wheel-history":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            rows = db_query("SELECT id, user_id, username, created_at, status, prize_id FROM pending_wheel_spins WHERE status != 'pending' ORDER BY id DESC LIMIT 50")
            prize_map = {p["id"]: p["name"] for p in PAID_WHEEL_PRIZES}
            history = [{"id": r[0], "user_id": r[1],
                        "user": f"@{r[2]}" if r[2] else str(r[1]),
                        "created_at": (r[3] or "")[:16],
                        "status": r[4],
                        "prize": prize_map.get(r[5] or "", r[5] or "—")} for r in rows]
            _json_response(self, {"ok": True, "history": history}); return

        if path == "/api/cart":
            user_id = int(params.get("user_id", 0))
            if not user_id:
                _json_response(self, {"ok": False, "error": "user_id required"}); return
            rows = db_query("SELECT id, pack, added_at FROM cart WHERE user_id=? ORDER BY id DESC", (user_id,))
            items = [{"id": r[0], "pack": r[1], "added_at": (r[2] or "")[:16]} for r in rows]
            _json_response(self, {"ok": True, "items": items}); return

        if path == "/api/admin/carts":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            rows = db_query("SELECT id, user_id, pack, added_at FROM cart ORDER BY id DESC LIMIT 200")
            items = [{"id": r[0], "user_id": r[1], "pack": r[2], "added_at": (r[3] or "")[:16]} for r in rows]
            _json_response(self, {"ok": True, "items": items}); return

        if path == "/api/notifications":
            user_id = int(params.get("user_id", 0) or 0)
            if not user_id:
                _json_response(self, {"notifications": []}); return
            rows = db_query("SELECT id, type, message, created_at FROM notifications WHERE user_id=? AND read=0 ORDER BY id ASC LIMIT 20", (user_id,))
            notifs = [{"id": r[0], "type": r[1], "message": r[2], "created_at": r[3]} for r in rows]
            if notifs:
                ids = tuple(r[0] for r in rows[:20])
                placeholders = ",".join(["?"] * len(ids))
                db_exec(f"UPDATE notifications SET read=1 WHERE user_id=? AND id IN ({placeholders})", (user_id, *ids))
            _json_response(self, {"notifications": notifs}); return

        if path == "/api/ticket":
            user_id = int(params.get("user_id", 0) or 0)
            if not user_id:
                _json_response(self, {"tickets": []}); return
            rows = db_query("SELECT id, category, message, status, admin_reply, created_at, rating FROM tickets WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (user_id,))
            tickets = [{"id": r[0], "category": r[1], "message": r[2], "status": r[3], "admin_reply": r[4], "created_at": r[5], "rating": r[6]} for r in rows]
            _json_response(self, {"tickets": tickets}); return

        if path == "/api/ticket/messages":
            ticket_id = int(params.get("ticket_id", 0) or 0)
            user_id = int(params.get("user_id", 0) or 0)
            if not ticket_id or not user_id:
                _json_response(self, {"ok": False, "messages": []}); return
            t = db_query_one("SELECT user_id, category, status, rating FROM tickets WHERE id=?", (ticket_id,))
            if not t or t[0] != user_id:
                _json_response(self, {"ok": False, "error": "Доступ заборонено"}); return
            msgs = db_query("SELECT sender, message, created_at FROM ticket_messages WHERE ticket_id=? ORDER BY id ASC", (ticket_id,))
            messages = [{"sender": r[0], "message": r[1], "created_at": (r[2] or "")[:16]} for r in msgs]
            _json_response(self, {"ok": True, "messages": messages, "ticket": {
                "category": t[1], "status": t[2], "rating": t[3]
            }}); return

        if path == "/api/admin/tickets":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            status_filter = params.get("status", "all")
            if status_filter == "all":
                rows = db_query("SELECT id, user_id, username, category, message, status, created_at, rating FROM tickets ORDER BY id DESC LIMIT 200")
            else:
                rows = db_query("SELECT id, user_id, username, category, message, status, created_at, rating FROM tickets WHERE status=? ORDER BY id DESC LIMIT 200", (status_filter,))
            tickets = [{"id": r[0], "user_id": r[1],
                        "user": f"@{r[2]}" if r[2] else str(r[1]),
                        "category": r[3], "message": r[4], "status": r[5],
                        "created_at": (r[6] or "")[:16], "rating": r[7]} for r in rows]
            _json_response(self, {"ok": True, "tickets": tickets}); return

        if path == "/api/admin/ticket/messages":
            pwd = params.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            ticket_id = int(params.get("ticket_id", 0) or 0)
            if not ticket_id:
                _json_response(self, {"ok": False, "messages": []}); return
            ticket = db_query_one("SELECT user_id, username, category, status FROM tickets WHERE id=?", (ticket_id,))
            if not ticket:
                _json_response(self, {"ok": False, "error": "Тікет не знайдено"}); return
            msgs = db_query("SELECT sender, message, created_at FROM ticket_messages WHERE ticket_id=? ORDER BY id ASC", (ticket_id,))
            messages = [{"sender": r[0], "message": r[1], "created_at": (r[2] or "")[:16]} for r in msgs]
            _json_response(self, {"ok": True, "messages": messages, "ticket": {
                "user_id": ticket[0],
                "user": f"@{ticket[1]}" if ticket[1] else str(ticket[0]),
                "category": ticket[2], "status": ticket[3]
            }}); return

        self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_POST_BYTES:
            _json_response(self, {"ok": False, "error": "Request too large"}, 413); return
        body = self.rfile.read(min(length, MAX_POST_BYTES))
        try:
            data = json.loads(body)
        except Exception:
            _json_response(self, {"ok": False, "error": "Bad JSON"}, 400); return

        path = self.path.split("?")[0]
        ip = _get_client_ip(self)

        if _ip_is_blocked(ip):
            _json_response(self, {"ok": False, "error": "Доступ заборонено."}, 403); return
        if not _rl_allow(f"ip:{ip}", 120, 60):
            _ip_violation(ip, path, "IP rate limit exceeded")
            _json_response(self, {"ok": False, "error": "Забагато запитів. Зачекайте."}, 429); return

        if path == "/api/check-admin":
            init_data = str(data.get("init_data", ""))
            if not init_data:
                _json_response(self, {"ok": False, "is_admin": False, "error": "no init_data"}); return
            try:
                parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
                received_hash = parsed.pop("hash", "")
                data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
                secret = _hmac_mod.new(b"WebAppData", TOKEN.encode(), _hashlib_mod.sha256).digest()
                computed = _hmac_mod.new(secret, data_check.encode(), _hashlib_mod.sha256).hexdigest()
                if not _hmac_mod.compare_digest(computed, received_hash):
                    _json_response(self, {"ok": False, "is_admin": False, "error": "invalid hash"}); return
                user_str = parsed.get("user", "")
                user_obj = json.loads(user_str) if user_str else {}
                user_id = int(user_obj.get("id", 0))
                if not user_id:
                    _json_response(self, {"ok": False, "is_admin": False, "error": "no user"}); return
                admin_row = db_query_one("SELECT id FROM admins WHERE id=?", (user_id,))
                _json_response(self, {"ok": True, "is_admin": bool(admin_row), "user_id": user_id}); return
            except Exception as e:
                _json_response(self, {"ok": False, "is_admin": False, "error": str(e)}); return

        if path == "/api/resolve-user":
            init_data = str(data.get("init_data", ""))
            if not init_data:
                _json_response(self, {"ok": False, "error": "no init_data"}); return
            try:
                parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
                received_hash = parsed.pop("hash", "")
                data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
                secret = _hmac_mod.new(b"WebAppData", TOKEN.encode(), _hashlib_mod.sha256).digest()
                computed = _hmac_mod.new(secret, data_check.encode(), _hashlib_mod.sha256).hexdigest()
                if not _hmac_mod.compare_digest(computed, received_hash):
                    _ip_violation(ip, path, "invalid initData hash")
                    _json_response(self, {"ok": False, "error": "invalid hash"}); return
                user_str = parsed.get("user", "")
                user_obj = json.loads(user_str) if user_str else {}
                user_id = int(user_obj.get("id", 0))
                uname_val = user_obj.get("username") or user_obj.get("first_name") or ""
                if not user_id:
                    _json_response(self, {"ok": False, "error": "no user"}); return
                update_user_profile(user_id)
                _json_response(self, {"ok": True, "user_id": user_id, "username": uname_val}); return
            except Exception as e:
                _json_response(self, {"ok": False, "error": str(e)}); return

        if path == "/api/track-visit":
            user_id = int(data.get("user_id", 0))
            if user_id:
                if _rl_allow(f"visit:{user_id}", 5, 60):
                    update_user_profile(user_id)
                    check_achievements(user_id)
            _json_response(self, {"ok": True}); return

        if path == "/api/submit-order":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"order:{user_id}", 5, 60):
                _json_response(self, {"ok": False, "error": "Забагато замовлень. Зачекайте хвилину."}, 429); return
            username = _sanitize(str(data.get("username", "")), 64)
            pack = str(data.get("pack", ""))
            player_id = _sanitize(str(data.get("player_id", "")), 32).strip()
            player_nick = _sanitize(str(data.get("player_nick", "")).strip(), 64)
            base_amount = int(data.get("amount", 0))
            flash_order = bool(data.get("flash_order", False))
            mix_packs = data.get("mix_packs", None)
            if not pack or not player_id:
                _json_response(self, {"ok": False, "error": "Відсутні дані"}); return
            if not _valid_player_id(player_id):
                _ip_violation(ip, path, f"invalid player_id={player_id[:32]}")
                _json_response(self, {"ok": False, "error": "Ігровий ID повинен містити від 5 до 16 цифр"}); return
            # Mix order validation
            if mix_packs is not None:
                if not isinstance(mix_packs, list) or len(mix_packs) < 2:
                    _json_response(self, {"ok": False, "error": "Невірний мікс"}); return
                for mp in mix_packs:
                    if mp not in ALL_PACKS:
                        _json_response(self, {"ok": False, "error": f"Пак не знайдено: {mp}"}); return
                disc_pct, disc_src, disc_id = get_user_discount(user_id, mix_packs[0])
                first_price = apply_discount(get_pack_price(mix_packs[0]), disc_pct) if disc_pct else get_pack_price(mix_packs[0])
                extras_sum = sum(get_pack_price(mp) for mp in mix_packs[1:])
                true_sum = first_price + extras_sum
                if true_sum != base_amount:
                    _json_response(self, {"ok": False, "error": f"Невірна сума. Очікується {true_sum} грн"}); return
                final_price = true_sum
                if disc_pct and disc_src == "promo":
                    db_exec("UPDATE user_bonuses SET used=1 WHERE id=?", (disc_id,))
                elif disc_pct and disc_src == "ref":
                    db_exec("DELETE FROM ref_discounts WHERE id=?", (disc_id,))
                disc_pct = 0
            else:
                # Single pack — server price always used, client amount ignored
                if pack not in ALL_PACKS:
                    _json_response(self, {"ok": False, "error": "Пак не знайдено"}); return
                disc_pct, disc_src, disc_id = get_user_discount(user_id, pack)
                price = get_pack_price(pack)
                final_price = apply_discount(price, disc_pct) if disc_pct else price
                if disc_pct and disc_src == "promo":
                    db_exec("UPDATE user_bonuses SET used=1 WHERE id=?", (disc_id,))
                elif disc_pct and disc_src == "ref":
                    db_exec("DELETE FROM ref_discounts WHERE id=?", (disc_id,))
            order_id = str(uuid.uuid4()).replace("-", "")[:12].upper()
            db_exec(
                "INSERT INTO orders (id, user, pack, status, chat_id, player_id, created_at, amount, player_nick) VALUES (?,?,?,?,?,?,?,?,?)",
                (order_id, username, pack, "pending", user_id, player_id, created_at_now(), str(final_price), player_nick or None)
            )
            _notify_admin_order(order_id, pack, player_id, final_price, user_id, username,
                               mix_packs_list=mix_packs if mix_packs is not None else None,
                               player_nick=player_nick or None)
            update_user_profile(user_id)
            if flash_order:
                grant_achievement(user_id, "flash")
            _json_response(self, {"ok": True, "order_id": order_id, "final_price": final_price,
                                  "discount": disc_pct}); return

        if path == "/api/submit-tg-order":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"tgorder:{user_id}", 5, 60):
                _json_response(self, {"ok": False, "error": "Забагато замовлень. Зачекайте хвилину."}, 429); return
            username = _sanitize(str(data.get("username", "")), 64)
            pack = _sanitize(str(data.get("pack", "")), 128)
            tg_tag = _sanitize(str(data.get("tg_tag", "")), 64).strip()
            amount = int(data.get("amount", 0))
            if not pack or not tg_tag or amount <= 0:
                _json_response(self, {"ok": False, "error": "Відсутні дані"}); return
            if len(tg_tag) < 3:
                _json_response(self, {"ok": False, "error": "Введіть правильний Telegram тег"}); return
            order_id = str(uuid.uuid4()).replace("-", "")[:12].upper()
            db_exec(
                "INSERT INTO orders (id, user, pack, status, chat_id, player_id, created_at, amount) VALUES (?,?,?,?,?,?,?,?)",
                (order_id, username, pack, "pending", user_id, tg_tag, created_at_now(), str(amount))
            )
            try:
                user_label_str = f"@{username}" if username else str(user_id)
                text = (f"⭐ ТГ ЗАМОВЛЕННЯ!\n🆔 {order_id}\n👤 {user_label_str}\n🎁 {pack}\n📲 Тег: {tg_tag}\n💵 Сума: {amount} грн")
                ok_btn = json.dumps({"inline_keyboard": [[
                    {"text": "✅ Готово", "callback_data": f"ok_{order_id}"},
                    {"text": "❌ Відхилити", "callback_data": f"no_{order_id}"}
                ]]})
                params = urllib.parse.urlencode({"chat_id": MY_ID, "text": text, "reply_markup": ok_btn}).encode()
                req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=params)
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                logging.warning(f"TG order admin notify error: {e}")
            update_user_profile(user_id)
            _json_response(self, {"ok": True, "order_id": order_id}); return

        if path == "/api/cart/add":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"cart:{user_id}", 20, 60):
                _json_response(self, {"ok": False, "error": "Забагато дій з кошиком."}, 429); return
            pack = str(data.get("pack", ""))
            if not user_id or not pack:
                _json_response(self, {"ok": False, "error": "Невірні дані"}); return
            if pack not in ALL_PACKS:
                _json_response(self, {"ok": False, "error": "Пак не знайдено"}); return
            cart_count = db_query_one("SELECT COUNT(*) FROM cart WHERE user_id=?", (user_id,))
            if cart_count and cart_count[0] >= 20:
                _json_response(self, {"ok": False, "error": "Кошик переповнений (макс. 20 позицій)"}); return
            existing = db_query_one("SELECT id FROM cart WHERE user_id=? AND pack=?", (user_id, pack))
            if existing:
                _json_response(self, {"ok": False, "error": "Вже є в кошику"}); return
            db_exec("INSERT INTO cart (user_id, pack, added_at) VALUES (?,?,?)", (user_id, pack, created_at_now()))
            _json_response(self, {"ok": True, "message": "Додано до кошика"}); return

        if path == "/api/cart/remove":
            item_id = int(data.get("id", 0))
            user_id = int(data.get("user_id", 0))
            db_exec("DELETE FROM cart WHERE id=? AND user_id=?", (item_id, user_id))
            _json_response(self, {"ok": True}); return

        if path == "/api/ticket":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"ticket:{user_id}", 3, 300):
                _json_response(self, {"ok": False, "error": "Забагато тікетів. Зачекайте 5 хвилин."}, 429); return
            category = str(data.get("category", "")).strip()[:64]
            message = str(data.get("message", "")).strip()
            username = str(data.get("username", "")).strip()[:64]
            if not user_id or not category or not message:
                _json_response(self, {"ok": False, "error": "Невірні дані"}); return
            if len(message) > 1000:
                _json_response(self, {"ok": False, "error": "Повідомлення занадто довге (макс 1000 символів)"}); return
            open_count = db_query_one("SELECT COUNT(*) FROM tickets WHERE user_id=? AND status='open'", (user_id,))
            if open_count and open_count[0] >= 5:
                _json_response(self, {"ok": False, "error": "Забагато відкритих тікетів. Зачекайте відповіді адміна."}); return
            cur = db_exec("INSERT INTO tickets (user_id, chat_id, username, category, message, status, created_at) VALUES (?,?,?,?,?,?,?)",
                          (user_id, user_id, username, category, message, "open", created_at_now()))
            tid = cur.lastrowid
            db_exec("INSERT INTO ticket_messages (ticket_id, sender, message, created_at) VALUES (?,?,?,?)",
                    (tid, "user", message, created_at_now()))
            _notify_admin_ticket(tid, user_id, username, category, message)
            _ai_ticket_auto_reply(tid, category, user_id)
            _json_response(self, {"ok": True, "ticket_id": tid}); return

        if path == "/api/ticket/reply":
            pwd = data.get("password", "")
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}); return
            ticket_id = int(data.get("ticket_id", 0))
            reply = str(data.get("reply", "")).strip()
            if not ticket_id or not reply:
                _json_response(self, {"ok": False, "error": "Невірні дані"}); return
            ticket = db_query_one("SELECT user_id, category FROM tickets WHERE id=?", (ticket_id,))
            if not ticket:
                _json_response(self, {"ok": False, "error": "Тікет не знайдено"}); return
            db_exec("UPDATE tickets SET admin_reply=?, status='answered', replied_at=? WHERE id=?", (reply, created_at_now(), ticket_id))
            db_exec("INSERT INTO ticket_messages (ticket_id, sender, message, created_at) VALUES (?,?,?,?)",
                    (ticket_id, "admin", reply, created_at_now()))
            push_notification(ticket[0], "ticket_reply", f"🎫 Відповідь на тікет #{ticket_id} [{ticket[1]}]: {reply[:80]}")
            try:
                msg_txt = f"🎫 Відповідь по тікету #{ticket_id} [{ticket[1]}]:\n\n{reply}"
                p = urllib.parse.urlencode({"chat_id": ticket[0], "text": msg_txt}).encode()
                urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=p), timeout=5)
            except Exception as e:
                logging.warning(f"ticket reply send error: {e}")
            _json_response(self, {"ok": True}); return

        if path == "/api/admin/ticket/status":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            ticket_id = int(data.get("ticket_id", 0))
            status = str(data.get("status", "")).strip()
            valid_statuses = ("open", "waiting", "closed", "answered")
            if not ticket_id or status not in valid_statuses:
                _json_response(self, {"ok": False, "error": "Невірні дані"}); return
            ticket = db_query_one("SELECT user_id FROM tickets WHERE id=?", (ticket_id,))
            if not ticket:
                _json_response(self, {"ok": False, "error": "Тікет не знайдено"}); return
            db_exec("UPDATE tickets SET status=? WHERE id=?", (status, ticket_id))
            if status == "closed":
                push_notification(ticket[0], "ticket_closed", f"⬜ Тікет #{ticket_id} закрито адміном. Оцініть будь ласка підтримку!")
            _json_response(self, {"ok": True}); return

        if path == "/api/admin/ticket/message":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            ticket_id = int(data.get("ticket_id", 0))
            message = str(data.get("message", "")).strip()
            if not ticket_id or not message:
                _json_response(self, {"ok": False, "error": "Невірні дані"}); return
            ticket = db_query_one("SELECT user_id, category FROM tickets WHERE id=?", (ticket_id,))
            if not ticket:
                _json_response(self, {"ok": False, "error": "Тікет не знайдено"}); return
            db_exec("INSERT INTO ticket_messages (ticket_id, sender, message, created_at) VALUES (?,?,?,?)",
                    (ticket_id, "admin", message, created_at_now()))
            db_exec("UPDATE tickets SET status='answered', admin_reply=?, replied_at=? WHERE id=?",
                    (message, created_at_now(), ticket_id))
            push_notification(ticket[0], "ticket_reply", f"🎫 Відповідь на тікет #{ticket_id} [{ticket[1]}]: {message[:80]}")
            try:
                msg_txt = f"🎫 Відповідь по тікету #{ticket_id} [{ticket[1]}]:\n\n{message}"
                p = urllib.parse.urlencode({"chat_id": ticket[0], "text": msg_txt}).encode()
                urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=p), timeout=5)
            except Exception as e:
                logging.warning(f"ticket admin msg send error: {e}")
            _json_response(self, {"ok": True}); return

        if path == "/api/admin/ai_chat":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            message = str(data.get("message", "")).strip()
            history = data.get("history", [])
            if not message:
                _json_response(self, {"ok": False, "error": "Немає повідомлення"}); return
            oai_msgs = [{"role": "system", "content": ADMIN_ADVISOR_PROMPT}]
            for h in (history[-8:] if len(history) > 8 else history):
                oai_msgs.append({"role": "assistant" if h.get("role") == "assistant" else "user", "content": str(h.get("text", ""))[:600]})
            oai_msgs.append({"role": "user", "content": message})
            response = _gemini_api_call(oai_msgs, max_tokens=700)
            _json_response(self, {"ok": True, "response": response}); return

        if path == "/api/user/ai-chat":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"ai:{user_id}", 15, 60):
                _json_response(self, {"ok": False, "error": "Забагато запитів до AI. Зачекайте хвилину."}, 429); return
            message = str(data.get("message", "")).strip()[:500]
            history = data.get("history", [])
            if not isinstance(history, list):
                history = []
            if not message:
                _json_response(self, {"ok": False, "error": "Немає повідомлення"}); return
            if not GEMINI_API_KEY:
                _json_response(self, {"ok": False, "error": "AI тимчасово недоступний"}); return
            oai_messages = [{"role": "system", "content": AI_SYSTEM_PROMPT}]
            for h in (history[-6:] if len(history) > 6 else history):
                role = "assistant" if h.get("role") == "assistant" else "user"
                oai_messages.append({"role": role, "content": str(h.get("text", ""))[:400]})
            oai_messages.append({"role": "user", "content": message})
            response = _gemini_api_call(oai_messages, max_tokens=400)
            if user_id:
                topic = _detect_ai_topic(message)
                db_exec("INSERT INTO ai_logs (user_id, message, topic, created_at) VALUES (?,?,?,?)",
                        (user_id, message[:200], topic, created_at_now()))
            _json_response(self, {"ok": True, "response": response}); return

        if path == "/api/ticket/close":
            user_id = int(data.get("user_id", 0))
            ticket_id = int(data.get("ticket_id", 0))
            rating = int(data.get("rating", 0) or 0)
            if not user_id or not ticket_id:
                _json_response(self, {"ok": False, "error": "Невірні дані"}); return
            ticket = db_query_one("SELECT user_id FROM tickets WHERE id=?", (ticket_id,))
            if not ticket or ticket[0] != user_id:
                _json_response(self, {"ok": False, "error": "Доступ заборонено"}); return
            if 1 <= rating <= 5:
                db_exec("UPDATE tickets SET status='closed', rating=? WHERE id=?", (rating, ticket_id))
            else:
                db_exec("UPDATE tickets SET status='closed' WHERE id=?", (ticket_id,))
            _json_response(self, {"ok": True}); return

        if path == "/api/ticket/message":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"tktmsg:{user_id}", 5, 60):
                _json_response(self, {"ok": False, "error": "Забагато повідомлень. Зачекайте хвилину."}, 429); return
            ticket_id = int(data.get("ticket_id", 0))
            message = _sanitize(str(data.get("message", "")).strip(), 1000)
            username = _sanitize(str(data.get("username", "")).strip(), 64)
            if not user_id or not ticket_id or not message:
                _json_response(self, {"ok": False, "error": "Невірні дані"}); return
            if len(message) > 1000:
                _json_response(self, {"ok": False, "error": "Повідомлення занадто довге (макс. 1000 символів)"}); return
            ticket = db_query_one("SELECT user_id, category FROM tickets WHERE id=? AND user_id=?", (ticket_id, user_id))
            if not ticket:
                _json_response(self, {"ok": False, "error": "Тікет не знайдено"}); return
            db_exec("INSERT INTO ticket_messages (ticket_id, sender, message, created_at) VALUES (?,?,?,?)",
                    (ticket_id, "user", message, created_at_now()))
            db_exec("UPDATE tickets SET status='open' WHERE id=?", (ticket_id,))
            user_label = f"@{username}" if username else str(user_id)
            try:
                text = (f"🎫 Нове повідомлення у тікеті #{ticket_id} [{ticket[1]}]\n"
                        f"👤 {user_label}:\n\n{message}")
                reply_btn = json.dumps({"inline_keyboard": [[
                    {"text": "✏️ Відповісти", "callback_data": f"tkt_reply_{ticket_id}"}
                ]]})
                p = urllib.parse.urlencode({"chat_id": MY_ID, "text": text, "reply_markup": reply_btn}).encode()
                urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=p), timeout=5)
            except Exception as e:
                logging.warning(f"ticket user msg notify error: {e}")
            _ai_ticket_auto_reply(ticket_id, ticket[1], user_id)
            _json_response(self, {"ok": True}); return

        if path == "/api/promo":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"promo:{user_id}", 5, 300):
                _ip_violation(ip, path, f"promo brute-force uid={user_id}")
                _json_response(self, {"ok": False, "error": "Забагато спроб. Зачекайте 5 хвилин."}, 429); return
            code = _sanitize(str(data.get("code", "")), 32).strip().upper()
            if not code or not user_id:
                _json_response(self, {"ok": False, "error": "Невірні дані"}); return
            already = db_query_one("SELECT 1 FROM used_promo_codes WHERE user_id=? AND code=?", (user_id, code))
            if already:
                _json_response(self, {"ok": False, "error": "Промокод вже використано"}); return
            promo = db_query_one("SELECT bonus_type, bonus_value, uses_left, secret, min_uc, max_uc FROM promo_codes WHERE code=?", (code,))
            if not promo:
                _json_response(self, {"ok": False, "error": "Промокод не знайдено"}); return
            bonus_type, bonus_value, uses_left, is_secret, promo_min_uc, promo_max_uc = promo
            if uses_left is not None and uses_left != -1 and uses_left <= 0:
                _json_response(self, {"ok": False, "error": "Промокод вичерпано"}); return
            was_limited = uses_left is not None and uses_left != -1
            db_exec("INSERT OR IGNORE INTO used_promo_codes (user_id, code) VALUES (?,?)", (user_id, code))
            if bonus_type.startswith("points_"):
                pts = int(bonus_type.split("_")[1])
                add_points(user_id, pts, f"Промокод {code}")
            else:
                db_exec("INSERT INTO user_bonuses (user_id, bonus_type, bonus_value, min_uc, max_uc, created_at) VALUES (?,?,?,?,?,?)",
                        (user_id, bonus_type, bonus_value or 1, promo_min_uc or 0, promo_max_uc or 0, created_at_now()))
            if uses_left is not None and uses_left != -1:
                db_exec("UPDATE promo_codes SET uses_left=uses_left-1 WHERE code=?", (code,))
            if is_secret:
                grant_achievement(user_id, "secret_seeker")
            if was_limited:
                grant_achievement(user_id, "precise")
            check_achievements(user_id)
            if bonus_type.startswith("discount_custom_"):
                pct = 0
                try: pct = int(bonus_type.split("_")[2])
                except: pass
                bonus_name = f"💸 Знижка {pct}% ({promo_min_uc}–{promo_max_uc} UC)"
            elif bonus_type.startswith("points_"):
                pts = int(bonus_type.split("_")[1])
                bonus_name = f"🪙 {pts} балів"
            else:
                bonus_name = BONUS_TYPES.get(bonus_type, bonus_type)
            _json_response(self, {"ok": True, "message": f"Бонус активовано: {bonus_name}"}); return

        if path == "/api/submit-review":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"review:{user_id}", 3, 3600):
                _json_response(self, {"ok": False, "error": "Забагато відгуків. Зачекайте годину."}, 429); return
            username = str(data.get("username", "")).strip()[:64]
            text = str(data.get("text", "")).strip()
            if not text or len(text) < 3:
                _json_response(self, {"ok": False, "error": "Відгук занадто короткий"}); return
            if len(text) > 500:
                _json_response(self, {"ok": False, "error": "Відгук занадто довгий (макс. 500 символів)"}); return
            user_label_str = f"@{username}" if username else str(user_id)
            db_exec("INSERT INTO reviews (user, text) VALUES (?,?)", (user_label_str, text))
            _json_response(self, {"ok": True, "message": "Відгук збережено!"}); return

        if path == "/api/claim-free-uc":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"freeuc:{user_id}", 2, 3600):
                _json_response(self, {"ok": False, "error": "Забагато запитів. Зачекайте годину."}, 429); return
            username = _sanitize(str(data.get("username", "")), 64)
            player_id = _sanitize(str(data.get("player_id", "")).strip(), 32)
            player_nick = _sanitize(str(data.get("player_nick", "")).strip(), 64)
            bonus_type = str(data.get("bonus_type", "free_uc_60"))
            if not _valid_player_id(player_id):
                _ip_violation(ip, path, f"invalid player_id={player_id[:32]}")
                _json_response(self, {"ok": False, "error": "Ігровий ID повинен містити від 5 до 16 цифр"}); return
            bonus = db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type=? AND used=0 LIMIT 1", (user_id, bonus_type))
            if not bonus:
                _json_response(self, {"ok": False, "error": f"Бонус {BONUS_TYPES.get(bonus_type,'')} недоступний"}); return
            db_exec("UPDATE user_bonuses SET used=1 WHERE id=?", (bonus[0],))
            uc_count = 30 if bonus_type == "free_uc_30" else 60
            oid = str(uuid.uuid4()).replace("-", "")[:12].upper()
            db_exec("INSERT INTO orders (id, user, pack, status, chat_id, player_id, created_at, amount, payment, player_nick) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (oid, username, f"🎁 {uc_count} UC Free (бонус)", "pending", user_id, player_id, created_at_now(), 0, "bonus", player_nick or None))
            nick_line = f"\n🪪 Нік: {player_nick}" if player_nick else ""
            _send_tg_message(MY_ID, f"🎁 БЕЗКОШТОВНІ UC!\n🆔 {oid}\n👤 {'@'+username if username else str(user_id)}\n🎮 ID: {player_id}{nick_line}\n💵 {uc_count} UC (безкоштовно)")
            _json_response(self, {"ok": True, "message": f"Заявку прийнято! {uc_count} UC буде нараховано."}); return

        if path == "/api/points/spend":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"pts:{user_id}", 10, 60):
                _json_response(self, {"ok": False, "error": "Забагато запитів. Зачекайте."}, 429); return
            item_id = str(data.get("item_id", ""))
            item = next((i for i in POINTS_SHOP if i["id"] == item_id), None)
            if not item:
                _json_response(self, {"ok": False, "error": "Невідомий товар"}); return
            override = db_query_one("SELECT cost FROM points_price_overrides WHERE item_id=?", (item_id,))
            actual_cost = override[0] if override else item["cost"]
            pts = get_points(user_id)
            if pts < actual_cost:
                _json_response(self, {"ok": False, "error": f"Недостатньо балів. Потрібно {actual_cost}, є {pts}"}); return
            db_exec("UPDATE user_points SET points=points-? WHERE user_id=?", (actual_cost, user_id))
            db_exec("INSERT INTO user_points_tx (user_id, delta, reason, created_at) VALUES (?,?,?,?)",
                    (user_id, -actual_cost, f"Покупка: {item['name']}", created_at_now()))
            if item["bonus_type"] == "extra_spin":
                db_exec("INSERT INTO user_bonuses (user_id, bonus_type, bonus_value, used, created_at) VALUES (?,?,?,0,?)",
                        (user_id, "extra_spin", 1, created_at_now()))
            else:
                db_exec("INSERT INTO user_bonuses (user_id, bonus_type, bonus_value, used, created_at) VALUES (?,?,?,0,?)",
                        (user_id, item["bonus_type"], 1, created_at_now()))
            _json_response(self, {"ok": True, "message": f"✅ {item['name']} додано! Залишок балів: {pts - actual_cost}"}); return

        if path == "/api/wheel/spin-free":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"wheelf:{user_id}", 3, 60):
                _json_response(self, {"ok": False, "error": "Забагато спроб крутити колесо."}, 429); return
            username = _sanitize(str(data.get("username", "")), 64)
            # Check cooldown
            wheel = db_query_one("SELECT last_free_spin FROM wheel_data WHERE user_id=?", (user_id,))
            can_spin = True
            if wheel and wheel[0]:
                try:
                    last = datetime.strptime(wheel[0], "%Y-%m-%d %H:%M:%S")
                    if (datetime.now() - last).total_seconds() < 86400:
                        can_spin = False
                except: pass
            # Check extra spin bonus
            extra = db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type='extra_spin' AND used=0 LIMIT 1", (user_id,))
            if not can_spin and not extra:
                wheel_data = db_query_one("SELECT last_free_spin FROM wheel_data WHERE user_id=?", (user_id,))
                try:
                    last = datetime.strptime((wheel_data[0] if wheel_data else ""), "%Y-%m-%d %H:%M:%S")
                    seconds_left = max(0, int(86400 - (datetime.now() - last).total_seconds()))
                except:
                    seconds_left = 0
                _json_response(self, {"ok": False, "error": "Ще не можна крутити", "seconds_left": seconds_left}); return
            if extra:
                db_exec("UPDATE user_bonuses SET used=1 WHERE id=?", (extra[0],))
            else:
                db_exec("INSERT OR IGNORE INTO wheel_data (user_id) VALUES (?)", (user_id,))
                db_exec("UPDATE wheel_data SET last_free_spin=? WHERE user_id=?", (created_at_now(), user_id))
            prize = spin_wheel_random(FREE_WHEEL_PRIZES)
            deliver_wheel_prize(user_id, username, prize)
            check_achievements(user_id)
            _json_response(self, {"ok": True, "prize": prize}); return

        if path == "/api/wheel/spin-paid":
            user_id = int(data.get("user_id", 0))
            if user_id and not _rl_allow(f"wheelp:{user_id}", 5, 300):
                _json_response(self, {"ok": False, "error": "Забагато запитів на платне колесо."}, 429); return
            username = _sanitize(str(data.get("username", "")), 64)
            cur = db_exec("INSERT INTO pending_wheel_spins (user_id, username, created_at) VALUES (?,?,?)",
                    (user_id, username, created_at_now()))
            spin_id = cur.lastrowid
            # Track paid spin count
            db_exec("INSERT OR IGNORE INTO wheel_data (user_id) VALUES (?)", (user_id,))
            db_exec("UPDATE wheel_data SET paid_spin_count=paid_spin_count+1 WHERE user_id=?", (user_id,))
            user_label_str = f"@{username}" if username else str(user_id)
            try:
                ok_btn = json.dumps({"inline_keyboard": [[
                    {"text": "🎰 Крутнути колесо", "callback_data": f"wspin_{spin_id}"},
                    {"text": "❌ Відхилити", "callback_data": f"wdeny_{spin_id}"}
                ]]})
                params = urllib.parse.urlencode({
                    "chat_id": MY_ID,
                    "text": f"🎰 ПЛАТНЕ КОЛЕСО!\n🆔 #{spin_id}\n👤 {user_label_str}\n💵 40 грн",
                    "reply_markup": ok_btn
                }).encode()
                req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=params)
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                logging.warning(f"wheel notify failed: {e}")
            check_achievements(user_id)
            _json_response(self, {"ok": True, "message": "Заявку надіслано! Адмін підтвердить і крутне колесо."}); return

        if path == "/api/admin/auth":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if _ok_adm:
                _json_response(self, {"ok": True}); return
            _json_response(self, {"ok": False, "error": _err_adm}); return

        if path == "/api/admin/action":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            order_id = str(data.get("order_id", ""))
            action = str(data.get("action", ""))
            res = db_query_one("SELECT chat_id, pack, user, player_id FROM orders WHERE id=?", (order_id,))
            if not res:
                _json_response(self, {"ok": False, "error": "Замовлення не знайдено"}); return
            chat_id, pack, user, player_id = res
            if action == "ok":
                db_exec("UPDATE orders SET status='done', completed_at=? WHERE id=?", (created_at_now(), order_id))
                ref = db_query_one("SELECT referrer_id FROM referrals WHERE referred_id=?", (chat_id,))
                if ref:
                    db_exec("INSERT INTO ref_discounts (user_id, created_at) VALUES (?,?)", (ref[0], created_at_now()))
                    _send_tg_message(ref[0], "🎉 Ваш реферал зробив покупку! Ви отримали знижку 1%.")
                if "Мікс UC" in pack:
                    _send_tg_message(chat_id, f"✅ Мікс UC нараховано! 🎉\n💎 {pack}\nУсі UC вже у грі. Дякуємо за покупку! 🌸")
                else:
                    _send_tg_message(chat_id, f"✅ {pack} нараховано! Дякуємо за покупку 🌸")
                check_achievements(chat_id)
                push_notification(chat_id, "order_done", f"✅ {pack} нараховано! Дякуємо за покупку 🌸")
                _json_response(self, {"ok": True, "message": f"Замовлення {order_id} виконано"}); return
            elif action == "no":
                db_exec("UPDATE orders SET status='canceled' WHERE id=?", (order_id,))
                _send_tg_message(chat_id, f"❌ Ваше замовлення ({pack}) відхилено. Зверніться в підтримку.")
                push_notification(chat_id, "order_canceled", f"❌ Замовлення ({pack}) відхилено. Зверніться в підтримку.")
                _json_response(self, {"ok": True, "message": f"Замовлення {order_id} відхилено"}); return
            _json_response(self, {"ok": False, "error": "Невідома дія"}); return

        if path == "/api/admin/approve-wheel":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            spin_id = int(data.get("spin_id", 0))
            spin = db_query_one("SELECT user_id, username, status FROM pending_wheel_spins WHERE id=?", (spin_id,))
            if not spin:
                _json_response(self, {"ok": False, "error": "Запит не знайдено"}); return
            if spin[2] != "pending":
                _json_response(self, {"ok": False, "error": "Вже оброблено"}); return
            prize = spin_wheel_random(PAID_WHEEL_PRIZES)
            db_exec("UPDATE pending_wheel_spins SET status='done', prize_id=? WHERE id=?", (prize["id"], spin_id))
            deliver_wheel_prize(spin[0], spin[1], prize)
            check_achievements(spin[0])
            _json_response(self, {"ok": True, "message": f"Колесо крутнуто! Приз: {prize['name']}", "prize": prize}); return

        if path == "/api/admin/toggle-admin":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            target_id = int(data.get("user_id", 0))
            action = str(data.get("action", ""))
            if not target_id:
                _json_response(self, {"ok": False, "error": "Невірний user_id"}); return
            if target_id == MY_ID:
                _json_response(self, {"ok": False, "error": "Не можна змінити власника"}); return
            if action == "add":
                db_exec("INSERT OR IGNORE INTO admins (id) VALUES (?)", (target_id,))
                _json_response(self, {"ok": True, "message": f"Адмін {target_id} доданий"}); return
            elif action == "remove":
                db_exec("DELETE FROM admins WHERE id=?", (target_id,))
                _json_response(self, {"ok": True, "message": f"Адмін {target_id} видалений"}); return
            _json_response(self, {"ok": False, "error": "Невідома дія"}); return

        if path == "/api/admin/create-promo":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            code = str(data.get("code", "")).strip().upper()
            bonus_type = str(data.get("bonus_type", ""))
            uses = int(data.get("uses", -1))
            is_secret = int(bool(data.get("secret", False)))
            promo_min_uc = int(data.get("min_uc", 0))
            promo_max_uc = int(data.get("max_uc", 0))
            discount_pct = int(data.get("discount_pct", 0))
            if not code or not bonus_type:
                _json_response(self, {"ok": False, "error": "Заповни всі поля"}); return
            # Allow discount_custom_X type
            if bonus_type == "discount_custom":
                if discount_pct < 1 or discount_pct > 99:
                    _json_response(self, {"ok": False, "error": "Відсоток знижки має бути від 1 до 99"}); return
                if promo_min_uc <= 0 or promo_max_uc <= 0 or promo_min_uc >= promo_max_uc:
                    _json_response(self, {"ok": False, "error": "Невірний діапазон UC"}); return
                bonus_type = f"discount_custom_{discount_pct}"
                all_types_ok = True
            else:
                all_types = list(BONUS_TYPES.keys()) + ["points_50","points_100","points_200","points_500"]
                all_types_ok = bonus_type in all_types
            if not all_types_ok:
                _json_response(self, {"ok": False, "error": "Невідомий тип бонусу"}); return
            existing = db_query_one("SELECT 1 FROM promo_codes WHERE code=?", (code,))
            if existing:
                _json_response(self, {"ok": False, "error": "Промокод вже існує"}); return
            db_exec(
                "INSERT INTO promo_codes (code, bonus_type, bonus_value, uses_left, total_uses, created_at, secret, min_uc, max_uc) VALUES (?,?,?,?,?,?,?,?,?)",
                (code, bonus_type, 1, uses, uses, created_at_now(), is_secret, promo_min_uc, promo_max_uc)
            )
            db_exec("DELETE FROM used_promo_codes WHERE code=?", (code,))
            _json_response(self, {"ok": True, "message": f"Промокод {code} створено"}); return

        if path == "/api/admin/delete-promo":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            code = str(data.get("code", "")).strip().upper()
            if not code:
                _json_response(self, {"ok": False, "error": "Вкажи код"}); return
            db_exec("DELETE FROM promo_codes WHERE code=?", (code,))
            db_exec("DELETE FROM used_promo_codes WHERE code=?", (code,))
            _json_response(self, {"ok": True, "message": f"Промокод {code} видалено"}); return

        if path == "/api/admin/delete-review":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            review_id = int(data.get("review_id", 0))
            if not review_id:
                _json_response(self, {"ok": False, "error": "Невірний ID"}); return
            db_exec("DELETE FROM reviews WHERE rowid=?", (review_id,))
            _json_response(self, {"ok": True, "message": "Відгук видалено"}); return

        if path == "/api/admin/update-price":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            pack_name = str(data.get("pack_name", ""))
            price = int(data.get("price", 0))
            if pack_name not in ALL_PACKS:
                _json_response(self, {"ok": False, "error": "Пак не знайдено"}); return
            if price < 1:
                _json_response(self, {"ok": False, "error": "Невірна ціна"}); return
            db_exec("INSERT OR REPLACE INTO price_overrides (pack_name, price, updated_at) VALUES (?,?,?)",
                    (pack_name, price, created_at_now()))
            _json_response(self, {"ok": True, "message": f"Ціна оновлена: {pack_name} → {price} грн"}); return

        if path == "/api/admin/reset-price":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            pack_name = str(data.get("pack_name", ""))
            db_exec("DELETE FROM price_overrides WHERE pack_name=?", (pack_name,))
            _json_response(self, {"ok": True, "message": f"Ціна скинута до базової"}); return

        if path == "/api/admin/points-shop-prices":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            overrides = {r[0]: r[1] for r in db_query("SELECT item_id, cost FROM points_price_overrides")}
            result = {}
            for item in POINTS_SHOP:
                result[item["id"]] = {
                    "name": item["name"],
                    "base": item["cost"],
                    "current": overrides.get(item["id"], item["cost"])
                }
            _json_response(self, {"ok": True, "items": result}); return

        if path == "/api/admin/update-points-price":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            item_id = str(data.get("item_id", ""))
            cost = int(data.get("cost", 0))
            if not any(i["id"] == item_id for i in POINTS_SHOP):
                _json_response(self, {"ok": False, "error": "Товар не знайдено"}); return
            if cost < 1:
                _json_response(self, {"ok": False, "error": "Ціна має бути більше 0"}); return
            db_exec("INSERT OR REPLACE INTO points_price_overrides (item_id, cost, updated_at) VALUES (?,?,?)",
                    (item_id, cost, created_at_now()))
            _json_response(self, {"ok": True, "message": f"Ціну оновлено: {item_id} → {cost} балів"}); return

        if path == "/api/admin/reset-points-price":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            item_id = str(data.get("item_id", ""))
            db_exec("DELETE FROM points_price_overrides WHERE item_id=?", (item_id,))
            _json_response(self, {"ok": True, "message": "Ціну скинуто до базової"}); return

        if path == "/api/admin/update-tg-prices":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            updated = []
            stars_rate_raw = data.get("stars_rate")
            if stars_rate_raw is not None:
                try:
                    rate = float(stars_rate_raw)
                    if rate <= 0: raise ValueError
                    set_setting("stars_rate", str(rate))
                    updated.append(f"Курс зірок → {rate} грн/⭐")
                except Exception:
                    _json_response(self, {"ok": False, "error": "Невірний курс зірок"}); return
            prem_ids = {p["id"] for p in PREMIUM_PACKS_BASE}
            for key, val in data.items():
                if key.endswith("_price") and key[:-len("_price")] in prem_ids:
                    pack_id = key[:-len("_price")]
                    try:
                        price = int(val)
                        if price < 1: raise ValueError
                        set_setting(f"{pack_id}_price", str(price))
                        label = next((p["label"] for p in PREMIUM_PACKS_BASE if p["id"] == pack_id), pack_id)
                        updated.append(f"{label} → {price} грн")
                    except Exception:
                        _json_response(self, {"ok": False, "error": f"Невірна ціна для {key}"}); return
            if not updated:
                _json_response(self, {"ok": False, "error": "Нічого не оновлено"}); return
            _json_response(self, {"ok": True, "message": "Оновлено: " + ", ".join(updated)}); return

        if path == "/api/admin/reset-tg-prices":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            key = str(data.get("key", ""))
            prem_ids = {p["id"] for p in PREMIUM_PACKS_BASE}
            if key == "stars_rate":
                db_exec("DELETE FROM settings WHERE key='stars_rate'")
            elif key.endswith("_price") and key[:-len("_price")] in prem_ids:
                db_exec("DELETE FROM settings WHERE key=?", (key,))
            else:
                _json_response(self, {"ok": False, "error": "Невідомий ключ"}); return
            _json_response(self, {"ok": True, "message": "Скинуто до базового"}); return

        if path == "/api/admin/grant-achievement":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            target_id = int(data.get("user_id", 0))
            ach_id = str(data.get("achievement_id", ""))
            if not target_id or ach_id not in ACHIEVEMENTS:
                _json_response(self, {"ok": False, "error": "Невірні дані"}); return
            granted = grant_achievement(target_id, ach_id)
            if granted:
                _json_response(self, {"ok": True, "message": f"Досягнення {ach_id} видано"}); return
            _json_response(self, {"ok": False, "error": "Досягнення вже є"}); return

        if path == "/api/admin/revoke-achievement":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            target_id = int(data.get("user_id", 0))
            ach_id = str(data.get("achievement_id", ""))
            if not target_id or not ach_id:
                _json_response(self, {"ok": False, "error": "Невірні дані"}); return
            db_exec("DELETE FROM user_achievements WHERE user_id=? AND achievement_id=?", (target_id, ach_id))
            _json_response(self, {"ok": True, "message": f"Досягнення {ach_id} відкликано"}); return

        if path == "/api/admin/broadcast":
            pwd = str(data.get("password", ""))
            _ok_adm, _err_adm = is_trusted_admin_post(ip, pwd)
            if not _ok_adm:
                _json_response(self, {"ok": False, "error": _err_adm}, 403); return
            message = str(data.get("message", "")).strip()
            if not message:
                _json_response(self, {"ok": False, "error": "Повідомлення порожнє"}); return
            # Broadcast to ALL registered users (from user_profile + orders)
            uids = set()
            for r in db_query("SELECT DISTINCT user_id FROM user_profile"):
                uids.add(r[0])
            for r in db_query("SELECT DISTINCT chat_id FROM orders WHERE chat_id IS NOT NULL"):
                uids.add(r[0])
            sent = 0
            for chat_id in uids:
                try:
                    _send_tg_message(chat_id, message)
                    sent += 1
                except: pass
            _json_response(self, {"ok": True, "message": f"Розсилку надіслано. Отримали: {sent}"}); return

        self.send_response(404); self.end_headers()

    def log_message(self, format, *args):
        return


def start_policy_server():
    port = int(os.environ.get("PORT", 5000))
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("0.0.0.0", port), PolicyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logging.info(f"Веб-сервер запущено на порту {port}")


def _db_backup_worker():
    """Кожні 30 хв робить резервну копію бази у backup/ поряд із DB_PATH."""
    backup_dir = os.path.join(os.path.dirname(DB_PATH), "backup")
    os.makedirs(backup_dir, exist_ok=True)
    while True:
        try:
            import shutil
            ts = datetime.now().strftime("%Y%m%d_%H%M")
            dst = os.path.join(backup_dir, f"bot_{ts}.db")
            with db_lock:
                src_conn = sqlite3.connect(DB_PATH)
                bck_conn = sqlite3.connect(dst)
                src_conn.backup(bck_conn)
                bck_conn.close()
                src_conn.close()
            # Лишаємо лише 48 останніх файлів (1 доба)
            files = sorted(
                [f for f in os.listdir(backup_dir) if f.endswith(".db")],
                reverse=True
            )
            for old in files[48:]:
                try: os.remove(os.path.join(backup_dir, old))
                except: pass
            logging.info(f"DB backup: {dst}")
        except Exception as e:
            logging.warning(f"DB backup error: {e}")
        import time; time.sleep(1800)


def start_db_backup():
    threading.Thread(target=_db_backup_worker, daemon=True).start()
    logging.info("Автобекап БД запущено (кожні 30 хв)")


# --- ПОМІЧНИКИ ---
def is_admin(uid):
    if uid == MY_ID: return True
    return bool(db_query_one("SELECT id FROM admins WHERE id=?", (uid,)))

def is_trusted_admin(user_id, password):
    """Return True ONLY if password matches ADMIN_PASSWORD.
    user_id alone is never sufficient — prevents ID-spoofing auth bypass."""
    return _hmac_mod.compare_digest(str(password), ADMIN_PASSWORD)

def is_trusted_admin_post(ip: str, password: str) -> tuple:
    """For POST admin endpoints: password check WITH brute-force protection.
    Returns (ok: bool, error_msg: str)."""
    return _rl_admin_check(ip, password)

def is_banned(uid):
    return bool(db_query_one("SELECT user_id FROM banned_users WHERE user_id=?", (uid,)))

def _get_domain():
    return (
        os.getenv("BOT_DOMAIN") or
        os.getenv("REPLIT_DEV_DOMAIN") or
        (os.getenv("REPLIT_DOMAINS", "").split(",")[0].strip() or None) or
        os.getenv("RAILWAY_PUBLIC_DOMAIN") or
        os.getenv("RAILWAY_STATIC_URL", "").replace("https://", "").replace("http://", "").strip("/") or
        ""
    )

def get_policy_url():
    domain = _get_domain()
    return f"https://{domain}/policy" if domain else "/policy"

def get_pack_price(pack):
    override = db_query_one("SELECT price FROM price_overrides WHERE pack_name=?", (pack,))
    if override:
        return override[0]
    if pack in ALL_PACKS: return ALL_PACKS[pack]
    m = re.search(r"(\d+)\s*грн", pack)
    return int(m.group(1)) if m else 0

def dynamic_label(pack_key: str) -> str:
    current_price = get_pack_price(pack_key)
    updated = re.sub(r'-\s*\d+\s*грн$', f'- {current_price} грн', pack_key)
    return updated

def label_to_pack_key(label: str):
    if label in ALL_PACKS:
        return label
    for key in ALL_PACKS:
        if dynamic_label(key) == label:
            return key
    return None

def status_text(s):
    return {"pending": "очікує виконання", "done": "виконано", "canceled": "відхилено"}.get(s, s)

def created_at_now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def user_label(username, chat_id=None):
    if username: return f"@{username}"
    return str(chat_id) if chat_id else "Без username"

def get_done_sum(today_only=False):
    if today_only:
        today = datetime.now().strftime("%Y-%m-%d")
        rows = db_query("SELECT CAST(COALESCE(amount, 0) AS INTEGER) FROM orders WHERE status='done' AND COALESCE(completed_at, created_at) LIKE ?", (f"{today}%",))
    else:
        rows = db_query("SELECT CAST(COALESCE(amount, 0) AS INTEGER) FROM orders WHERE status='done'")
    return sum(r[0] for r in rows)

def get_user_discount(uid, pack_name):
    # Extract UC amount from pack name for range checks
    pack_uc = 0
    m = re.search(r"^(\d+)\s*UC", pack_name)
    if m:
        pack_uc = int(m.group(1))
    if pack_name in SMALL_UC:
        for pct in [5, 4, 3, 2, 1]:
            b = db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type=? AND used=0 LIMIT 1", (uid, f"discount_small_{pct}"))
            if b: return pct, "promo", b[0]
    if pack_name in MEDIUM_UC:
        for pct in [2, 1]:
            b = db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type=? AND used=0 LIMIT 1", (uid, f"discount_medium_{pct}"))
            if b: return pct, "promo", b[0]
    # Check custom range discounts
    if pack_uc > 0:
        custom = db_query("SELECT id, bonus_type, min_uc, max_uc FROM user_bonuses WHERE user_id=? AND bonus_type LIKE 'discount_custom_%' AND used=0", (uid,))
        best_pct, best_id = 0, None
        for row in custom:
            bid, bt, min_uc, max_uc = row
            if (min_uc or 0) <= pack_uc <= (max_uc or 0):
                try:
                    pct = int(bt.split("_")[2])
                except:
                    pct = 0
                if pct > best_pct:
                    best_pct, best_id = pct, bid
        if best_pct > 0:
            return best_pct, "promo", best_id
    r = db_query_one("SELECT id FROM ref_discounts WHERE user_id=? LIMIT 1", (uid,))
    if r: return 1, "ref", r[0]
    return 0, None, None

def apply_discount(price, pct):
    return max(1, int(price * (100 - pct) / 100))

def uses_left_label(uses_left, total_uses=None):
    if uses_left is None or uses_left == -1: return "∞ безліміт"
    if total_uses is not None and total_uses != -1:
        return f"{uses_left}/{total_uses} активацій"
    return f"{uses_left} активацій залишилось"


# --- КОМАНДИ ---
def get_miniapp_url():
    domain = _get_domain()
    if domain:
        return f"https://{domain}/app"
    logging.warning("BOT_DOMAIN не задан — кнопка Mini App не буде працювати! Встанови змінну BOT_DOMAIN.")
    return ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states[uid] = None
    update_user_profile(uid)
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg[4:])
                if referrer_id != uid and not db_query_one("SELECT referred_id FROM referrals WHERE referred_id=?", (uid,)):
                    db_exec("INSERT OR IGNORE INTO referrals (referrer_id, referred_id, created_at) VALUES (?,?,?)", (referrer_id, uid, created_at_now()))
            except: pass
    webapp_url = get_miniapp_url()
    if webapp_url:
        mini_app_btn = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌸 Відкрити Mini App", web_app=WebAppInfo(url=webapp_url))
        ]])
    else:
        mini_app_btn = None
    # Hidden letter N in the welcome message
    await update.message.reply_text(
        "👋 Вітаємо у магазині UC від Nezuko! 🌸\n\n"
        "Скористайся зручним міні-застосунком або звичайними кнопками нижче.\n\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        "🔤 N · · · · · · · · · · · · ·",
        reply_markup=mini_app_btn
    )
    await update.message.reply_text("⬇️ Або обери дію:", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))

async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    earned = db_query("SELECT achievement_id FROM user_achievements WHERE user_id=?", (uid,))
    earned_set = {r[0] for r in earned}
    total_all = len(ACHIEVEMENTS)
    msg = f"🏅 МОЇ ДОСЯГНЕННЯ ({len(earned_set)}/{total_all}):\n\n"
    for aid, ach in ACHIEVEMENTS.items():
        if aid in earned_set:
            msg += f"✅ {ach['emoji']} {ach['name']}\n"
        else:
            msg += f"🔒 ??? ({ach['hint']})\n"
    await update.message.reply_text(msg[:4096])

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    update_user_profile(uid)
    done_orders = db_query("SELECT pack FROM orders WHERE chat_id=? AND status='done'", (uid,))
    total_spent = sum(get_pack_price(r[0]) for r in done_orders)
    total_uc = 0
    for (pack,) in done_orders:
        m = re.search(r"(\d+)\s*UC", pack)
        if m: total_uc += int(m.group(1))
    pts = get_points(uid)
    ach_count = db_query_one("SELECT COUNT(*) FROM user_achievements WHERE user_id=?", (uid,))[0]
    profile = db_query_one("SELECT first_seen FROM user_profile WHERE user_id=?", (uid,))
    first_seen = (profile[0] or "")[:10] if profile else "—"
    await update.message.reply_text(
        f"👤 ПРОФІЛЬ\n\n"
        f"🆔 ID: {uid}\n"
        f"📅 З нами з: {first_seen}\n\n"
        f"💰 Витрачено: {total_spent} грн\n"
        f"💎 Донатовано UC: {total_uc} UC\n"
        f"📦 Замовлень: {len(done_orders)}\n\n"
        f"🪙 Бали: {pts}\n"
        f"🏅 Досягнень: {ach_count}/{len(ACHIEVEMENTS)}"
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_admin(uid):
        if not _admin_session_valid(uid):
            _admin_logout(uid)
            user_states[uid] = "WAIT_PASS"
            await update.message.reply_text("⏰ Сесія закінчилась. Введіть пароль:", reply_markup=ReplyKeyboardRemove())
            return
        _admin_touch(uid)
        user_states[uid] = "ADMIN_MODE"
        await update.message.reply_text("🔘 Адмін-панель:", reply_markup=ReplyKeyboardMarkup(ADMIN_KB, resize_keyboard=True))
    else:
        user_states[uid] = "WAIT_PASS"
        await update.message.reply_text("🔑 Пароль:", reply_markup=ReplyKeyboardRemove())

async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Hidden letter K in shop
    await update.message.reply_text("🛍 Оберіть категорію:\n· · · K · · ·", reply_markup=SHOP_KB)

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Оберіть пакет:", reply_markup=ReplyKeyboardMarkup([[p] for p in PACKS.keys()], resize_keyboard=True))

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Питання/Спілкування", callback_data="tkt_cat_💬 Питання/Спілкування")],
        [InlineKeyboardButton("🐛 Баг", callback_data="tkt_cat_🐛 Баг")],
        [InlineKeyboardButton("📦 Проблема з замовленням", callback_data="tkt_cat_📦 Проблема з замовленням")],
        [InlineKeyboardButton("💳 Питання по оплаті", callback_data="tkt_cat_💳 Питання по оплаті")],
        [InlineKeyboardButton("💬 Написати @Manager_Nezuko", url="https://t.me/Manager_Nezuko")],
    ])
    await update.message.reply_text(
        "🎫 *Підтримка 24/7*\n\nОберіть категорію тікету або напишіть напряму менеджеру:",
        reply_markup=btns, parse_mode="Markdown"
    )

async def policy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    policy_text = (
        "📄 *ПОЛІТИКА МАГАЗИНУ UC*\n\n"
        "Наш магазин надає послуги з поповнення UC для гравців PUBG Mobile. "
        "Оформлюючи замовлення, клієнт погоджується з правилами роботи магазину.\n\n"
        "*1. Оформлення замовлення*\n"
        "Клієнт самостійно обирає потрібний пакет UC та вказує свій ігровий ID. "
        "Перед оплатою необхідно уважно перевірити правильність введених даних.\n\n"
        "*2. Оплата*\n"
        "Замовлення передається в обробку тільки після підтвердження оплати. "
        "Якщо оплата не була здійснена або не підтверджена, замовлення не виконується.\n\n"
        "*3. Виконання замовлення*\n"
        "Після оплати UC нараховуються на вказаний клієнтом ігровий ID. "
        "Час виконання може залежати від навантаження та доступності сервісу.\n\n"
        "*4. Відповідальність клієнта*\n"
        "Магазин не несе відповідальності за помилки у введеному ігровому ID. "
        "Якщо клієнт вказав неправильний ID, повернення коштів або повторне нарахування не гарантується.\n\n"
        "*5. Повернення коштів*\n"
        "Повернення коштів можливе лише у випадку, якщо замовлення ще не було виконано. "
        "Після успішного нарахування UC повернення коштів не здійснюється.\n\n"
        "*6. Підтримка*\n"
        "Якщо виникли питання або проблеми із замовленням, клієнт може звернутися до підтримки. "
        "Ми намагаємося допомогти кожному клієнту якнайшвидше.\n"
        "Підтримка: @Manager\\_Nezuko\n\n"
        "*7. Зміна правил*\n"
        "Магазин залишає за собою право змінювати ці правила. "
        "Актуальна політика діє на момент оформлення замовлення.\n\n"
        "*8. Флуд у особисті повідомлення*\n"
        "Якщо клієнт після оформлення замовлення починає надсилати повідомлення на кшталт «Де мої UC?» — "
        "магазин має право відмовити в обслуговуванні. Писати нагадування допустимо лише якщо з моменту "
        "замовлення пройшло більше 10 хвилин.\n\n"
        "_Оформлюючи замовлення, клієнт підтверджує, що ознайомився з цією політикою та погоджується з її умовами._"
    )
    await update.message.reply_text(policy_text, parse_mode="Markdown")

async def reviews_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    revs = db_query("SELECT user, text FROM reviews ORDER BY rowid DESC LIMIT 15")
    if not revs:
        await update.message.reply_text("🌟 Відгуків ще немає."); return
    msg = "🌟 ОСТАННІ ВІДГУКИ:\n\n"
    for r in revs: msg += f"👤 {r[0]}: {r[1]}\n\n"
    await update.message.reply_text(msg[:4096])

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    order = db_query_one("SELECT id, pack, status FROM orders WHERE chat_id=? ORDER BY rowid DESC LIMIT 1", (uid,))
    if not order:
        await update.message.reply_text("📭 У вас ще немає замовлень."); return
    await update.message.reply_text(f"📦 Останній заказ:\n🆔 {order[0]}\n🎁 {order[1]}\n📌 Статус: {status_text(order[2])}")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # Hidden letter U
    orders = db_query("SELECT id, pack, status FROM orders WHERE chat_id=? ORDER BY rowid DESC LIMIT 5", (uid,))
    if not orders:
        await update.message.reply_text("📭 У вас ще немає замовлень.\n· · · U · · ·"); return
    msg = "📋 Останні замовлення:\n\n"
    for o in orders: msg += f"🆔 {o[0]}\n🎁 {o[1]}\n📌 {status_text(o[2])}\n\n"
    await update.message.reply_text(msg)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Hidden letter E
    await update.message.reply_text(
        "ℹ️ Як користуватися ботом:\n\n"
        "1. «🛍 Магазин» → обери категорію\n"
        "2. Оберіть пакет UC\n"
        "3. Введіть ігровий ID\n"
        "4. Напишіть «ОК» та оплатіть\n"
        "5. Після оплати натисніть «✅ Я оплатив»\n\n"
        "Підтримка 24/7: /support\n"
        "Політика: /policy\n"
        "Статус: /status\n"
        "Профіль: /profile\n"
        "Досягнення: /achievements\n\n"
        "· · E · · · · · · · · ·"
    )

async def mypromos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bonuses = db_query("SELECT bonus_type, bonus_value FROM user_bonuses WHERE user_id=? AND used=0", (uid,))
    ref_discounts = db_query("SELECT id FROM ref_discounts WHERE user_id=?", (uid,))
    pts = get_points(uid)
    if not bonuses and not ref_discounts:
        await update.message.reply_text(f"🎁 Активних бонусів немає.\n🪙 Бали: {pts}"); return
    msg = f"🎁 АКТИВНІ БОНУСИ:\n\n"
    counts = {}
    for bt, _ in bonuses:
        counts[bt] = counts.get(bt, 0) + 1
    for bt, cnt in counts.items():
        msg += f"✅ {BONUS_TYPES.get(bt, bt)} × {cnt}\n"
    if ref_discounts:
        msg += f"✅ Реферальна знижка 1% × {len(ref_discounts)}\n"
    msg += f"\n🪙 Бали: {pts}"
    await update.message.reply_text(msg)

async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Тільки для адміна."); return
    orders = db_query("SELECT id, user, pack, player_id, chat_id FROM orders WHERE status='pending'")
    if not orders:
        await update.message.reply_text("📭 Немає замовлень."); return
    for o in orders:
        btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data=f"ok_{o[0]}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{o[0]}")]])
        await update.message.reply_text(f"📦 {o[2]}\n👤 {user_label(o[1], o[4])}\n🎮 ID: `{o[3]}`\n🆔 `{o[0]}`", reply_markup=btns)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Тільки для адміна."); return
    done_count     = db_query_one("SELECT COUNT(*) FROM orders WHERE status='done'")[0]
    canceled_count = db_query_one("SELECT COUNT(*) FROM orders WHERE status='canceled'")[0]
    pending_count  = db_query_one("SELECT COUNT(*) FROM orders WHERE status='pending'")[0]
    total_sum      = get_done_sum()
    today_sum      = get_done_sum(today_only=True)
    total_users    = db_query_one("SELECT COUNT(*) FROM user_profile")[0]
    today          = datetime.now().strftime("%Y-%m-%d")
    new_today      = db_query_one("SELECT COUNT(*) FROM user_profile WHERE first_seen LIKE ?", (f"{today}%",))[0]
    banned_count   = db_query_one("SELECT COUNT(*) FROM banned_users")[0]
    banned_rows    = db_query("SELECT user_id, reason, banned_at FROM banned_users ORDER BY banned_at DESC LIMIT 5")

    text = (
        f"📊 Статистика магазину\n"
        f"{'─'*28}\n"
        f"✅ Виконано замовлень: {done_count}\n"
        f"⏳ Очікують: {pending_count}\n"
        f"❌ Відхилено: {canceled_count}\n"
        f"{'─'*28}\n"
        f"💰 Загальна сума: {total_sum} грн\n"
        f"📅 Сьогодні: {today_sum} грн\n"
        f"{'─'*28}\n"
        f"👥 Всього користувачів: {total_users}\n"
        f"🆕 Нових сьогодні: {new_today}\n"
        f"🚫 Заблоковано: {banned_count}\n"
    )
    if banned_rows:
        text += f"\n🚫 Останні бани:\n"
        for r in banned_rows:
            text += f"  • ID {r[0]} — {r[1]} ({(r[2] or '')[:10]})\n"
        if banned_count > 5:
            text += f"  …та ще {banned_count - 5}. Повний список: /unban"
    await update.message.reply_text(text)

async def importdb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != MY_ID:
        await update.message.reply_text("⛔ Тільки для власника бота."); return
    user_states[uid] = "WAIT_DB_FILE"
    await update.message.reply_text(
        "📂 Надішліть файл бази даних (.db) у відповідь на це повідомлення.\n\n"
        "⚠️ Поточна база буде замінена! Перед заміною автоматично збережеться резервна копія.\n\n"
        "Для скасування надішліть /cancel"
    )


def reconnect_db(new_path: str, source_path: str = None):
    """Close current connection, optionally replace DB file, then reopen.
    
    source_path: if given, copy this file to new_path AFTER safely closing
                 the old connection (prevents old WAL from corrupting new DB).
    """
    global conn
    with db_lock:
        # Checkpoint WAL so pending writes are flushed before we close
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        # Remove WAL/SHM files left from the old connection so they cannot
        # be replayed onto the newly imported database file
        for _ext in ("-wal", "-shm"):
            _p = new_path + _ext
            if os.path.exists(_p):
                try:
                    os.remove(_p)
                except Exception:
                    pass
        # Now it is safe to place the new DB file — no old WAL remains
        if source_path:
            import shutil as _shutil
            _shutil.copy2(source_path, new_path)
        conn = sqlite3.connect(new_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.commit()
        run_migrations(conn)


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Тільки для адміна."); return
    await update.message.reply_text("⏳ Створюю резервну копію бази даних...")
    try:
        import shutil, tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        with db_lock:
            src = sqlite3.connect(DB_PATH)
            dst = sqlite3.connect(tmp.name)
            src.backup(dst)
            dst.close()
            src.close()
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        with open(tmp.name, "rb") as f:
            await context.bot.send_document(
                chat_id=uid,
                document=f,
                filename=f"bot_backup_{ts}.db",
                caption=f"🗄 Резервна копія бази даних\n📅 {ts}\n📦 Файл: bot_backup_{ts}.db"
            )
        os.remove(tmp.name)
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка бекапу: {e}")


async def restartbot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != MY_ID:
        await update.message.reply_text("⛔ Тільки для власника бота."); return
    await update.message.reply_text(
        "🔄 Перезапускаю бота...\n\n"
        "⏳ Через кілька секунд бот знову буде доступний."
    )
    import asyncio as _asyncio
    # Schedule stop so the reply is sent before polling ends.
    # The outer while-loop in __main__ will restart main() automatically.
    _asyncio.get_event_loop().call_later(1.5, lambda: _asyncio.ensure_future(context.application.stop()))


async def handle_broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Хендлер для медіа-повідомлень під час очікування розсилки (фото, відео, стікер тощо)."""
    uid = update.effective_user.id
    if not is_admin(uid): return
    if is_banned(uid): return

    if user_states.get(uid) == "WAIT_DB_FILE" and uid == MY_ID:
        doc = update.message.document
        if not doc:
            await update.message.reply_text("❌ Надішліть файл бази даних (.db)"); return
        await update.message.reply_text("⏳ Завантажую файл та перевіряю...")
        try:
            import shutil, tempfile
            tg_file = await context.bot.get_file(doc.file_id)
            tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            tmp.close()
            await tg_file.download_to_drive(tmp.name)
            # Validate SQLite magic bytes
            with open(tmp.name, "rb") as f:
                magic = f.read(16)
            if not magic.startswith(b"SQLite format 3"):
                os.remove(tmp.name)
                await update.message.reply_text(
                    "❌ Файл не є базою SQLite. Операцію скасовано.\n"
                    "Спробуйте ще раз — надішліть правильний .db файл."
                )
                return  # state stays WAIT_DB_FILE — user can retry
            # Backup current db using sqlite3.backup API (safe, respects WAL)
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            backup_path = os.path.join(os.path.dirname(DB_PATH), f"backup_before_import_{ts}.db")
            with db_lock:
                src = sqlite3.connect(DB_PATH)
                dst = sqlite3.connect(backup_path)
                src.backup(dst)
                dst.close()
                src.close()
            # reconnect_db checkpoints WAL, closes old conn, removes WAL/SHM,
            # copies source_path → DB_PATH, then opens a fresh connection.
            # This is the only correct order — copying BEFORE closing causes
            # the old WAL to overwrite the new DB on the next checkpoint.
            reconnect_db(DB_PATH, source_path=tmp.name)
            os.remove(tmp.name)
            # Success — only NOW clear the state
            user_states[uid] = None
            await update.message.reply_text(
                f"✅ База даних успішно замінена!\n\n"
                f"📦 Резервна копія збережена:\n`backup_before_import_{ts}.db`\n\n"
                f"🔄 Бот переключився на нову базу.",
                parse_mode="Markdown"
            )
            logging.info(f"DB imported by owner {uid}, backup: {backup_path}")
        except Exception as e:
            # State stays WAIT_DB_FILE so user can retry without /importdb
            await update.message.reply_text(
                f"❌ Помилка імпорту: {e}\n\n"
                f"Надішліть файл ще раз — повторювати /importdb не потрібно."
            )
            logging.error(f"DB import error: {e}")
        return

    if user_states.get(uid) == "WAIT_BROADCAST":
        user_states[uid] = "ADMIN_MODE"
        await send_broadcast(update, context, update.message)


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Тільки для адміна."); return
    if not context.args:
        await update.message.reply_text(
            "Використання: /ban <user_id> [причина]\n\n"
            "Приклад: /ban 123456789 спам\n\n"
            "User ID можна знайти командою /find або з повідомлення."
        ); return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID має бути числом."); return
    if target_id == MY_ID or is_admin(target_id):
        await update.message.reply_text("❌ Не можна забанити адміна."); return
    reason = " ".join(context.args[1:]) or "без причини"
    db_exec(
        "INSERT OR REPLACE INTO banned_users (user_id, reason, banned_at) VALUES (?,?,?)",
        (target_id, reason, created_at_now())
    )
    await update.message.reply_text(
        f"🚫 Користувача {target_id} заблоковано.\n"
        f"📝 Причина: {reason}\n\n"
        f"Для розблокування: /unban {target_id}"
    )
    # Повідомляємо забаненого
    try:
        await context.bot.send_message(
            target_id,
            "🚫 Вас заблоковано в боті UC Shop · Nezuko.\n"
            f"📝 Причина: {reason}\n\n"
            "Для оскарження зверніться до підтримки."
        )
    except: pass


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Тільки для адміна."); return
    if not context.args:
        # Показати список заблокованих
        rows = db_query("SELECT user_id, reason, banned_at FROM banned_users ORDER BY banned_at DESC")
        if not rows:
            await update.message.reply_text("✅ Немає заблокованих користувачів."); return
        lines = ["🚫 Заблоковані користувачі:\n"]
        for r in rows:
            lines.append(f"• ID {r[0]} — {r[1]} ({(r[2] or '')[:10]})")
        lines.append(f"\nДля розблокування: /unban <user_id>")
        await update.message.reply_text("\n".join(lines)); return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID має бути числом."); return
    existed = db_query_one("SELECT user_id FROM banned_users WHERE user_id=?", (target_id,))
    if not existed:
        await update.message.reply_text(f"ℹ️ Користувач {target_id} не заблокований."); return
    db_exec("DELETE FROM banned_users WHERE user_id=?", (target_id,))
    await update.message.reply_text(f"✅ Користувача {target_id} розблоковано.")
    try:
        await context.bot.send_message(
            target_id,
            "✅ Вас розблоковано в боті UC Shop · Nezuko. Ласкаво просимо знову! 🌸"
        )
    except: pass


async def setprice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Тільки для адміна."); return
    if not context.args or len(context.args) < 2:
        packs_list = "\n".join([f"• {p}" for p in list(ALL_PACKS.keys())[:10]])
        await update.message.reply_text(f"Використання: /setprice <назва_паку> <ціна>\n\nПаки:\n{packs_list}\n..."); return
    price_str = context.args[-1]
    pack_name = " ".join(context.args[:-1])
    if pack_name not in ALL_PACKS:
        await update.message.reply_text(f"❌ Пак '{pack_name}' не знайдено."); return
    try:
        price = int(price_str)
        if price < 1: raise ValueError
    except:
        await update.message.reply_text("❌ Ціна повинна бути числом > 0"); return
    db_exec("INSERT OR REPLACE INTO price_overrides (pack_name, price, updated_at) VALUES (?,?,?)", (pack_name, price, created_at_now()))
    await update.message.reply_text(f"✅ Ціна оновлена:\n{pack_name} → {price} грн")

async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Тільки для адміна."); return
    if not context.args:
        await update.message.reply_text("Використання: /find <ID замовлення або ігровий ID або @юзернейм>"); return
    query = context.args[0].strip()
    query_upper = query.upper()
    query_lower = query.lower().lstrip("@")
    orders = (
        db_query("SELECT id, user, pack, status, chat_id, player_id, amount FROM orders WHERE id=?", (query_upper,)) or
        db_query("SELECT id, user, pack, status, chat_id, player_id, amount FROM orders WHERE player_id=?", (query,)) or
        db_query("SELECT id, user, pack, status, chat_id, player_id, amount FROM orders WHERE LOWER(user)=?", (query_lower,)) or
        db_query("SELECT id, user, pack, status, chat_id, player_id, amount FROM orders WHERE id LIKE ? OR player_id LIKE ? OR LOWER(user) LIKE ?",
                 (f"%{query_upper}%", f"%{query}%", f"%{query_lower}%"))
    )
    if not orders:
        await update.message.reply_text("📭 Замовлення не знайдено."); return
    lines = []
    for o in orders[:10]:
        lines.append(f"🆔 {o[0]} | {status_text(o[3])}\n👤 {user_label(o[1], o[4])}\n🎁 {o[2]}\n🎮 ID: {o[5]}\n💵 {o[6] or '?'} грн\n")
    header = f"🔎 Знайдено: {len(orders)} замовлень\n\n" if len(orders) > 1 else "🔎 Замовлення:\n\n"
    await update.message.reply_text(header + "\n".join(lines))

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Тільки для адміна."); return
    user_states[uid] = "WAIT_BROADCAST"
    await update.message.reply_text(
        "📣 Надішли повідомлення для розсилки.\n\n"
        "Підтримуються: текст, фото, відео, GIF, стікер, голосове, документ.\n"
        "Форматування Markdown/HTML — теж працює."
    )

def _collect_broadcast_uids():
    uids = set()
    for tbl, col in [
        ("user_profile", "user_id"), ("orders", "chat_id"),
        ("referrals", "referrer_id"), ("referrals", "referred_id"),
        ("user_bonuses", "user_id"), ("user_achievements", "user_id"),
        ("user_points", "user_id"),
    ]:
        try:
            for r in db_query(f"SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL"):
                if r[0]: uids.add(r[0])
        except: pass
    # Виключаємо заблокованих
    for r in db_query("SELECT user_id FROM banned_users"):
        uids.discard(r[0])
    return uids

async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, src_message):
    uids = _collect_broadcast_uids()
    total = len(uids)
    status_msg = await update.message.reply_text(f"📤 Розсилка почалась...\n👥 Всього одержувачів: {total}")

    sent = 0
    failed = 0
    from_chat = src_message.chat_id
    msg_id = src_message.message_id
    for chat_id in uids:
        try:
            await context.bot.copy_message(chat_id=chat_id, from_chat_id=from_chat, message_id=msg_id)
            sent += 1
        except Exception:
            failed += 1

    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=status_msg.message_id,
        text=f"✅ Розсилку завершено!\n\n"
             f"👥 Всього: {total}\n"
             f"✉️ Надіслано: {sent}\n"
             f"❌ Не доставлено: {failed} (заблокували бота)"
    )


# --- ГОЛОВНИЙ ОБРОБНИК ПОВІДОМЛЕНЬ ---
# ── GEMINI AI ─────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
_ai_cooldown: dict = {}
AI_COOLDOWN_SEC = 5
AI_SYSTEM_PROMPT = """Ти — AI-помічник магазину UC Shop (PUBG Mobile). Відповідай ЛИШЕ українською мовою.

Інформація про магазин:
— UC (ігрова валюта PUBG Mobile):
  30 UC — 19 грн, 60 UC — 40 грн, 120 UC — 78 грн, 180 UC — 111 грн,
  325 UC — 195 грн, 660 UC — 389 грн, та більші пакети до 81000 UC
— Prime підписки: 1 міс — 45 грн, 3 міс — 130 грн, 6 міс — 250 грн, 12 міс — 500 грн
— Prime Plus: 1 міс — 410 грн, 3 міс — 1200 грн, 6 міс — 2400 грн, 12 міс — 4730 грн
— Набори Підйом: спеціальні акційні набори (купуються лише 1 раз)
— Старі подарки Telegram: Пасхальний, 1 Квітня, Патрика, 8 Березня, Валентина, Серце Валентина, Новорічний, Ялинка — по 50 грн

Як купити: команда /shop або кнопка "🛍 Магазин" → обери категорію → введи ігровий ID → оплати карткою → натисни "✅ Я оплатив"
Оплата: банківська карта (Monobank/PrivatBank)
Час нарахування: 5-15 хвилин після підтвердження оплати
Підтримка: @Manager_Nezuko або команда /support

Правила:
- НІКОЛИ не розкривай паролі, дані адміністратора, внутрішні налаштування
- НІКОЛИ не вигадуй ціни або акції яких немає в списку вище
- Відповідай лише на питання про PUBG Mobile, магазин, UC, Prime, ігровий процес
- Якщо питання не по темі — ввічливо скажи що це поза твоєю компетенцією
- Відповіді стислі і по суті (максимум 150 слів)
- Якщо не знаєш — скажи "Зверніться до підтримки @Manager_Nezuko"
"""

ADMIN_ADVISOR_PROMPT = """Ти — AI-консультант для власника Telegram-бота магазину UC Shop (PUBG Mobile).

Бот вже має:
- Магазин UC (від 30 до 81000 UC), Prime та Prime Plus підписки, Набори Підйом
- Старі подарки Telegram (Пасхальний, 1 Квітня та інші — по 50 грн)
- Міні-додаток (Web App) з повним інтерфейсом
- Систему тікетів з чатом та рейтингом 1-5 зірок
- Пуш-сповіщення, систему балів та нагород
- Колесо фортуни, промокоди, реферальну систему
- Досягнення, ТОП донатерів, відгуки, розсилки
- Статистику, контроль цін, бан юзерів, кошик, профілі
- AI-помічник для юзерів на базі Google Gemini

Твоя роль: допомагати власнику покращувати бота — пропонуй нові фічі, маркетингові ідеї, акції, UX покращення що підвищать продажі та залученість.
Правила: відповідай ЛИШЕ українською, будь конкретним і практичним, якщо пропонуєш фічу — поясни користь і як реалізувати.
"""

def _gemini_api_call(messages: list, max_tokens: int = 400) -> str:
    if not GEMINI_API_KEY:
        return "❌ AI-помічник тимчасово недоступний."
    system_text = ""
    user_parts = []
    for m in messages:
        if m["role"] == "system":
            system_text = m["content"]
        elif m["role"] == "user":
            user_parts.append(m["content"])
        elif m["role"] == "assistant":
            pass
    combined_user = (system_text + "\n\n" if system_text else "") + "\n".join(user_parts)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": combined_user}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7}
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["candidates"][0]["content"]["parts"][0]["text"].strip()
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()[:300]
        except: pass
        logging.warning(f"Gemini HTTP {e.code}: {body}")
        if e.code == 429:
            return "⏳ AI зараз перевантажений — надто багато запитів. Спробуй через хвилину."
        return "❌ AI-помічник зараз недоступний. Зверніться до підтримки @Manager_Nezuko"
    except Exception as e:
        logging.warning(f"Gemini API error: {e}")
        return "❌ AI-помічник зараз недоступний. Зверніться до підтримки @Manager_Nezuko"

def _detect_ai_topic(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ['ціна', ' uc', 'купи', 'prime', 'скільки', 'коштує', 'вартість', 'пак']):
        return 'Ціни та покупки'
    if any(w in t for w in ['pubg', 'гра', 'режим', 'зброя', 'карта', 'battle', 'royal', 'персонаж']):
        return 'PUBG Mobile'
    if any(w in t for w in ['замовлен', 'оплат', 'карт', 'нарахув', 'статус', 'де мої']):
        return 'Замовлення та оплата'
    if any(w in t for w in ['тікет', 'підтримк', 'допомог', 'проблем']):
        return 'Підтримка'
    return 'Інше'

def _gemini_sync_call(user_message: str) -> str:
    messages = [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        {"role": "user", "content": user_message}
    ]
    return _gemini_api_call(messages)

async def openai_reply(user_message: str) -> str:
    try:
        return await asyncio.to_thread(_gemini_sync_call, user_message)
    except Exception as e:
        logging.warning(f"gemini_reply error: {e}")
        return "❌ Помилка AI. Зверніться до підтримки @Manager_Nezuko"

async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🤖 Я — AI-помічник магазину UC Shop!\n\n"
            "Запитай мене про PUBG Mobile, ціни UC, Prime, або як зробити замовлення.\n\n"
            "Наприклад: /ai Скільки коштує 325 UC?"
        )
        return
    question = " ".join(context.args)
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    response = await openai_reply(question)
    await update.message.reply_text(f"🤖 {response}")

async def aichat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    if not context.args:
        await update.message.reply_text(
            "🤖 *AI-радник для бота*\n\n"
            "Я допоможу з ідеями для розвитку магазину!\n\n"
            "Приклади:\n"
            "`/aichat Що варто додати до бота?`\n"
            "`/aichat Які акції можна провести?`\n"
            "`/aichat Як збільшити продажі?`",
            parse_mode="Markdown"
        )
        return
    question = " ".join(context.args)
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    msgs = [{"role": "system", "content": ADMIN_ADVISOR_PROMPT}, {"role": "user", "content": question}]
    response = await asyncio.to_thread(_gemini_api_call, msgs, 700)
    await update.message.reply_text(f"🤖 {response}")

# ──────────────────────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message: return
    if is_banned(uid) and not is_admin(uid):
        return
    if not _rl_allow(f"msg:{uid}", 20, 60):
        return
    if not update.message.text: return
    text = update.message.text
    state = user_states.get(uid)
    # Auto-logout expired admin sessions
    if state == "ADMIN_MODE" and is_admin(uid) and not _admin_session_valid(uid):
        _admin_logout(uid)
        user_states[uid] = None
        await update.message.reply_text("⏰ Сесія адміна закінчилась. Введіть /admin для повторного входу.",
                                        reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
        return
    if state == "ADMIN_MODE" and is_admin(uid):
        _admin_touch(uid)

    # ── Глобальні кнопки (завжди спрацьовують, незалежно від стану) ───────────

    if text == "📄 Політика":
        await policy_command(update, context); return

    if text == "🌸 Відкрити Mini App":
        webapp_url = get_miniapp_url()
        if webapp_url:
            await update.message.reply_text(
                "🌸 Натисни кнопку нижче щоб відкрити Mini App:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🌸 Відкрити Mini App", web_app=WebAppInfo(url=webapp_url))
                ]])
            )
        else:
            await update.message.reply_text("❌ Mini App тимчасово недоступний.")
        return

    # ── Пріоритетні стани ──────────────────────────────────────────────────────

    if state == "WAIT_REVIEW":
        user_states[uid] = None
        user_name = f"@{update.effective_user.username}" if update.effective_user.username else "Анонім"
        db_exec("INSERT INTO reviews (user, text) VALUES (?, ?)", (user_name, text))
        await update.message.reply_text("✅ Відгук збережено.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
        return

    if state == "WAIT_DONATE_CUSTOM":
        user_states[uid] = None
        try:
            amount = int(text.strip().replace(" ", "").replace(",", ""))
            if amount < 5 or amount > 50000:
                raise ValueError
        except (ValueError, TypeError):
            await update.message.reply_text("❌ Вкажіть коректну суму від 5 до 50000 грн.",
                                            reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
            return
        stars_amount = max(1, round(amount * 0.5))
        btns = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Карткою (UAH)", callback_data=f"donate_card_{amount}")],
            [InlineKeyboardButton(f"⭐ Telegram Stars ({stars_amount}⭐)", callback_data=f"donate_stars_{amount}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="donate_back")],
        ])
        await update.message.reply_text(
            f"💖 Підтримка на *{amount} грн*\n\nОбери спосіб оплати:",
            reply_markup=btns, parse_mode="Markdown"
        )
        return

    if state == "WAIT_BROADCAST" and is_admin(uid):
        user_states[uid] = "ADMIN_MODE"
        await send_broadcast(update, context, update.message)
        return

    if state == "WAIT_PROMO_CODE":
        user_states[uid] = None
        code = text.upper().strip().replace(" ", "")
        promo = db_query_one("SELECT bonus_type, bonus_value, uses_left, total_uses, secret, min_uc, max_uc FROM promo_codes WHERE code=?", (code,))
        if not promo:
            await update.message.reply_text("❌ Промокод не знайдено.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)); return
        already = db_query_one("SELECT 1 FROM used_promo_codes WHERE user_id=? AND code=?", (uid, code))
        if already:
            await update.message.reply_text("❌ Вже використано.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)); return
        bonus_type, bonus_value, uses_left, total_uses, is_secret, promo_min_uc, promo_max_uc = promo
        if uses_left is not None and uses_left != -1 and uses_left <= 0:
            await update.message.reply_text("❌ Промокод вичерпано.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)); return
        was_limited = uses_left is not None and uses_left != -1
        db_exec("INSERT OR IGNORE INTO used_promo_codes (user_id, code) VALUES (?,?)", (uid, code))
        if bonus_type.startswith("points_"):
            pts = int(bonus_type.split("_")[1])
            add_points(uid, pts, f"Промокод {code}")
        else:
            db_exec("INSERT INTO user_bonuses (user_id, bonus_type, bonus_value, min_uc, max_uc, used, created_at) VALUES (?,?,?,?,?,0,?)",
                    (uid, bonus_type, bonus_value or 1, promo_min_uc or 0, promo_max_uc or 0, created_at_now()))
        if uses_left is not None and uses_left != -1:
            new_uses = uses_left - 1
            if new_uses <= 0:
                db_exec("DELETE FROM promo_codes WHERE code=?", (code,))
                db_exec("DELETE FROM used_promo_codes WHERE code=?", (code,))
            else:
                db_exec("UPDATE promo_codes SET uses_left=? WHERE code=?", (new_uses, code))
        if is_secret:
            grant_achievement(uid, "secret_seeker")
        if was_limited:
            grant_achievement(uid, "precise")
        check_achievements(uid)
        if bonus_type.startswith("discount_custom_"):
            try: pct = int(bonus_type.split("_")[2])
            except: pct = 0
            bonus_name = f"💸 Знижка {pct}% ({promo_min_uc}–{promo_max_uc} UC)"
        elif bonus_type.startswith("points_"):
            bonus_name = f"🪙 {int(bonus_type.split('_')[1])} балів"
        else:
            bonus_name = BONUS_TYPES.get(bonus_type, bonus_type)
        kb = ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)
        await update.message.reply_text(f"✅ Промокод активовано!\n🎁 Бонус: {bonus_name}", reply_markup=kb)
        return

    if isinstance(state, dict) and state.get("step") == "WAIT_PROMO_CODE_NAME" and is_admin(uid):
        code = text.upper().strip().replace(" ", "")
        if not code:
            await update.message.reply_text("❌ Введіть назву промокоду:"); return
        user_states[uid] = {"step": "WAIT_PROMO_BONUS", "code": code}
        btns = [[InlineKeyboardButton(desc, callback_data=f"promo_bonus_{btype}")] for btype, desc in BONUS_TYPES.items()]
        await update.message.reply_text(f"🎁 Промокод: *{code}*\nОберіть тип бонусу:", reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")
        return

    if isinstance(state, dict) and state.get("step") == "WAIT_PROMO_USES" and is_admin(uid):
        cleaned = re.sub(r"[^0-9]", "", text)
        if not cleaned:
            await update.message.reply_text("❌ Введіть число:"); return
        uses = int(cleaned)
        if uses < 1 or uses > 1000000:
            await update.message.reply_text("❌ Число від 1 до 1000000:"); return
        code = state["code"]
        bonus_type = state["bonus_type"]
        db_exec("INSERT OR REPLACE INTO promo_codes (code, bonus_type, bonus_value, uses_left, total_uses, created_at) VALUES (?,?,?,?,?,?)",
                (code, bonus_type, 0, uses, uses, created_at_now()))
        db_exec("DELETE FROM used_promo_codes WHERE code=?", (code,))
        user_states[uid] = None
        bonus_name = BONUS_TYPES.get(bonus_type, bonus_type)
        await update.message.reply_text(f"✅ Промокод *{code}* створено!\n🎁 Бонус: {bonus_name}\n🔢 Активацій: {uses}", parse_mode="Markdown")
        return

    # ── Адмін кнопки ──────────────────────────────────────────────────────────

    if is_admin(uid):
        if "Замовлення" in text:
            orders = db_query("SELECT id, user, pack, player_id, chat_id FROM orders WHERE status='pending'")
            if not orders:
                await update.message.reply_text("📭 Немає замовлень."); return
            for o in orders:
                btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data=f"ok_{o[0]}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{o[0]}")]])
                await update.message.reply_text(f"📦 {o[2]}\n👤 {user_label(o[1], o[4])}\n🎮 ID: `{o[3]}`\n🆔 `{o[0]}`", reply_markup=btns)
            return
        if "Відгуки" in text:
            revs = db_query("SELECT rowid, user, text FROM reviews ORDER BY rowid DESC LIMIT 15")
            if not revs:
                await update.message.reply_text("🌟 Відгуків немає."); return
            await update.message.reply_text(f"🌟 ОСТАННІ ВІДГУКИ ({len(revs)}):")
            for r in revs:
                del_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Видалити", callback_data=f"delrev_{r[0]}")]])
                await update.message.reply_text(f"👤 {r[1]}:\n{r[2]}", reply_markup=del_btn)
            return
        if "Статистика" in text:
            done_count = db_query_one("SELECT COUNT(*) FROM orders WHERE status='done'")[0]
            canceled_count = db_query_one("SELECT COUNT(*) FROM orders WHERE status='canceled'")[0]
            total_sum = get_done_sum()
            today_sum = get_done_sum(today_only=True)
            total_users = db_query_one("SELECT COUNT(DISTINCT user_id) FROM user_profile")[0] or 0
            await update.message.reply_text(f"📊 Статистика:\n✅ Виконано: {done_count}\n❌ Відхилено: {canceled_count}\n💰 Загальна сума: {total_sum} грн\n📅 Сьогодні: {today_sum} грн\n👥 Всього користувачів: {total_users}")
            return
        if "Промокоди" in text:
            codes = db_query("SELECT code, bonus_type, uses_left, total_uses FROM promo_codes ORDER BY code")
            inline_btns = []
            msg = "🎁 ПРОМОКОДИ:\n\n"
            if codes:
                for code, btype, ul, tu in codes:
                    msg += f"• {code} — {BONUS_TYPES.get(btype, btype)}\n  🔢 {uses_left_label(ul, tu)}\n\n"
                    inline_btns.append([InlineKeyboardButton(f"🗑 Видалити {code}", callback_data=f"promo_del_{code}")])
            else:
                msg += "Активних промокодів немає.\n"
            inline_btns.append([InlineKeyboardButton("➕ Створити промокод", callback_data="promo_create")])
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(inline_btns))
            return
        if "Вийти" in text:
            user_states[uid] = None
            await update.message.reply_text("Головне меню", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
            return

    # ── Загальні кнопки ────────────────────────────────────────────────────────

    if text == "⚙️ Адмін":
        await admin_panel(update, context); return

    if state == "WAIT_PASS":
        ok, err_msg = _tg_admin_check(uid, text)
        username = update.effective_user.username or str(uid)
        if ok:
            otp = _generate_otp()
            _admin_otp[uid] = {"code": otp, "expires_at": time.time() + ADMIN_OTP_TTL}
            user_states[uid] = "WAIT_2FA"
            logging.warning(f"[SECURITY] Admin password OK, 2FA sent: uid={uid} @{username}")
            try:
                await context.bot.send_message(MY_ID, f"🔐 Спроба входу в адмін!\n👤 @{username} (ID: {uid})\n\n2FA код: <b>{otp}</b>\nДійсний 5 хвилин.", parse_mode="HTML")
            except Exception:
                pass
            await update.message.reply_text("✅ Пароль вірний!\n🔐 Введіть 2FA код який щойно прийшов власнику бота:", reply_markup=ReplyKeyboardRemove())
        else:
            user_states[uid] = None
            try:
                await context.bot.send_message(MY_ID, f"⚠️ Невдала спроба входу в адмін\n👤 @{username} (ID: {uid})\n{err_msg}")
            except Exception:
                pass
            await update.message.reply_text(err_msg, reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
        return

    if state == "WAIT_2FA":
        username = update.effective_user.username or str(uid)
        otp_data = _admin_otp.get(uid)
        if not otp_data or time.time() > otp_data["expires_at"]:
            _admin_otp.pop(uid, None)
            user_states[uid] = None
            await update.message.reply_text("⏰ 2FA код прострочений. Почніть заново /admin.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
            return
        if _hmac_mod.compare_digest(str(text).strip(), str(otp_data["code"])):
            _admin_otp.pop(uid, None)
            user_states[uid] = "ADMIN_MODE"
            _admin_touch(uid)
            log_admin_action(uid, "LOGIN", f"@{username}")
            logging.warning(f"[SECURITY] Admin 2FA passed, logged in: uid={uid} @{username}")
            try:
                await context.bot.send_message(MY_ID, f"✅ Вхід в адмін-панель підтверджено 2FA\n👤 @{username} (ID: {uid})")
            except Exception:
                pass
            await update.message.reply_text("✅ Доступ надано!", reply_markup=ReplyKeyboardMarkup(ADMIN_KB, resize_keyboard=True))
        else:
            user_states[uid] = None
            _admin_otp.pop(uid, None)
            logging.warning(f"[SECURITY] Wrong 2FA code: uid={uid} @{username}")
            try:
                await context.bot.send_message(MY_ID, f"❌ Невірний 2FA код\n👤 @{username} (ID: {uid})")
            except Exception:
                pass
            await update.message.reply_text("❌ Невірний код. Почніть заново /admin.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
        return

    if text == "🛍 Магазин":
        await update.message.reply_text("🛍 Оберіть категорію:", reply_markup=SHOP_KB); return

    if text == "🔙 Назад":
        await update.message.reply_text("Головне меню", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)); return

    if text == "⭐ Бали за зірки":
        msg = "⭐ *Купити бали за Telegram Stars*\n\nОбери пакет — зірки спишуться з твого балансу Telegram, а бали зарахуються одразу після оплати.\n\n"
        for p in STARS_PACKAGES:
            msg += f"• {p['label']}\n"
        buttons = [[InlineKeyboardButton(p["label"], callback_data=f"stars_buy_{p['id']}")] for p in STARS_PACKAGES]
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons)); return

    if text == "🏆 Топ донатерів":
        # Hidden letter O — uses stored amount column (same as mini app /api/top)
        rows = db_query(
            "SELECT user, chat_id, SUM(CAST(COALESCE(amount,0) AS INTEGER)) as total "
            "FROM orders WHERE status='done' GROUP BY chat_id ORDER BY total DESC LIMIT 10"
        )
        if not rows:
            await update.message.reply_text("🏆 Поки немає даних.\n· O ·"); return
        medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
        msg = "🏆 ТОП-10 ДОНАТЕРІВ:\n\n"
        for i, (user, cid, total) in enumerate(rows):
            name = user_label(user, cid)
            msg += f"{medals[i]} {name} — {total} грн\n"
        await update.message.reply_text(msg); return

    if text == "🏅 Досягнення":
        await achievements_command(update, context); return

    if text == "🎁 Промокод":
        user_states[uid] = "WAIT_PROMO_CODE"
        await update.message.reply_text("🎁 Введіть промокод:", reply_markup=ReplyKeyboardRemove()); return

    if text == "👥 Реферал":
        bot_username = context.bot.username
        ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"
        refs = db_query("SELECT referred_id FROM referrals WHERE referrer_id=?", (uid,))
        discounts = db_query("SELECT id FROM ref_discounts WHERE user_id=?", (uid,))
        # Hidden letter Z
        msg = (
            f"👥 РЕФЕРАЛЬНА СИСТЕМА\n\n"
            f"🔗 Ваше посилання:\n{ref_link}\n\n"
            f"👤 Запрошено людей: {len(refs)}\n"
            f"🎁 Знижок доступно (1%): {len(discounts)}\n\n"
            f"За кожного друга, що зробить покупку — знижка 1%!\n"
            f"· · · Z · · ·"
        )
        await update.message.reply_text(msg); return

    if text == "💸 Купити UC":
        await update.message.reply_text("Оберіть пакет:", reply_markup=ReplyKeyboardMarkup([[dynamic_label(p)] for p in PACKS.keys()], resize_keyboard=True)); return

    if text == "👑 Prime":
        await update.message.reply_text("Оберіть Prime:", reply_markup=ReplyKeyboardMarkup([[dynamic_label(p)] for p in PRIME_PACKS.keys()], resize_keyboard=True)); return

    if text == "👑 Prime Plus":
        await update.message.reply_text("Оберіть Prime Plus:", reply_markup=ReplyKeyboardMarkup([[dynamic_label(p)] for p in PRIME_PLUS_PACKS.keys()], resize_keyboard=True)); return

    if text == "⭐️ Набори Підйом":
        await update.message.reply_text(
            "⭐️ *Набори Підйом*\n\n"
            "⚠️ Кожний набір купується лише *1 раз*!\n"
            "Перед покупкою перевірте чи він у вас куплений.\n"
            "Після — пишіть нам чи можна його купити 💸\n\n"
            "Оберіть набір:",
            reply_markup=ReplyKeyboardMarkup([[dynamic_label(p)] for p in RISE_PACKS.keys()], resize_keyboard=True),
            parse_mode="Markdown"
        ); return

    if text == "🎁 Старі подарки Telegram":
        gift_price = get_pack_price(list(TG_GIFTS.keys())[0])
        msg = f"🎁 *Старі подарки Telegram*\n\n_Ці подарки більше не продаються в Telegram — колекційні!\nЦіна кожного: {gift_price} грн_\n\nОбери подарунок:"
        kb = [[g] for g in TG_GIFTS.keys()]
        kb.append(["🔙 Назад"])
        await update.message.reply_text(msg, reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True), parse_mode="Markdown")
        return

    if text == "💖 Підтримати бота":
        btns = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"💳 {a} грн", callback_data=f"donate_amount_{a}") for a in DONATE_AMOUNTS[:3]],
             [InlineKeyboardButton(f"💳 {a} грн", callback_data=f"donate_amount_{a}") for a in DONATE_AMOUNTS[3:]],
             [InlineKeyboardButton("✍️ Своя сума", callback_data="donate_custom")]]
        )
        await update.message.reply_text(
            "💖 *Підтримати бота*\n\nОбери суму підтримки (гривнями або зірками Telegram):",
            reply_markup=btns, parse_mode="Markdown"
        )
        return

    if text == "🆘 Підтримка":
        await support_command(update, context); return

    if text == "📋 Мої замовлення":
        await history_command(update, context); return

    if text == "📄 Політика":
        await policy_command(update, context); return

    if text in ("🎁 60 UC Free", "🎁 30 UC Free"):
        bt = "free_uc_60" if "60" in text else "free_uc_30"
        uc = 60 if "60" in text else 30
        bonus = db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type=? AND used=0 LIMIT 1", (uid, bt))
        if not bonus:
            await update.message.reply_text("❌ Цей бонус недоступний.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)); return
        user_states[uid] = {"step": "FREE_UC_ID", "bonus_id": bonus[0], "uc": uc, "bt": bt}
        await update.message.reply_text(f"🎮 Введіть ваш ігровий ID для нарахування {uc} UC:", reply_markup=ReplyKeyboardRemove()); return

    # ── Вибір пакету ───────────────────────────────────────────────────────────

    _matched_pack = label_to_pack_key(text)
    if _matched_pack:
        price = get_pack_price(_matched_pack)
        user_states[uid] = {"pack": _matched_pack, "step": "ID", "price": price}
        await update.message.reply_text(f"🎮 Введіть ваш ігровий ID:", reply_markup=ReplyKeyboardRemove()); return

    # ── Тікет: повідомлення від юзера ──────────────────────────────────────────

    if isinstance(state, dict) and state.get("step") == "TICKET_MSG":
        msg_text = text.strip()
        if len(msg_text) < 3:
            await update.message.reply_text("❌ Повідомлення занадто коротке."); return
        if len(msg_text) > 1000:
            await update.message.reply_text("❌ Повідомлення занадто довге (макс 1000 символів)."); return
        category = state.get("category", "Інше")
        uname = update.effective_user.username or ""
        open_count = db_query_one("SELECT COUNT(*) FROM tickets WHERE user_id=? AND status='open'", (uid,))
        if open_count and open_count[0] >= 5:
            await update.message.reply_text("❌ Забагато відкритих тікетів. Зачекайте відповіді адміна.",
                                             reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
            user_states[uid] = None; return
        cur = db_exec("INSERT INTO tickets (user_id, chat_id, username, category, message, status, created_at) VALUES (?,?,?,?,?,?,?)",
                      (uid, uid, uname, category, msg_text, "open", created_at_now()))
        tid = cur.lastrowid
        db_exec("INSERT INTO ticket_messages (ticket_id, sender, message, created_at) VALUES (?,?,?,?)",
                (tid, "user", msg_text, created_at_now()))
        _notify_admin_ticket(tid, uid, uname, category, msg_text)
        user_states[uid] = None
        await update.message.reply_text(
            f"✅ Тікет *#{tid}* створено!\n\nАдмін відповість найближчим часом. Ви отримаєте сповіщення тут 🌸",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)
        ); return

    if isinstance(state, dict) and state.get("step") == "TICKET_REPLY":
        if not is_admin(uid):
            user_states[uid] = None; return
        reply_text = text.strip()
        ticket_id = state.get("ticket_id")
        ticket_user_id = state.get("ticket_user_id")
        ticket = db_query_one("SELECT category FROM tickets WHERE id=?", (ticket_id,))
        if ticket:
            db_exec("UPDATE tickets SET admin_reply=?, status='answered', replied_at=? WHERE id=?",
                    (reply_text, created_at_now(), ticket_id))
            db_exec("INSERT INTO ticket_messages (ticket_id, sender, message, created_at) VALUES (?,?,?,?)",
                    (ticket_id, "admin", reply_text, created_at_now()))
            try:
                await context.bot.send_message(ticket_user_id,
                    f"🎫 Відповідь по тікету *#{ticket_id}* [{ticket[0]}]:\n\n{reply_text}",
                    parse_mode="Markdown")
            except Exception as e:
                logging.warning(f"ticket reply send error: {e}")
        user_states[uid] = None
        await update.message.reply_text(f"✅ Відповідь надіслано на тікет #{ticket_id}!"); return

    # ── Флоу замовлення ────────────────────────────────────────────────────────

    if isinstance(state, dict) and state.get("step") == "FREE_UC_ID":
        state["game_id"] = text
        state["step"] = "FREE_UC_NICK"
        await update.message.reply_text(f"🪪 Введіть ваш нік в PUBG Mobile (для перевірки ID):", reply_markup=ReplyKeyboardRemove()); return

    if isinstance(state, dict) and state.get("step") == "FREE_UC_NICK":
        game_id = state["game_id"]
        nick = text.strip()
        bonus_id = state["bonus_id"]
        uc = state.get("uc", 60)
        bt = state.get("bt", "free_uc_60")
        db_exec("UPDATE user_bonuses SET used=1 WHERE id=?", (bonus_id,))
        oid = str(uuid.uuid4()).replace("-", "")[:12].upper()
        db_exec("INSERT INTO orders (id, user, pack, status, chat_id, player_id, created_at, amount, payment, player_nick) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (oid, update.effective_user.username, f"🎁 {uc} UC Free (бонус)", "pending", uid, game_id, created_at_now(), 0, "bonus", nick or None))
        if MY_ID != 0:
            try:
                nick_line = f"\n🪪 Нік: {nick}" if nick else ""
                btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Надіслано", callback_data=f"ok_{oid}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{oid}")]])
                await context.bot.send_message(MY_ID, f"🎁 БЕЗКОШТОВНІ UC!\n🆔 {oid}\n👤 {user_label(update.effective_user.username, uid)}\n🎮 ID: {game_id}{nick_line}\n💵 {uc} UC (безкоштовно)", reply_markup=btns)
            except: pass
        user_states[uid] = None
        await update.message.reply_text(f"✅ Заявку прийнято! {uc} UC буде нараховано.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
        return

    if isinstance(state, dict) and state.get("step") == "ID":
        state["pid"] = text
        state["step"] = "NICK"
        await update.message.reply_text(f"🪪 Введіть ваш нік в PUBG Mobile (для перевірки ID):"); return

    if isinstance(state, dict) and state.get("step") == "NICK":
        state["nick"] = text.strip()
        state["step"] = "OK"
        await update.message.reply_text(
            f"📝 {state['pack']}\n🎮 ID: {state['pid']}\n🪪 Нік: {state['nick']}\nНапишіть 'ОК' для підтвердження."
        ); return

    if isinstance(state, dict) and state.get("step") == "OK" and text.upper() in ["ОК", "OK"]:
        pack = state["pack"]
        pid = state["pid"]
        nick = state.get("nick", "")
        price = get_pack_price(pack)
        disc_pct, disc_src, disc_id = get_user_discount(uid, pack)
        final_price = apply_discount(price, disc_pct) if disc_pct else price

        oid = str(uuid.uuid4()).replace("-", "")[:12].upper()
        db_exec("INSERT INTO orders (id, user, pack, status, chat_id, player_id, created_at, amount, payment, player_nick) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (oid, update.effective_user.username, pack, "pending", uid, pid, created_at_now(), final_price, "card", nick or None))

        if disc_pct and disc_src == "promo":
            db_exec("UPDATE user_bonuses SET used=1 WHERE id=?", (disc_id,))
        elif disc_pct and disc_src == "ref":
            db_exec("DELETE FROM ref_discounts WHERE id=?", (disc_id,))

        update_user_profile(uid)

        nick_line = f"\n🪪 Нік: *{nick}*" if nick else ""
        price_text = f"💵 Сума: *{final_price} грн*"
        if disc_pct:
            price_text += f" _(знижка {disc_pct}%, було {price} грн)_"

        if MY_ID != 0:
            try:
                btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data=f"ok_{oid}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{oid}")]])
                await context.bot.send_message(MY_ID,
                    f"💰 ОПЛАТА (Bot)!\n🆔 {oid}\n👤 {user_label(update.effective_user.username, uid)}\n🎁 {pack}\n🎮 ID: {pid}{nick_line.replace('*','')}\n💵 Сума: {final_price} грн",
                    reply_markup=btns)
            except: pass

        btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Я оплатив", callback_data=f"paid_{oid}")]])
        await update.message.reply_text(
            f"💳 Карта: `{PAYMENT_CARD}`\n{price_text}",
            reply_markup=btn, parse_mode="Markdown"
        )
        user_states[uid] = None
        return

    # ── AI-помічник (Gemini) — відповідає на всі нерозпізнані повідомлення ─────
    if GEMINI_API_KEY and not is_admin(uid):
        now = time.time()
        last = _ai_cooldown.get(uid, 0)
        if now - last < AI_COOLDOWN_SEC:
            wait = int(AI_COOLDOWN_SEC - (now - last)) + 1
            await update.message.reply_text(f"⏳ Зачекай {wait} сек перед наступним запитом до AI.")
            return
        _ai_cooldown[uid] = now
        try:
            await context.bot.send_chat_action(update.effective_chat.id, "typing")
            response = await openai_reply(text)
            topic = _detect_ai_topic(text)
            db_exec("INSERT INTO ai_logs (user_id, message, topic, created_at) VALUES (?,?,?,?)",
                    (uid, text[:200], topic, created_at_now()))
            await update.message.reply_text(
                f"🤖 {response}",
                reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)
            )
        except Exception as e:
            logging.warning(f"AI reply send error: {e}")


# --- CALLBACK ОБРОБНИК ---
async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "leave_review":
        user_states[q.from_user.id] = "WAIT_REVIEW"
        await context.bot.send_message(q.from_user.id, "✍️ Напишіть ваш відгук:")
        return

    # ── Донат: вибір суми ────────────────────────────────────────────────────
    if data.startswith("donate_amount_") or data == "donate_custom":
        if data == "donate_custom":
            user_states[q.from_user.id] = "WAIT_DONATE_CUSTOM"
            await q.edit_message_text("✍️ Введіть суму підтримки в гривнях (наприклад: 150):")
            return
        amount = int(data.split("_")[-1])
        stars_amount = max(1, round(amount * 0.5))
        btns = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💳 Карткою (UAH)", callback_data=f"donate_card_{amount}")],
            [InlineKeyboardButton(f"⭐ Telegram Stars ({stars_amount}⭐)", callback_data=f"donate_stars_{amount}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="donate_back")],
        ])
        await q.edit_message_text(
            f"💖 Підтримка на *{amount} грн*\n\nОбери спосіб оплати:",
            reply_markup=btns, parse_mode="Markdown"
        )
        return

    if data == "donate_back":
        btns = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"💳 {a} грн", callback_data=f"donate_amount_{a}") for a in DONATE_AMOUNTS[:3]],
             [InlineKeyboardButton(f"💳 {a} грн", callback_data=f"donate_amount_{a}") for a in DONATE_AMOUNTS[3:]],
             [InlineKeyboardButton("✍️ Своя сума", callback_data="donate_custom")]]
        )
        await q.edit_message_text(
            "💖 *Підтримати бота*\n\nОбери суму підтримки (гривнями або зірками Telegram):",
            reply_markup=btns, parse_mode="Markdown"
        )
        return

    if data.startswith("donate_card_"):
        amount = int(data.split("_")[-1])
        don_uid = q.from_user.id
        don_id = db_exec("INSERT INTO donations (user_id, username, amount, method, status, created_at) VALUES (?,?,?,?,?,?)",
                         (don_uid, q.from_user.username or "", amount, "card", "pending", created_at_now())).lastrowid
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Я переказав", callback_data=f"donate_confirmed_{don_id}")]])
        await q.edit_message_text(
            f"💳 *Переказ на карту*\n\n"
            f"Карта: `{PAYMENT_CARD}`\n"
            f"Сума: *{amount} грн*\n\n"
            f"Після переказу натисни кнопку нижче 👇",
            reply_markup=btn, parse_mode="Markdown"
        )
        return

    if data.startswith("donate_stars_"):
        amount = int(data.split("_")[-1])
        stars_amount = max(1, round(amount * 0.5))
        don_uid = q.from_user.id
        don_id = db_exec("INSERT INTO donations (user_id, username, amount, method, status, created_at) VALUES (?,?,?,?,?,?)",
                         (don_uid, q.from_user.username or "", amount, "stars", "pending", created_at_now())).lastrowid
        try:
            await context.bot.send_invoice(
                chat_id=don_uid,
                title="💖 Підтримка бота",
                description=f"Підтримка Nezuko UC Shop на {stars_amount}⭐",
                payload=f"donate_stars_{don_id}_{don_uid}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(f"Підтримка ({amount} грн)", stars_amount)],
            )
            await q.edit_message_text("⭐ Інвойс надіслано! Оплатіть зірками у повідомленні вище.")
        except Exception as e:
            await q.edit_message_text(f"❌ Помилка створення інвойсу: {e}")
        return

    if data.startswith("donate_confirmed_"):
        don_id = int(data.split("_")[-1])
        don = db_query_one("SELECT user_id, username, amount, method FROM donations WHERE id=?", (don_id,))
        if not don:
            await q.answer("Донат не знайдено."); return
        db_exec("UPDATE donations SET status='unverified' WHERE id=?", (don_id,))
        btns = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Підтвердити", callback_data=f"donate_ok_{don_id}"),
            InlineKeyboardButton("❌ Відхилити", callback_data=f"donate_no_{don_id}")
        ]])
        try:
            await context.bot.send_message(MY_ID,
                f"💖 ДОНАТ!\n👤 {user_label(don[1], don[0])}\n💵 {don[2]} грн\n💳 {don[3]}\n🆔 #{don_id}",
                reply_markup=btns)
        except: pass
        await q.edit_message_text("🙏 Дякуємо! Адмін перевірить переказ і підтвердить незабаром.")
        return

    if data.startswith("donate_ok_"):
        if not is_admin(q.from_user.id): return
        don_id = int(data.split("_")[-1])
        don = db_query_one("SELECT user_id, username, amount FROM donations WHERE id=?", (don_id,))
        if don:
            db_exec("UPDATE donations SET status='done' WHERE id=?", (don_id,))
            log_admin_action(q.from_user.id, "DONATE_OK", f"don_id={don_id} amount={don[2]} user={don[0]}")
            _send_tg_message(don[0], f"💖 Дякуємо за підтримку на {don[2]} грн! Ти справжній герой 🌸")
        await q.edit_message_text(f"✅ Донат #{don_id} підтверджено.")
        return

    if data.startswith("donate_no_"):
        if not is_admin(q.from_user.id): return
        don_id = int(data.split("_")[-1])
        don = db_query_one("SELECT user_id, amount FROM donations WHERE id=?", (don_id,))
        if don:
            db_exec("UPDATE donations SET status='canceled' WHERE id=?", (don_id,))
            _send_tg_message(don[0], f"❌ Ваш донат ({don[1]} грн) не підтверджено. Зверніться в підтримку.")
        await q.edit_message_text(f"❌ Донат #{don_id} відхилено.")
        return

    if data.startswith("delrev_"):
        if is_admin(q.from_user.id):
            db_exec("DELETE FROM reviews WHERE rowid=?", (data[7:],))
            await q.edit_message_text("🗑 Відгук видалено.")
        return

    if data.startswith("stars_buy_"):
        pkg_id = data[len("stars_buy_"):]
        pkg = next((p for p in STARS_PACKAGES if p["id"] == pkg_id), None)
        if not pkg:
            await q.answer("❌ Пакет не знайдено", show_alert=True); return
        try:
            await context.bot.send_invoice(
                chat_id=q.from_user.id,
                title=f"⭐ {pkg['points']} балів",
                description=pkg["label"],
                payload=f"stars_points_{pkg_id}_{q.from_user.id}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(pkg["label"], pkg["stars"])],
            )
        except Exception as e:
            logging.warning(f"stars_buy send_invoice error: {e}")
            await context.bot.send_message(
                q.from_user.id,
                f"❌ Не вдалося створити інвойс: {e}\n\nПереконайтесь, що у бота увімкнені платежі Telegram Stars у @BotFather → Payments."
            )
        return

    if data.startswith("ok_"):
        if not is_admin(q.from_user.id): return
        _admin_touch(q.from_user.id)
        order_id = data[3:]
        res = db_query_one("SELECT chat_id, pack FROM orders WHERE id=?", (order_id,))
        if not res:
            await q.edit_message_text("❌ Замовлення не знайдено."); return
        chat_id, pack = res
        db_exec("UPDATE orders SET status='done', completed_at=? WHERE id=?", (created_at_now(), order_id))
        log_admin_action(q.from_user.id, "ORDER_DONE", f"order={order_id} pack={pack} user={chat_id}")
        ref = db_query_one("SELECT referrer_id FROM referrals WHERE referred_id=?", (chat_id,))
        if ref:
            db_exec("INSERT INTO ref_discounts (user_id, created_at) VALUES (?,?)", (ref[0], created_at_now()))
            try: await context.bot.send_message(ref[0], "🎉 Ваш реферал зробив покупку! Ви отримали знижку 1%.")
            except: pass
        if "Мікс UC" in pack:
            done_msg = f"✅ Мікс UC нараховано! 🎉\n💎 {pack}\nУсі UC вже у грі. Дякуємо! 🌸"
        else:
            done_msg = f"✅ {pack} нараховано! Дякуємо 🌸"
        try: await context.bot.send_message(chat_id, done_msg)
        except: pass
        check_achievements(chat_id)
        await q.edit_message_text(f"✅ Замовлення {order_id} виконано.")
        return

    if data.startswith("no_"):
        if not is_admin(q.from_user.id): return
        _admin_touch(q.from_user.id)
        order_id = data[3:]
        res = db_query_one("SELECT chat_id, pack FROM orders WHERE id=?", (order_id,))
        if not res:
            await q.edit_message_text("❌ Замовлення не знайдено."); return
        chat_id, pack = res
        db_exec("UPDATE orders SET status='canceled' WHERE id=?", (order_id,))
        log_admin_action(q.from_user.id, "ORDER_CANCELED", f"order={order_id} pack={pack} user={chat_id}")
        try: await context.bot.send_message(chat_id, f"❌ Замовлення ({pack}) відхилено. Зверніться в підтримку.")
        except: pass
        await q.edit_message_text(f"❌ Замовлення {order_id} відхилено.")
        return

    if data.startswith("wspin_"):
        if not is_admin(q.from_user.id): return
        spin_id = int(data[6:])
        spin = db_query_one("SELECT user_id, username, status FROM pending_wheel_spins WHERE id=?", (spin_id,))
        if not spin:
            await q.edit_message_text("❌ Запит не знайдено."); return
        if spin[2] != "pending":
            await q.edit_message_text("⚠️ Цей запит вже оброблено."); return
        prize = spin_wheel_random(PAID_WHEEL_PRIZES)
        db_exec("UPDATE pending_wheel_spins SET status='done', prize_id=? WHERE id=?", (prize["id"], spin_id))
        deliver_wheel_prize(spin[0], spin[1], prize)
        check_achievements(spin[0])
        await q.edit_message_text(f"✅ Колесо #/{spin_id} крутнуто!\n🎁 Приз: {prize['name']}\n👤 {'@'+spin[1] if spin[1] else str(spin[0])}")
        return

    if data.startswith("wdeny_"):
        if not is_admin(q.from_user.id): return
        spin_id = int(data[6:])
        spin = db_query_one("SELECT user_id, username, status FROM pending_wheel_spins WHERE id=?", (spin_id,))
        if not spin:
            await q.edit_message_text("❌ Запит не знайдено."); return
        if spin[2] != "pending":
            await q.edit_message_text("⚠️ Цей запит вже оброблено."); return
        db_exec("UPDATE pending_wheel_spins SET status='denied' WHERE id=?", (spin_id,))
        try: await context.bot.send_message(spin[0], "❌ Ваш запит на платне колесо відхилено. Зверніться в підтримку.")
        except: pass
        await q.edit_message_text(f"❌ Запит #{spin_id} відхилено.\n👤 {'@'+spin[1] if spin[1] else str(spin[0])}")
        return

    if data.startswith("paid_"):
        order_id = data[5:]
        pay_uid = q.from_user.id
        res = db_query_one("SELECT pack, player_id, amount, status FROM orders WHERE id=?", (order_id,))
        if not res:
            await q.answer("Замовлення не знайдено."); return
        pack, player_id, amount, status = res
        if status != "pending":
            await q.answer("Це замовлення вже оброблено."); return
        if _check_fake_pay(pay_uid):
            db_exec("INSERT OR IGNORE INTO banned_users (user_id, reason, banned_at) VALUES (?,?,?)",
                    (pay_uid, "Авто-бан: підозра у фейкових оплатах", created_at_now()))
            logging.warning(f"[SECURITY] Auto-banned for fake payments: uid={pay_uid}")
            try:
                await context.bot.send_message(MY_ID, f"🚫 Авто-бан за фейкові оплати\n👤 {user_label(q.from_user.username, pay_uid)}")
            except: pass
            await q.answer("⛔ Ваш акаунт заблоковано."); return
        if _check_suspicious_player_id(player_id, pay_uid):
            try:
                await context.bot.send_message(MY_ID, f"🕵️ Підозрілий PUBG ID!\n🎮 ID: {player_id}\n👤 {user_label(q.from_user.username, pay_uid)}\nЦей ID вже використовувався з інших акаунтів!")
            except: pass
        try:
            btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data=f"ok_{order_id}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{order_id}")]])
            rise_marker = "⭐️ НАБІР ПІДЙОМ\n" if "Набір Підйом" in pack else ""
            await context.bot.send_message(MY_ID, f"💰 ОПЛАТА!\n{rise_marker}🆔 {order_id}\n👤 {user_label(q.from_user.username, pay_uid)}\n🎁 {pack}\n🎮 ID: {player_id}\n💵 {amount} грн", reply_markup=btns)
        except: pass
        await q.edit_message_text(f"✅ Дякуємо! Замовлення прийнято.\n🆔 {order_id}\nАдмін підтвердить незабаром.")
        return

    if data == "promo_create":
        if is_admin(q.from_user.id):
            user_states[q.from_user.id] = {"step": "WAIT_PROMO_CODE_NAME"}
            await context.bot.send_message(q.from_user.id, "🎁 Введіть назву промокоду:")
        return

    if data.startswith("tkt_cat_"):
        category = data[8:]
        user_states[q.from_user.id] = {"step": "TICKET_MSG", "category": category}
        await context.bot.send_message(
            q.from_user.id,
            f"✍️ Ви обрали: *{category}*\n\nНапишіть ваше повідомлення (до 1000 символів):",
            parse_mode="Markdown"
        )
        return

    if data.startswith("tkt_reply_"):
        if not is_admin(q.from_user.id): return
        ticket_id = int(data[10:])
        ticket = db_query_one("SELECT user_id, username, category, message FROM tickets WHERE id=?", (ticket_id,))
        if not ticket:
            await q.answer("Тікет не знайдено."); return
        user_label_str = f"@{ticket[1]}" if ticket[1] else str(ticket[0])
        user_states[q.from_user.id] = {"step": "TICKET_REPLY", "ticket_id": ticket_id, "ticket_user_id": ticket[0]}
        await context.bot.send_message(
            q.from_user.id,
            f"✏️ Відповідь на тікет *#{ticket_id}*\n📂 {ticket[2]}\n👤 {user_label_str}\n\n💬 _{ticket[3]}_\n\nВведіть вашу відповідь:",
            parse_mode="Markdown"
        )
        return

    if data.startswith("promo_del_"):
        if is_admin(q.from_user.id):
            code = data[10:]
            db_exec("DELETE FROM promo_codes WHERE code=?", (code,))
            db_exec("DELETE FROM used_promo_codes WHERE code=?", (code,))
            await q.edit_message_text(f"🗑 Промокод {code} видалено.")
        return

    if data.startswith("promo_bonus_"):
        uid = q.from_user.id
        if not is_admin(uid): return
        bonus_type = data[12:]
        state = user_states.get(uid)
        if not isinstance(state, dict) or state.get("step") != "WAIT_PROMO_BONUS": return
        state["bonus_type"] = bonus_type
        state["step"] = "WAIT_PROMO_USES"
        bonus_name = BONUS_TYPES.get(bonus_type, bonus_type)
        await context.bot.send_message(uid, f"✅ Бонус: {bonus_name}\nВведіть кількість активацій (-1 = безліміт):")
        return


async def _send_db_to_owner(context: ContextTypes.DEFAULT_TYPE):
    try:
        tmp = DB_PATH + ".send_tmp"
        with db_lock:
            src = sqlite3.connect(DB_PATH)
            dst = sqlite3.connect(tmp)
            src.backup(dst)
            dst.close()
            src.close()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(tmp, "rb") as f:
            await context.bot.send_document(
                chat_id=MY_ID,
                document=f,
                filename=f"bot_{now}.db",
                caption=f"🗄 Автобекап БД • {now}"
            )
        try: os.remove(tmp)
        except: pass
    except Exception as e:
        logging.warning(f"send_db_to_owner error: {e}")


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload  # stars_points_{pkg_id}_{user_id}
    uid = update.effective_user.id
    try:
        parts = payload.split("_")
        pkg_id = "_".join(parts[2:4])  # e.g. stars_50
        pkg = next((p for p in STARS_PACKAGES if p["id"] == pkg_id), None)
        if not pkg:
            logging.warning(f"Unknown stars package in payload: {payload}")
            return
        points = pkg["points"]
        db_exec("INSERT OR IGNORE INTO user_points (user_id, points) VALUES (?,0)", (uid,))
        db_exec("UPDATE user_points SET points=points+? WHERE user_id=?", (points, uid))
        db_exec("INSERT INTO user_points_tx (user_id, delta, reason, created_at) VALUES (?,?,?,?)",
                (uid, points, f"Купівля за зірки: {pkg['label']}", created_at_now()))
        uname = update.effective_user.username or str(uid)
        logging.info(f"Stars payment: {uname} bought {points} points ({pkg['stars']}⭐)")
        _send_tg_message(MY_ID, f"⭐ Оплата зірками!\n👤 @{uname} (ID: {uid})\n🪙 +{points} балів\n💫 {pkg['stars']} Stars")
        await update.message.reply_text(
            f"✅ Оплата успішна!\n\n🪙 На ваш рахунок нараховано *{points} балів*.\n\n"
            f"Дякуємо за підтримку! 🌸",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"successful_payment_handler error: {e}")
        await update.message.reply_text("✅ Оплата отримана! Бали буде нараховано найближчим часом.")


# --- MAIN ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("ai", ai_command))
    app.add_handler(CommandHandler("aichat", aichat_command))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("shop", shop_command))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CommandHandler("support", support_command))
    app.add_handler(CommandHandler("ticket", support_command))
    app.add_handler(CommandHandler("policy", policy_command))
    app.add_handler(CommandHandler("reviews", reviews_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("mypromos", mypromos_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("find", find_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("achievements", achievements_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("setprice", setprice_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("importdb", importdb_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("restartbot", restartbot_command))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.Document.ALL |
         filters.Sticker.ALL | filters.VOICE | filters.ANIMATION) & ~filters.COMMAND,
        handle_broadcast_media
    ))
    app.add_handler(CallbackQueryHandler(callback))
    if app.job_queue:
        app.job_queue.run_repeating(_send_db_to_owner, interval=3600, first=3600)
    logging.info("Бот запущено!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    import time as _time
    import asyncio as _asyncio
    start_policy_server()
    start_db_backup()
    if os.environ.get("DISABLE_BOT") == "1":
        logging.info("DISABLE_BOT=1 — бот вимкнено. Веб-сервер працює на порту 5000.")
        while True:
            _time.sleep(3600)
    _conflict_count = 0
    while True:
        try:
            _conflict_count = 0
            main()
        except Exception as _e:
            _err_str = str(_e).lower()
            if "conflict" in _err_str or "terminated by other" in _err_str:
                _conflict_count += 1
                _wait = min(30 * _conflict_count, 120)
                logging.warning(
                    f"⚠️ Конфлікт: інший екземпляр бота вже запущений! "
                    f"Очікування {_wait} сек перед перезапуском (спроба {_conflict_count})..."
                )
                _time.sleep(_wait)
            else:
                logging.warning(f"Бот зупинився ({_e}), перезапуск через 15 сек...")
                _time.sleep(15)
        finally:
            try:
                loop = _asyncio.get_event_loop()
                if loop.is_closed():
                    _asyncio.set_event_loop(_asyncio.new_event_loop())
            except Exception:
                _asyncio.set_event_loop(_asyncio.new_event_loop())
