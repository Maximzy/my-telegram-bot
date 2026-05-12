import sqlite3, uuid, logging, threading, os, re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- НАЛАШТУВАННЯ ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set!")
ADMIN_PASSWORD = "NezukoAdmin"
PAYMENT_CARD = "4874070020367247"
MY_ID = 1440236609

logging.basicConfig(level=logging.INFO)

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
_c = conn.cursor()
_c.execute("CREATE TABLE IF NOT EXISTS orders (id TEXT, user TEXT, pack TEXT, status TEXT, chat_id INTEGER, player_id TEXT)")
_c.execute("CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY)")
_c.execute("CREATE TABLE IF NOT EXISTS reviews (user TEXT, text TEXT)")
_c.execute("PRAGMA table_info(orders)")
_oc = [r[1] for r in _c.fetchall()]
for _col in ["created_at", "completed_at", "amount", "payment"]:
    if _col not in _oc:
        _c.execute(f"ALTER TABLE orders ADD COLUMN {_col} TEXT")
_c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_id ON orders(id)")
_c.execute("DROP INDEX IF EXISTS idx_reviews_unique")
_c.execute("DROP INDEX IF EXISTS idx_reviews_user")
_c.execute("CREATE TABLE IF NOT EXISTS referrals (referrer_id INTEGER, referred_id INTEGER PRIMARY KEY, created_at TEXT)")
_c.execute("CREATE TABLE IF NOT EXISTS ref_discounts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, created_at TEXT)")
_c.execute("CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, bonus_type TEXT, bonus_value INTEGER, uses_left INTEGER DEFAULT -1, total_uses INTEGER DEFAULT -1, created_at TEXT)")
_c.execute("CREATE TABLE IF NOT EXISTS user_bonuses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, bonus_type TEXT, bonus_value INTEGER, used INTEGER DEFAULT 0, created_at TEXT)")
_c.execute("CREATE TABLE IF NOT EXISTS used_promo_codes (user_id INTEGER, code TEXT, PRIMARY KEY (user_id, code))")
# Додаємо колонки якщо таблиця promo_codes вже існувала без них
_c.execute("PRAGMA table_info(promo_codes)")
_pc_cols = [r[1] for r in _c.fetchall()]
if "uses_left" not in _pc_cols:
    _c.execute("ALTER TABLE promo_codes ADD COLUMN uses_left INTEGER DEFAULT -1")
if "total_uses" not in _pc_cols:
    _c.execute("ALTER TABLE promo_codes ADD COLUMN total_uses INTEGER DEFAULT -1")
conn.commit()
del _c, _oc, _pc_cols

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
ALL_PACKS = {**PACKS, **PRIME_PACKS, **PRIME_PLUS_PACKS}
SMALL_UC = set(list(PACKS.keys())[:6])
MEDIUM_UC = set(list(PACKS.keys())[6:9])

BONUS_TYPES = {
    "free_uc_60":        "🎁 60 UC безкоштовно на акаунт",
    "discount_small_5":  "Знижка 5% на малі UC паки (30–660 UC)",
    "discount_small_4":  "Знижка 4% на малі UC паки (30–660 UC)",
    "discount_small_3":  "Знижка 3% на малі UC паки (30–660 UC)",
    "discount_small_2":  "Знижка 2% на малі UC паки (30–660 UC)",
    "discount_small_1":  "Знижка 1% на малі UC паки (30–660 UC)",
    "discount_medium_2": "Знижка 2% на середні UC паки (1800–8100 UC)",
    "discount_medium_1": "Знижка 1% на середні UC паки (1800–8100 UC)",
}

# --- КЛАВІАТУРИ ---
MAIN_KB = [
    ["🛍 Магазин"],
    ["🏆 Топ донатерів"],
    ["🎁 Промокод", "👥 Реферал"],
    ["📋 Мої замовлення", "📄 Політика"],
    ["🆘 Підтримка"],
    ["⚙️ Адмін"]
]
def get_main_kb(uid):
    has_free_uc = db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type='free_uc_60' AND used=0 LIMIT 1", (uid,))
    if has_free_uc:
        return [["🎁 60 UC Free"]] + MAIN_KB
    return MAIN_KB

SHOP_KB = ReplyKeyboardMarkup(
    [["💸 Купити UC"], ["👑 Prime", "👑 Prime Plus"], ["🔙 Назад"]],
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

POLICY_HTML = """<!doctype html>
<html lang="uk">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Політика магазину UC</title>
    <style>
        body { margin: 0; font-family: Arial, sans-serif; background: #0f172a; color: #e5e7eb; line-height: 1.6; }
        main { max-width: 820px; margin: 0 auto; padding: 32px 18px 48px; }
        section { background: #111827; border: 1px solid #334155; border-radius: 18px; padding: 24px; }
        h1 { color: #facc15; margin-top: 0; }
        h2 { color: #93c5fd; margin-bottom: 6px; }
        p { margin-top: 0; }
    </style>
</head>
<body>
<main>
<section>
<h1>Політика магазину UC</h1>
<p>Наш магазин надає послуги з поповнення UC для гравців PUBG Mobile. Оформлюючи замовлення, клієнт погоджується з правилами роботи магазину.</p>
<h2>1. Оформлення замовлення</h2>
<p>Клієнт самостійно обирає потрібний пакет UC та вказує свій ігровий ID. Перед оплатою необхідно уважно перевірити правильність введених даних.</p>
<h2>2. Оплата</h2>
<p>Замовлення передається в обробку тільки після підтвердження оплати. Якщо оплата не була здійснена або не підтверджена, замовлення не виконується.</p>
<h2>3. Виконання замовлення</h2>
<p>Після оплати UC нараховуються на вказаний клієнтом ігровий ID. Час виконання може залежати від навантаження та доступності сервісу.</p>
<h2>4. Відповідальність клієнта</h2>
<p>Магазин не несе відповідальності за помилки у введеному ігровому ID. Якщо клієнт вказав неправильний ID, повернення коштів або повторне нарахування не гарантується.</p>
<h2>5. Повернення коштів</h2>
<p>Повернення коштів можливе лише у випадку, якщо замовлення ще не було виконано. Після успішного нарахування UC повернення коштів не здійснюється.</p>
<h2>6. Підтримка</h2>
<p>Якщо виникли питання або проблеми із замовленням, клієнт може звернутися до підтримки магазину. Ми намагаємося допомогти кожному клієнту якнайшвидше.</p>
<h2>7. Зміна правил</h2>
<p>Магазин залишає за собою право змінювати ці правила. Актуальна політика діє на момент оформлення замовлення.</p>
<p>Оформлюючи замовлення, клієнт підтверджує, що ознайомився з цією політикою та погоджується з її умовами.</p>
</section>
</main>
</body>
</html>"""


class PolicyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204); self.end_headers(); return
        if self.path not in ("/", "/policy"):
            self.send_response(404); self.end_headers(); return
        body = POLICY_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_policy_server():
    server = ThreadingHTTPServer(("0.0.0.0", 5000), PolicyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


# --- ПОМІЧНИКИ ---
def is_admin(uid):
    if uid == MY_ID: return True
    return bool(db_query_one("SELECT id FROM admins WHERE id=?", (uid,)))

def get_policy_url():
    domain = os.getenv("REPLIT_DEV_DOMAIN") or os.getenv("REPLIT_DOMAINS", "").split(",")[0]
    return f"https://{domain}/policy" if domain else "/policy"

def get_pack_price(pack):
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
        rows = db_query("SELECT pack FROM orders WHERE status='done' AND COALESCE(completed_at, created_at) LIKE ?", (f"{today}%",))
    else:
        rows = db_query("SELECT pack FROM orders WHERE status='done'")
    return sum(get_pack_price(r[0]) for r in rows)

def get_user_discount(uid, pack_name):
    if pack_name in SMALL_UC:
        for pct in [5, 4, 3, 2, 1]:
            b = db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type=? AND used=0 LIMIT 1", (uid, f"discount_small_{pct}"))
            if b: return pct, "promo", b[0]
    if pack_name in MEDIUM_UC:
        for pct in [2, 1]:
            b = db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type=? AND used=0 LIMIT 1", (uid, f"discount_medium_{pct}"))
            if b: return pct, "promo", b[0]
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
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg[4:])
                if referrer_id != uid and not db_query_one("SELECT referred_id FROM referrals WHERE referred_id=?", (uid,)):
                    db_exec("INSERT OR IGNORE INTO referrals (referrer_id, referred_id, created_at) VALUES (?,?,?)", (referrer_id, uid, created_at_now()))
            except: pass
    await update.message.reply_text("👋 Вітаємо!", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_admin(uid):
        user_states[uid] = "ADMIN_MODE"
        await update.message.reply_text("🔘 Адмін-панель:", reply_markup=ReplyKeyboardMarkup(ADMIN_KB, resize_keyboard=True))
    else:
        user_states[uid] = "WAIT_PASS"
        await update.message.reply_text("🔑 Пароль:", reply_markup=ReplyKeyboardRemove())

async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛍 Оберіть категорію:", reply_markup=SHOP_KB)

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Оберіть пакет:", reply_markup=ReplyKeyboardMarkup([[p] for p in PACKS.keys()], resize_keyboard=True))

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👨‍💻 Менеджер: @Manager_Nezuko")

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
        "Якщо виникли питання або проблеми із замовленням, клієнт може звернутися до підтримки магазину. "
        "Ми намагаємося допомогти кожному клієнту якнайшвидше.\n\n"
        "*7. Зміна правил*\n"
        "Магазин залишає за собою право змінювати ці правила. "
        "Актуальна політика діє на момент оформлення замовлення.\n\n"
        "*8. Флуд у особисті повідомлення*\n"
        "Якщо клієнт після оформлення замовлення починає надсилати повідомлення на кшталт "
        "«Де мої UC?», «Ау, UC де?» — магазин має право відмовити в обслуговуванні. "
        "Писати нагадування допустимо лише у тому випадку, якщо з моменту оформлення замовлення "
        "пройшло більше 10 хвилин.\n\n"
        "_Оформлюючи замовлення, клієнт підтверджує, що ознайомився з цією політикою та погоджується з її умовами._"
    )
    await update.message.reply_text(policy_text, parse_mode="Markdown")

async def reviews_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    revs = db_query("SELECT user, text FROM reviews ORDER BY rowid DESC LIMIT 15")
    if not revs:
        await update.message.reply_text("🌟 Відгуків ще немає."); return
    msg = "🌟 ОСТАННІ ВІДГУКИ:\n\n"
    for r in revs: msg += f"👤 {r[0]}: {r[1]}\n\n"
    await update.message.reply_text(msg)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    order = db_query_one("SELECT id, pack, status FROM orders WHERE chat_id=? ORDER BY rowid DESC LIMIT 1", (uid,))
    if not order:
        await update.message.reply_text("📭 У вас ще немає замовлень."); return
    await update.message.reply_text(f"📦 Ваш останній заказ:\n🆔 {order[0]}\n🎁 {order[1]}\n📌 Статус: {status_text(order[2])}")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    orders = db_query("SELECT id, pack, status FROM orders WHERE chat_id=? ORDER BY rowid DESC LIMIT 5", (uid,))
    if not orders:
        await update.message.reply_text("📭 У вас ще немає замовлень."); return
    msg = "📋 Ваші останні замовлення:\n\n"
    for o in orders: msg += f"🆔 {o[0]}\n🎁 {o[1]}\n📌 Статус: {status_text(o[2])}\n\n"
    await update.message.reply_text(msg)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Як користуватися ботом:\n\n"
        "1. Натисніть «🛍 Магазин» і оберіть категорію.\n"
        "2. Оберіть потрібний пакет UC.\n"
        "3. Введіть свій ігровий ID.\n"
        "4. Напишіть «ОК» та оплатіть замовлення.\n"
        "5. Після оплати натисніть «✅ Я оплатив».\n\n"
        "Підтримка: /support\n"
        "Політика магазину: /policy\n"
        "Статус замовлення: /status\n"
        "Історія замовлень: /history"
    )

async def mypromos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bonuses = db_query("SELECT bonus_type, bonus_value FROM user_bonuses WHERE user_id=? AND used=0", (uid,))
    ref_discounts = db_query("SELECT id FROM ref_discounts WHERE user_id=?", (uid,))

    if not bonuses and not ref_discounts:
        await update.message.reply_text("🎁 У вас поки немає активних бонусів.\n\nАктивуйте промокод або запросіть друзів через реферальне посилання!")
        return

    msg = "🎁 ВАШІ АКТИВНІ БОНУСИ:\n\n"

    bonus_counts = {}
    for bonus_type, _ in bonuses:
        bonus_counts[bonus_type] = bonus_counts.get(bonus_type, 0) + 1

    for bonus_type, count in bonus_counts.items():
        name = BONUS_TYPES.get(bonus_type, bonus_type)
        plural = "шт." if count == 1 else "шт."
        msg += f"✅ {name}\n   Кількість: {count} {plural}\n\n"

    if ref_discounts:
        msg += f"✅ Реферальна знижка 1% на будь-яку покупку\n   Кількість: {len(ref_discounts)} шт.\n\n"

    msg += "💡 Знижки та бонуси застосовуються автоматично при покупці."
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
    done_count = db_query_one("SELECT COUNT(*) FROM orders WHERE status='done'")[0]
    canceled_count = db_query_one("SELECT COUNT(*) FROM orders WHERE status='canceled'")[0]
    total_sum = get_done_sum()
    today_sum = get_done_sum(today_only=True)
    await update.message.reply_text(f"📊 Статистика магазину:\n✅ Виконано замовлень: {done_count}\n❌ Відхилено замовлень: {canceled_count}\n💰 Загальна сума продажів: {total_sum} грн\n📅 Сума за сьогодні: {today_sum} грн")

async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Тільки для адміна."); return
    if not context.args:
        await update.message.reply_text("Використання: /find ID_замовлення"); return
    order = db_query_one("SELECT id, user, pack, status, chat_id, player_id FROM orders WHERE id=?", (context.args[0],))
    if not order:
        await update.message.reply_text("📭 Замовлення не знайдено."); return
    await update.message.reply_text(f"🔎 Замовлення:\n🆔 {order[0]}\n👤 {user_label(order[1], order[4])}\n🎁 {order[2]}\n🎮 ID: {order[5]}\n📌 Статус: {status_text(order[3])}\n💬 Chat ID: {order[4]}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Тільки для адміна."); return
    message = " ".join(context.args)
    if not message:
        user_states[uid] = "WAIT_BROADCAST"
        await update.message.reply_text("✉️ Напишіть текст розсилки:"); return
    await send_broadcast(update, context, message)

async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, message):
    users = [r[0] for r in db_query("SELECT DISTINCT chat_id FROM orders WHERE chat_id IS NOT NULL")]
    sent = 0
    for chat_id in users:
        try:
            await context.bot.send_message(chat_id, message); sent += 1
        except: pass
    await update.message.reply_text(f"✅ Розсилку надіслано. Отримали: {sent}")


# --- ГОЛОВНИЙ ОБРОБНИК ПОВІДОМЛЕНЬ ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message or not update.message.text: return
    text = update.message.text
    state = user_states.get(uid)

    # ── Пріоритетні стани ──────────────────────────────────────────────────────

    if state == "WAIT_REVIEW":
        user_states[uid] = None
        user_name = f"@{update.effective_user.username}" if update.effective_user.username else "Анонім"
        db_exec("INSERT INTO reviews (user, text) VALUES (?, ?)", (user_name, text))
        await update.message.reply_text("✅ Дякуємо! Відгук збережено.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
        return

    if state == "WAIT_BROADCAST" and is_admin(uid):
        user_states[uid] = "ADMIN_MODE"
        await send_broadcast(update, context, text)
        return

    if state == "WAIT_PROMO_CODE":
        user_states[uid] = None
        code = text.upper().strip().replace(" ", "")
        promo = db_query_one("SELECT bonus_type, bonus_value, uses_left, total_uses FROM promo_codes WHERE code=?", (code,))
        if not promo:
            await update.message.reply_text("❌ Промокод не знайдено або він недійсний.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)); return
        already = db_query_one("SELECT 1 FROM used_promo_codes WHERE user_id=? AND code=?", (uid, code))
        if already:
            await update.message.reply_text("❌ Ви вже використали цей промокод.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)); return
        bonus_type, bonus_value, uses_left, total_uses = promo
        # Перевірка залишку активацій
        if uses_left is not None and uses_left != -1 and uses_left <= 0:
            await update.message.reply_text("❌ Цей промокод вичерпав ліміт активацій.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)); return
        # Застосовуємо промокод
        db_exec("INSERT OR IGNORE INTO used_promo_codes (user_id, code) VALUES (?,?)", (uid, code))
        db_exec("INSERT INTO user_bonuses (user_id, bonus_type, bonus_value, used, created_at) VALUES (?,?,?,0,?)", (uid, bonus_type, bonus_value, created_at_now()))
        # Зменшуємо лічильник активацій
        if uses_left is not None and uses_left != -1:
            new_uses = uses_left - 1
            if new_uses <= 0:
                db_exec("DELETE FROM promo_codes WHERE code=?", (code,))
                db_exec("DELETE FROM used_promo_codes WHERE code=?", (code,))
            else:
                db_exec("UPDATE promo_codes SET uses_left=? WHERE code=?", (new_uses, code))
        bonus_name = BONUS_TYPES.get(bonus_type, bonus_type)
        if bonus_type == "free_uc_60":
            kb = ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)
            await update.message.reply_text(f"✅ Промокод активовано!\n🎁 Бонус: {bonus_name}\n\nНатисніть кнопку нижче для отримання UC:", reply_markup=kb)
        else:
            await update.message.reply_text(f"✅ Промокод активовано!\n🎁 Бонус: {bonus_name}\n\nЗнижка буде застосована автоматично при наступній покупці.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
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
            await update.message.reply_text("❌ Введіть число від 1 до 1000000:"); return
        uses = int(cleaned)
        if uses < 1 or uses > 1000000:
            await update.message.reply_text("❌ Введіть число від 1 до 1000000:"); return
        code = state["code"]
        bonus_type = state["bonus_type"]
        db_exec("INSERT OR REPLACE INTO promo_codes (code, bonus_type, bonus_value, uses_left, total_uses, created_at) VALUES (?,?,?,?,?,?)",
                (code, bonus_type, 0, uses, uses, created_at_now()))
        db_exec("DELETE FROM used_promo_codes WHERE code=?", (code,))
        user_states[uid] = None
        bonus_name = BONUS_TYPES.get(bonus_type, bonus_type)
        await update.message.reply_text(f"✅ Промокод *{code}* створено!\n🎁 Бонус: {bonus_name}\n🔢 Активацій: {uses}/{uses}", parse_mode="Markdown")
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
                await update.message.reply_text("🌟 Відгуків ще немає."); return
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
            await update.message.reply_text(f"📊 Статистика магазину:\n✅ Виконано: {done_count}\n❌ Відхилено: {canceled_count}\n💰 Загальна сума: {total_sum} грн\n📅 Сума за сьогодні: {today_sum} грн")
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
        raw = db_query("SELECT user, chat_id, pack FROM orders WHERE status='done'")
        totals = {}
        for user, cid, pack in raw:
            key = (user_label(user, cid), cid)
            totals[key] = totals.get(key, 0) + get_pack_price(pack)
        top = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:10]
        if not top:
            await update.message.reply_text("🏆 Поки що немає даних для таблиці лідерів."); return
        msg = "🏆 ТОП-10 ДОНАТЕРІВ:\n\n"
        medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
        for i, ((uname, _), total) in enumerate(top):
            msg += f"{medals[i]} {uname} — {total} грн\n"
        await update.message.reply_text(msg); return

    if text == "🎁 Промокод":
        user_states[uid] = "WAIT_PROMO_CODE"
        await update.message.reply_text("🎁 Введіть промокод:", reply_markup=ReplyKeyboardRemove()); return

    if text == "👥 Реферал":
        bot_username = context.bot.username
        ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"
        refs = db_query("SELECT referred_id FROM referrals WHERE referrer_id=?", (uid,))
        discounts = db_query("SELECT id FROM ref_discounts WHERE user_id=?", (uid,))
        msg = (
            f"👥 РЕФЕРАЛЬНА СИСТЕМА\n\n"
            f"🔗 Ваше посилання:\n{ref_link}\n\n"
            f"👤 Запрошено людей: {len(refs)}\n"
            f"🎁 Доступних знижок (1%): {len(discounts)}\n\n"
            f"За кожного друга, який зробить покупку через ваше посилання — ви отримуєте знижку 1% на будь-яку покупку!"
        )
        await update.message.reply_text(msg); return

    if text == "💸 Купити UC":
        await update.message.reply_text("Оберіть пакет:", reply_markup=ReplyKeyboardMarkup([[p] for p in PACKS.keys()], resize_keyboard=True)); return

    if text == "👑 Prime":
        await update.message.reply_text("Оберіть Prime:", reply_markup=ReplyKeyboardMarkup([[p] for p in PRIME_PACKS.keys()], resize_keyboard=True)); return

    if text == "👑 Prime Plus":
        await update.message.reply_text("Оберіть Prime Plus:", reply_markup=ReplyKeyboardMarkup([[p] for p in PRIME_PLUS_PACKS.keys()], resize_keyboard=True)); return

    if text == "🆘 Підтримка":
        await update.message.reply_text("👨‍💻 Менеджер: @Manager_Nezuko"); return

    if text == "📋 Мої замовлення":
        await history_command(update, context); return

    if text == "📄 Політика":
        await policy_command(update, context); return

    if text == "🎁 60 UC Free":
        bonus = db_query_one("SELECT id FROM user_bonuses WHERE user_id=? AND bonus_type='free_uc_60' AND used=0 LIMIT 1", (uid,))
        if not bonus:
            await update.message.reply_text("❌ Цей бонус недоступний.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True)); return
        user_states[uid] = {"step": "FREE_UC_ID", "bonus_id": bonus[0]}
        await update.message.reply_text("🎮 Введіть ваш ігровий ID для нарахування 60 UC:", reply_markup=ReplyKeyboardRemove()); return

    # ── Вибір пакету ───────────────────────────────────────────────────────────

    if text in ALL_PACKS:
        user_states[uid] = {"pack": text, "step": "ID"}
        await update.message.reply_text("🎮 Введіть ваш ігровий ID:", reply_markup=ReplyKeyboardRemove()); return

    # ── Флоу замовлення ────────────────────────────────────────────────────────

    if isinstance(state, dict) and state.get("step") == "FREE_UC_ID":
        game_id = text
        bonus_id = state["bonus_id"]
        db_exec("UPDATE user_bonuses SET used=1 WHERE id=?", (bonus_id,))
        oid = str(uuid.uuid4())[:8]
        db_exec("INSERT INTO orders (id, user, pack, status, chat_id, player_id, created_at, amount, payment) VALUES (?,?,?,?,?,?,?,?,?)",
                (oid, update.effective_user.username, "🎁 60 UC Free (бонус)", "pending", uid, game_id, created_at_now(), 0, "bonus"))
        if MY_ID != 0:
            try:
                btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Надіслано", callback_data=f"ok_{oid}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{oid}")]])
                await context.bot.send_message(MY_ID, f"🎁 БЕЗКОШТОВНІ UC!\n🆔 {oid}\n👤 {user_label(update.effective_user.username, uid)}\n🎮 ID: {game_id}\n💵 60 UC (безкоштовно)", reply_markup=btns)
            except: pass
        user_states[uid] = None
        await update.message.reply_text("✅ Заявку прийнято! 60 UC буде нараховано найближчим часом.", reply_markup=ReplyKeyboardMarkup(get_main_kb(uid), resize_keyboard=True))
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

    # ── Промокоди (адмін) ────────────────────────────────────────────────────
    if data == "promo_create":
        if is_admin(q.from_user.id):
            user_states[q.from_user.id] = {"step": "WAIT_PROMO_CODE_NAME"}
            await context.bot.send_message(q.from_user.id, "🎁 Введіть назву нового промокоду (напр. SUMMER2024):")
        return

    if data.startswith("promo_del_"):
        if is_admin(q.from_user.id):
            code = data[len("promo_del_"):]
            db_exec("DELETE FROM promo_codes WHERE code=?", (code,))
            db_exec("DELETE FROM used_promo_codes WHERE code=?", (code,))
            await q.edit_message_text(f"🗑 Промокод {code} видалено.")
        return

    if data.startswith("promo_bonus_"):
        if is_admin(q.from_user.id):
            bonus_type = data[len("promo_bonus_"):]
            st = user_states.get(q.from_user.id)
            if isinstance(st, dict) and st.get("step") == "WAIT_PROMO_BONUS":
                code = st["code"]
                user_states[q.from_user.id] = {"step": "WAIT_PROMO_USES", "code": code, "bonus_type": bonus_type}
                bonus_name = BONUS_TYPES.get(bonus_type, bonus_type)
                unlimited_btn = InlineKeyboardMarkup([[InlineKeyboardButton("∞ Без ліміту", callback_data=f"promo_unlimited_{code}|{bonus_type}")]])
                await q.edit_message_text(
                    f"🎁 Промокод: *{code}*\n✅ Бонус: {bonus_name}\n\n🔢 Введіть кількість активацій (1–1 000 000)\nабо натисніть кнопку нижче:",
                    parse_mode="Markdown", reply_markup=unlimited_btn
                )
        return

    if data.startswith("promo_unlimited_"):
        if is_admin(q.from_user.id):
            payload = data[len("promo_unlimited_"):]
            if "|" in payload:
                code, bonus_type = payload.split("|", 1)
                db_exec("INSERT OR REPLACE INTO promo_codes (code, bonus_type, bonus_value, uses_left, total_uses, created_at) VALUES (?,?,?,?,?,?)",
                        (code, bonus_type, 0, -1, -1, created_at_now()))
                db_exec("DELETE FROM used_promo_codes WHERE code=?", (code,))
                user_states[q.from_user.id] = None
                bonus_name = BONUS_TYPES.get(bonus_type, bonus_type)
                await q.edit_message_text(f"✅ Промокод *{code}* створено!\n🎁 Бонус: {bonus_name}\n🔢 Активацій: ∞ безліміт", parse_mode="Markdown")
        return

    # ── Замовлення ────────────────────────────────────────────────────────────
    parts = data.split("_", 1)
    if len(parts) < 2: return
    act, oid = parts[0], parts[1]

    res = db_query_one("SELECT chat_id, pack, user, player_id FROM orders WHERE id=?", (oid,))
    if not res: return

    if act == "paid":
        if MY_ID != 0:
            try:
                btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data=f"ok_{oid}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{oid}")]])
                price_row = db_query_one("SELECT amount FROM orders WHERE id=?", (oid,))
                price_val = (price_row[0] if price_row and price_row[0] else get_pack_price(res[1]))
                await context.bot.send_message(MY_ID, f"💰 ОПЛАТА!\n🆔 {oid}\n👤 {user_label(res[2], res[0])}\n🎁 {res[1]}\n🎮 ID: {res[3]}\n💵 Сума: {price_val} грн", reply_markup=btns)
            except: pass
        await q.edit_message_text("✅ Очікуйте нарахування!")

    elif act == "ok":
        db_exec("UPDATE orders SET status='done', completed_at=? WHERE id=?", (created_at_now(), oid))
        ref = db_query_one("SELECT referrer_id FROM referrals WHERE referred_id=?", (res[0],))
        if ref:
            db_exec("INSERT INTO ref_discounts (user_id, created_at) VALUES (?,?)", (ref[0], created_at_now()))
            try:
                await context.bot.send_message(ref[0], "🎉 Ваш реферал зробив покупку! Ви отримали знижку 1% на наступне замовлення. 👥")
            except: pass
        rev_kb = InlineKeyboardMarkup([[InlineKeyboardButton("✍️ Залишити відгук", callback_data="leave_review")]])
        await context.bot.send_message(res[0], f"✅ {res[1]} нараховано!", reply_markup=rev_kb)
        await q.edit_message_text(f"✅ Виконано: {oid}")

    elif act == "no":
        db_exec("UPDATE orders SET status='canceled' WHERE id=?", (oid,))
        try:
            await context.bot.send_message(res[0], f"❌ Ваше замовлення ({res[1]}) відхилено. Зверніться в підтримку.")
        except: pass
        await q.edit_message_text(f"❌ Відхилено: {oid}")


if __name__ == "__main__":
    start_policy_server()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("shop", shop_command))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CommandHandler("support", support_command))
    app.add_handler(CommandHandler("policy", policy_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("reviews", reviews_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("mypromos", mypromos_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("find", find_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(callback))
    print("🚀 БОТ ЗАПУЩЕНИЙ!")
    app.run_polling(drop_pending_updates=True)
