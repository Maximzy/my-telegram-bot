import sqlite3, uuid, logging, threading, os, re, json, urllib.request, urllib.parse, random
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- НАЛАШТУВАННЯ ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
# Strip accidental "TELEGRAM_BOT_TOKEN " prefix if user pasted the var name too
if TOKEN.upper().startswith("TELEGRAM_BOT_TOKEN"):
    TOKEN = TOKEN[len("TELEGRAM_BOT_TOKEN"):].strip()
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set!")
ADMIN_PASSWORD = "NezukoAdmin"
PAYMENT_CARD = "4874070020367247"
MY_ID = 1440236609

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
    for _col in ["created_at", "completed_at", "amount", "payment"]:
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
    c.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY, reason TEXT, banned_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS cart (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, pack TEXT, added_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, chat_id INTEGER, username TEXT, category TEXT, message TEXT, status TEXT DEFAULT 'open', admin_reply TEXT, created_at TEXT, replied_at TEXT)")
    connection.commit()

run_migrations(conn)

logging.info(f"База даних: {DB_PATH}")

# --- ТОВАРИ ---
PACKS = {
    "30 UC - 19 грн": 19, "60 UC - 40 грн": 40, "120 UC - 78 грн": 78,
    "180 UC - 113 грн": 111, "325 UC - 195 грн": 195, "660 UC - 389 грн": 389,
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
ALL_PACKS = {**PACKS, **PRIME_PACKS, **PRIME_PLUS_PACKS, **RISE_PACKS}
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
    ["📋 Мої замовлення", "📄 Політика"],
    ["🆘 Підтримка"],
    ["⚙️ Адмін"]
]

def get_main_kb(uid):
    kb = list(MAIN_KB)
    extras = []
    if db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type='free_uc_60' AND used=0 LIMIT 1", (uid,)):
        extras.append("🎁 60 UC Free")
    if db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type='free_uc_30' AND used=0 LIMIT 1", (uid,)):
        extras.append("🎁 30 UC Free")
    if extras:
        kb = [extras] + kb
    return kb

SHOP_KB = ReplyKeyboardMarkup(
    [["💸 Купити UC"], ["👑 Prime", "👑 Prime Plus"], ["⭐️ Набори Підйом"], ["🔙 Назад"]],
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


def _send_tg_message(chat_id, text):
    try:
        params = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=params)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logging.warning(f"_send_tg_message failed: {e}")

def _notify_admin_order(order_id, pack, player_id, amount, user_id, username, mix_packs_list=None):
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
        text = (f"💰 ОПЛАТА (Mini App)!\n{rise_marker}🆔 {order_id}\n👤 {user_label_str}\n{pack_info}\n🎮 ID: {player_id}\n💵 Сума: {amount} грн")
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

def is_banned(uid):
    return bool(db_query_one("SELECT user_id FROM banned_users WHERE user_id=?", (uid,)))

def get_pack_price(pack):
    override = db_query_one("SELECT price FROM price_overrides WHERE pack_name=?", (pack,))
    if override:
        return override[0]
    if pack in ALL_PACKS: return ALL_PACKS[pack]
    m = re.search(r"(\d+)\s*грн", pack)
    return int(m.group(1)) if m else 0

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
    # Hidden letter N in the welcome message
    await update.message.reply_text(
        "👋 Вітаємо у магазині UC від Nezuko! 🌸\n\n"
        "Обери дію нижче:\n\n"
        "🔤 N · · · · · · · · · · · · ·",
        reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)
    )

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


def reconnect_db(new_path: str):
    global conn
    with db_lock:
        try:
            conn.close()
        except Exception:
            pass
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


async def handle_broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Хендлер для медіа-повідомлень під час очікування розсилки (фото, відео, стікер тощо)."""
    uid = update.effective_user.id
    if not is_admin(uid): return
    if is_banned(uid): return

    if user_states.get(uid) == "WAIT_DB_FILE" and uid == MY_ID:
        user_states[uid] = None
        doc = update.message.document
        if not doc:
            await update.message.reply_text("❌ Надішліть файл бази даних"); return
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
                await update.message.reply_text("❌ Файл не є базою SQLite. Операцію скасовано."); return
            # Backup current db
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            backup_path = os.path.join(os.path.dirname(DB_PATH), f"backup_before_import_{ts}.db")
            with db_lock:
                src = sqlite3.connect(DB_PATH)
                dst = sqlite3.connect(backup_path)
                src.backup(dst)
                dst.close()
                src.close()
            # Replace db file
            shutil.copy2(tmp.name, DB_PATH)
            os.remove(tmp.name)
            # Reconnect
            reconnect_db(DB_PATH)
            await update.message.reply_text(
                f"✅ База даних успішно замінена!\n\n"
                f"📦 Резервна копія збережена:\n`backup_before_import_{ts}.db`\n\n"
                f"🔄 Бот переключився на нову базу.",
                parse_mode="Markdown"
            )
            logging.info(f"DB imported by owner {uid}, backup: {backup_path}")
        except Exception as e:
            await update.message.reply_text(f"❌ Помилка імпорту: {e}")
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
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message: return
    # Перевірка бану — заблоковані мовчки ігноруються
    if is_banned(uid) and not is_admin(uid):
        return
    if not update.message.text: return
    text = update.message.text
    state = user_states.get(uid)

    # ── Глобальні кнопки (завжди спрацьовують, незалежно від стану) ───────────

    if text == "📄 Політика":
        await policy_command(update, context); return

    # ── Пріоритетні стани ──────────────────────────────────────────────────────

    if state == "WAIT_REVIEW":
        user_states[uid] = None
        user_name = f"@{update.effective_user.username}" if update.effective_user.username else "Анонім"
        db_exec("INSERT INTO reviews (user, text) VALUES (?, ?)", (user_name, text))
        await update.message.reply_text("✅ Відгук збережено.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
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
        if text == ADMIN_PASSWORD:
            db_exec("INSERT OR IGNORE INTO admins VALUES (?)", (uid,))
            user_states[uid] = "ADMIN_MODE"
            await update.message.reply_text("✅ Доступ надано!", reply_markup=ReplyKeyboardMarkup(ADMIN_KB, resize_keyboard=True))
        else:
            user_states[uid] = None
            await update.message.reply_text("❌ Невірно", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
        return

    if text == "🛍 Магазин":
        await update.message.reply_text("🛍 Оберіть категорію:", reply_markup=SHOP_KB); return

    if text == "🔙 Назад":
        await update.message.reply_text("Головне меню", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)); return

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
        await update.message.reply_text("Оберіть пакет:", reply_markup=ReplyKeyboardMarkup([[p] for p in PACKS.keys()], resize_keyboard=True)); return

    if text == "👑 Prime":
        await update.message.reply_text("Оберіть Prime:", reply_markup=ReplyKeyboardMarkup([[p] for p in PRIME_PACKS.keys()], resize_keyboard=True)); return

    if text == "👑 Prime Plus":
        await update.message.reply_text("Оберіть Prime Plus:", reply_markup=ReplyKeyboardMarkup([[p] for p in PRIME_PLUS_PACKS.keys()], resize_keyboard=True)); return

    if text == "⭐️ Набори Підйом":
        await update.message.reply_text(
            "⭐️ *Набори Підйом*\n\n"
            "⚠️ Кожний набір купується лише *1 раз*!\n"
            "Перед покупкою перевірте чи він у вас куплений.\n"
            "Після — пишіть нам чи можна його купити 💸\n\n"
            "Оберіть набір:",
            reply_markup=ReplyKeyboardMarkup([[p] for p in RISE_PACKS.keys()], resize_keyboard=True),
            parse_mode="Markdown"
        ); return

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

    if text in ALL_PACKS:
        price = get_pack_price(text)
        user_states[uid] = {"pack": text, "step": "ID", "price": price}
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
        game_id = text
        bonus_id = state["bonus_id"]
        uc = state.get("uc", 60)
        bt = state.get("bt", "free_uc_60")
        db_exec("UPDATE user_bonuses SET used=1 WHERE id=?", (bonus_id,))
        oid = str(uuid.uuid4())[:8]
        db_exec("INSERT INTO orders (id, user, pack, status, chat_id, player_id, created_at, amount, payment) VALUES (?,?,?,?,?,?,?,?,?)",
                (oid, update.effective_user.username, f"🎁 {uc} UC Free (бонус)", "pending", uid, game_id, created_at_now(), 0, "bonus"))
        if MY_ID != 0:
            try:
                btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Надіслано", callback_data=f"ok_{oid}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{oid}")]])
                await context.bot.send_message(MY_ID, f"🎁 БЕЗКОШТОВНІ UC!\n🆔 {oid}\n👤 {user_label(update.effective_user.username, uid)}\n🎮 ID: {game_id}\n💵 {uc} UC (безкоштовно)", reply_markup=btns)
            except: pass
        user_states[uid] = None
        await update.message.reply_text(f"✅ Заявку прийнято! {uc} UC буде нараховано.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
        return

    if isinstance(state, dict) and state.get("step") == "ID":
        state["pid"] = text; state["step"] = "OK"
        await update.message.reply_text(f"📝 {state['pack']}\nID: {text}\nНапишіть 'ОК' для підтвердження."); return

    if isinstance(state, dict) and state.get("step") == "OK" and text.upper() in ["ОК", "OK"]:
        pack = state["pack"]
        pid = state["pid"]
        price = get_pack_price(pack)
        disc_pct, disc_src, disc_id = get_user_discount(uid, pack)
        final_price = apply_discount(price, disc_pct) if disc_pct else price

        oid = str(uuid.uuid4())[:8]
        db_exec("INSERT INTO orders (id, user, pack, status, chat_id, player_id, created_at, amount, payment) VALUES (?,?,?,?,?,?,?,?,?)",
                (oid, update.effective_user.username, pack, "pending", uid, pid, created_at_now(), final_price, "card"))

        if disc_pct and disc_src == "promo":
            db_exec("UPDATE user_bonuses SET used=1 WHERE id=?", (disc_id,))
        elif disc_pct and disc_src == "ref":
            db_exec("DELETE FROM ref_discounts WHERE id=?", (disc_id,))

        update_user_profile(uid)

        price_text = f"💵 Сума: *{final_price} грн*"
        if disc_pct:
            price_text += f" _(знижка {disc_pct}%, було {price} грн)_"

        btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Я оплатив", callback_data=f"paid_{oid}")]])
        await update.message.reply_text(
            f"💳 Карта: `{PAYMENT_CARD}`\n{price_text}",
            reply_markup=btn, parse_mode="Markdown"
        )
        user_states[uid] = None
        return


# --- CALLBACK ОБРОБНИК ---
async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "leave_review":
        user_states[q.from_user.id] = "WAIT_REVIEW"
        await context.bot.send_message(q.from_user.id, "✍️ Напишіть ваш відгук:")
        return

    if data.startswith("delrev_"):
        if is_admin(q.from_user.id):
            db_exec("DELETE FROM reviews WHERE rowid=?", (data[7:],))
            await q.edit_message_text("🗑 Відгук видалено.")
        return

    if data.startswith("ok_"):
        order_id = data[3:]
        res = db_query_one("SELECT chat_id, pack FROM orders WHERE id=?", (order_id,))
        if not res:
            await q.edit_message_text("❌ Замовлення не знайдено."); return
        chat_id, pack = res
        db_exec("UPDATE orders SET status='done', completed_at=? WHERE id=?", (created_at_now(), order_id))
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
        order_id = data[3:]
        res = db_query_one("SELECT chat_id, pack FROM orders WHERE id=?", (order_id,))
        if not res:
            await q.edit_message_text("❌ Замовлення не знайдено."); return
        chat_id, pack = res
        db_exec("UPDATE orders SET status='canceled' WHERE id=?", (order_id,))
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
        res = db_query_one("SELECT pack, player_id, amount FROM orders WHERE id=?", (order_id,))
        if not res:
            await q.answer("Замовлення не знайдено."); return
        pack, player_id, amount = res
        try:
            btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data=f"ok_{order_id}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{order_id}")]])
            rise_marker = "⭐️ НАБІР ПІДЙОМ\n" if "Набір Підйом" in pack else ""
            await context.bot.send_message(MY_ID, f"💰 ОПЛАТА!\n{rise_marker}🆔 {order_id}\n👤 {user_label(q.from_user.username, q.from_user.id)}\n🎁 {pack}\n🎮 ID: {player_id}\n💵 {amount} грн", reply_markup=btns)
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


# --- MAIN ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.Document.ALL |
         filters.Sticker.ALL | filters.VOICE | filters.ANIMATION) & ~filters.COMMAND,
        handle_broadcast_media
    ))
    app.add_handler(CallbackQueryHandler(callback))
    app.job_queue.run_repeating(_send_db_to_owner, interval=3600, first=3600)
    logging.info("Бот запущено!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    import time as _time
    import asyncio as _asyncio
    start_db_backup()
    if os.environ.get("DISABLE_BOT") == "1":
        logging.info("DISABLE_BOT=1 — бот вимкнено.")
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
