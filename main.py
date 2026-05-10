import sqlite3, uuid, logging, threading, os, re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- НАЛАШТУВАННЯ ---
TOKEN = "8036989406:AAFVrPB-41p5XLMgrndZHlAKqWOQMMB46E4"
ADMIN_PASSWORD = "NezukoAdmin"
PAYMENT_CARD = "4874070020367247"
MY_ID = 1440236609  # Впиши свій ID

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

_init_cur = conn.cursor()
_init_cur.execute("CREATE TABLE IF NOT EXISTS orders (id TEXT, user TEXT, pack TEXT, status TEXT, chat_id INTEGER, player_id TEXT)")
_init_cur.execute("CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY)")
_init_cur.execute("CREATE TABLE IF NOT EXISTS reviews (user TEXT, text TEXT)")
_init_cur.execute("PRAGMA table_info(orders)")
order_columns = [column[1] for column in _init_cur.fetchall()]
if "created_at" not in order_columns:
    _init_cur.execute("ALTER TABLE orders ADD COLUMN created_at TEXT")
if "completed_at" not in order_columns:
    _init_cur.execute("ALTER TABLE orders ADD COLUMN completed_at TEXT")
_init_cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_id ON orders(id)")
_init_cur.execute("DROP INDEX IF EXISTS idx_reviews_unique")
_init_cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_user ON reviews(user)")
conn.commit()
del _init_cur

logging.info(f"База данных: {DB_PATH}")

PACKS = {
    "30 UC - 19 грн": 19, "60 UC - 40 грн": 40, "120 UC - 78 грн": 78,
    "180 UC - 113 грн": 111, "325 UC - 195 грн": 195, "660 UC - 389 грн": 389,
    "1800 UC - 960 грн": 960, "3800 UC - 1909 грн": 1909, "8100 UC - 3840 грн": 3840,
    "16200 UC - 7599 грн": 7599, "24300 UC - 11399 грн": 11399, "32400 UC - 15399 грн": 15399,
    "40500 UC - 18999 грн": 18999, "81000 UC - 37900 грн": 37900
}

PRIME_PACKS = {
    "👑 Prime 1 Місяць - 45 грн": 45,
    "👑 Prime 3 Місяця - 130 грн": 130,
    "👑 Prime 6 Місяців - 250 грн": 250,
    "👑 Prime 12 Місяців - 500 грн": 500
}

PRIME_PLUS_PACKS = {
    "👑 Prime Plus 1 Місяць - 410 грн": 410,
    "👑 Prime Plus 3 Місяці - 1200 грн": 1200,
    "👑 Prime Plus 6 Місяців - 2400 грн": 2400,
    "👑 Prime Plus 12 Місяців - 4730 грн": 4730
}

ALL_PACKS = {**PACKS, **PRIME_PACKS, **PRIME_PLUS_PACKS}

MAIN_KB = [["🛍 Магазин"], ["💸 Купити UC"], ["👑 Prime", "👑 Prime Plus"], ["📋 Мої замовлення", "📄 Політика"], ["🆘 Підтримка"], ["⚙️ Адмін"]]
ADMIN_KB = [["📦 Замовлення"], ["🌟 Відгуки"], ["📊 Статистика", "🚪 Вийти"]]

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
            self.send_response(204)
            self.end_headers()
            return
        if self.path not in ("/", "/policy"):
            self.send_response(404)
            self.end_headers()
            return
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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()


def is_admin(uid):
    if uid == MY_ID: return True
    return bool(db_query_one("SELECT id FROM admins WHERE id=?", (uid,)))

def get_policy_url():
    domain = os.getenv("REPLIT_DEV_DOMAIN") or os.getenv("REPLIT_DOMAINS", "").split(",")[0]
    if domain:
        return f"https://{domain}/policy"
    return "/policy"

def get_pack_price(pack):
    if pack in ALL_PACKS:
        return ALL_PACKS[pack]
    match = re.search(r"(\d+)\s*грн", pack)
    if match:
        return int(match.group(1))
    return 0

def status_text(status):
    statuses = {
        "pending": "очікує виконання",
        "done": "виконано",
        "canceled": "відхилено"
    }
    return statuses.get(status, status)

def created_at_now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def user_label(username, chat_id=None):
    if username:
        return f"@{username}"
    if chat_id:
        return str(chat_id)
    return "Без username"

def get_done_sum(today_only=False):
    if today_only:
        today = datetime.now().strftime("%Y-%m-%d")
        rows = db_query("SELECT pack FROM orders WHERE status='done' AND COALESCE(completed_at, created_at) LIKE ?", (f"{today}%",))
    else:
        rows = db_query("SELECT pack FROM orders WHERE status='done'")
    return sum(get_pack_price(row[0]) for row in rows)

def shop_keyboard():
    return ReplyKeyboardMarkup([["💸 Купити UC"], ["👑 Prime", "👑 Prime Plus"], ["📋 Мої замовлення", "📄 Політика"], ["🆘 Підтримка"], ["⚙️ Адмін"]], resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states[uid] = None
    await update.message.reply_text("👋 Вітаємо!", reply_markup=ReplyKeyboardMarkup(MAIN_KB, resize_keyboard=True))

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_admin(uid):
        user_states[uid] = "ADMIN_MODE"
        await update.message.reply_text("🔘 Адмін-панель:", reply_markup=ReplyKeyboardMarkup(ADMIN_KB, resize_keyboard=True))
    else:
        user_states[uid] = "WAIT_PASS"
        await update.message.reply_text("🔑 Пароль:", reply_markup=ReplyKeyboardRemove())

async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛍 Оберіть категорію:", reply_markup=shop_keyboard())

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Оберіть пакет:", reply_markup=ReplyKeyboardMarkup([[p] for p in PACKS.keys()], resize_keyboard=True))

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👨‍💻 Менеджер: @Manager_Nezuko")

async def policy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"📄 Політика магазину:\n{get_policy_url()}")

async def reviews_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    revs = db_query("SELECT user, text FROM reviews ORDER BY rowid DESC LIMIT 15")
    if not revs:
        await update.message.reply_text("🌟 Відгуків ще немає.")
        return
    msg = "🌟 ОСТАННІ ВІДГУКИ:\n\n"
    for r in revs: msg += f"👤 {r[0]}: {r[1]}\n\n"
    await update.message.reply_text(msg)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    order = db_query_one("SELECT id, pack, status FROM orders WHERE chat_id=? ORDER BY rowid DESC LIMIT 1", (uid,))
    if not order:
        await update.message.reply_text("📭 У вас ще немає замовлень.")
        return
    await update.message.reply_text(f"📦 Ваш останній заказ:\n🆔 {order[0]}\n🎁 {order[1]}\n📌 Статус: {status_text(order[2])}")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    orders = db_query("SELECT id, pack, status FROM orders WHERE chat_id=? ORDER BY rowid DESC LIMIT 5", (uid,))
    if not orders:
        await update.message.reply_text("📭 У вас ще немає замовлень.")
        return
    msg = "📋 Ваші останні замовлення:\n\n"
    for order in orders:
        msg += f"🆔 {order[0]}\n🎁 {order[1]}\n📌 Статус: {status_text(order[2])}\n\n"
    await update.message.reply_text(msg)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Як користуватися ботом:\n\n"
        "1. Натисніть /shop або кнопку «💸 Купити UC».\n"
        "2. Оберіть потрібний пакет UC.\n"
        "3. Введіть свій ігровий ID.\n"
        "4. Напишіть «ОК» та оплатіть замовлення.\n"
        "5. Після оплати натисніть «✅ Я оплатив».\n\n"
        "Підтримка: /support\n"
        "Політика магазину: /policy\n"
        "Статус замовлення: /status\n"
        "Історія замовлень: /history"
    )

async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Ця команда доступна тільки адміну.")
        return
    orders = db_query("SELECT id, user, pack, player_id, chat_id FROM orders WHERE status='pending'")
    if not orders:
        await update.message.reply_text("📭 Немає замовлень.")
        return
    for o in orders:
        btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data=f"ok_{o[0]}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{o[0]}")]])
        await update.message.reply_text(f"📦 {o[2]}\n👤 {user_label(o[1], o[4])}\n🎮 ID: `{o[3]}`\n🆔 `{o[0]}`", reply_markup=btns)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Ця команда доступна тільки адміну.")
        return
    done_count = db_query_one("SELECT COUNT(*) FROM orders WHERE status='done'")[0]
    canceled_count = db_query_one("SELECT COUNT(*) FROM orders WHERE status='canceled'")[0]
    total_sum = get_done_sum()
    today_sum = get_done_sum(today_only=True)
    await update.message.reply_text(f"📊 Статистика магазину:\n✅ Виконано замовлень: {done_count}\n❌ Відхилено замовлень: {canceled_count}\n💰 Загальна сума продажів: {total_sum} грн\n📅 Сума за сьогодні: {today_sum} грн")

async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Ця команда доступна тільки адміну.")
        return
    if not context.args:
        await update.message.reply_text("Використання: /find ID_замовлення")
        return
    oid = context.args[0]
    order = db_query_one("SELECT id, user, pack, status, chat_id, player_id FROM orders WHERE id=?", (oid,))
    if not order:
        await update.message.reply_text("📭 Замовлення не знайдено.")
        return
    await update.message.reply_text(f"🔎 Замовлення знайдено:\n🆔 {order[0]}\n👤 {user_label(order[1], order[4])}\n🎁 {order[2]}\n🎮 ID: {order[5]}\n📌 Статус: {status_text(order[3])}\n💬 Chat ID: {order[4]}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Ця команда доступна тільки адміну.")
        return
    message = " ".join(context.args)
    if not message:
        user_states[uid] = "WAIT_BROADCAST"
        await update.message.reply_text("✉️ Напишіть текст розсилки одним повідомленням.")
        return
    await send_broadcast(update, context, message)

async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, message):
    users = [row[0] for row in db_query("SELECT DISTINCT chat_id FROM orders WHERE chat_id IS NOT NULL")]
    sent = 0
    for chat_id in users:
        try:
            await context.bot.send_message(chat_id, message)
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ Розсилку надіслано. Отримали: {sent}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message or not update.message.text: return
    text = update.message.text
    state = user_states.get(uid)

    if state == "WAIT_REVIEW":
        user_states[uid] = None
        user_name = f"@{update.effective_user.username}" if update.effective_user.username else "Анонім"
        db_exec("INSERT OR REPLACE INTO reviews (user, text) VALUES (?, ?)", (user_name, text))
        await update.message.reply_text("✅ Дякуємо! Відгук збережено в базі.", reply_markup=ReplyKeyboardMarkup(MAIN_KB, resize_keyboard=True))
        return

    if state == "WAIT_BROADCAST" and is_admin(uid):
        user_states[uid] = "ADMIN_MODE"
        await send_broadcast(update, context, text)
        return

    if is_admin(uid):
        if "Замовлення" in text:
            orders = db_query("SELECT id, user, pack, player_id, chat_id FROM orders WHERE status='pending'")
            if not orders:
                await update.message.reply_text("📭 Немає замовлень.")
                return
            for o in orders:
                btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data=f"ok_{o[0]}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{o[0]}")]])
                await update.message.reply_text(f"📦 {o[2]}\n👤 {user_label(o[1], o[4])}\n🎮 ID: `{o[3]}`\n🆔 `{o[0]}`", reply_markup=btns)
            return
        if "Відгуки" in text:
            revs = db_query("SELECT user, text FROM reviews ORDER BY rowid DESC LIMIT 15")
            if not revs:
                await update.message.reply_text("🌟 Відгуків ще немає.")
                return
            msg = "🌟 ОСТАННІ ВІДГУКИ:\n\n"
            for r in revs: msg += f"👤 {r[0]}: {r[1]}\n\n"
            await update.message.reply_text(msg)
            return
        if "Статистика" in text:
            done_count = db_query_one("SELECT COUNT(*) FROM orders WHERE status='done'")[0]
            canceled_count = db_query_one("SELECT COUNT(*) FROM orders WHERE status='canceled'")[0]
            total_sum = get_done_sum()
            today_sum = get_done_sum(today_only=True)
            await update.message.reply_text(f"📊 Статистика магазину:\n✅ Виконано замовлень: {done_count}\n❌ Відхилено замовлень: {canceled_count}\n💰 Загальна сума продажів: {total_sum} грн\n📅 Сума за сьогодні: {today_sum} грн")
            return
        if "Вийти" in text:
            user_states[uid] = None
            await update.message.reply_text("Головне меню", reply_markup=ReplyKeyboardMarkup(MAIN_KB, resize_keyboard=True))
            return

    if text == "⚙️ Адмін":
        await admin_panel(update, context)
        return

    if state == "WAIT_PASS":
        if text == ADMIN_PASSWORD:
            db_exec("INSERT OR IGNORE INTO admins VALUES (?)", (uid,))
            user_states[uid] = "ADMIN_MODE"
            await update.message.reply_text("✅ Доступ надано!", reply_markup=ReplyKeyboardMarkup(ADMIN_KB, resize_keyboard=True))
        else:
            user_states[uid] = None
            await update.message.reply_text("❌ Невірно", reply_markup=ReplyKeyboardMarkup(MAIN_KB, resize_keyboard=True))
        return

    if text == "🛍 Магазин":
        await update.message.reply_text("🛍 Оберіть категорію:", reply_markup=shop_keyboard())
        return
    if text == "💸 Купити UC":
        await update.message.reply_text("Оберіть пакет:", reply_markup=ReplyKeyboardMarkup([[p] for p in PACKS.keys()], resize_keyboard=True))
        return
    if text == "👑 Prime":
        await update.message.reply_text("Оберіть Prime:", reply_markup=ReplyKeyboardMarkup([[p] for p in PRIME_PACKS.keys()], resize_keyboard=True))
        return
    if text == "👑 Prime Plus":
        await update.message.reply_text("Оберіть Prime Plus:", reply_markup=ReplyKeyboardMarkup([[p] for p in PRIME_PLUS_PACKS.keys()], resize_keyboard=True))
        return
    if text == "🆘 Підтримка":
        await update.message.reply_text("👨‍💻 Менеджер: @Manager_Nezuko")
        return
    if text == "📋 Мої замовлення":
        await history_command(update, context)
        return
    if text == "📄 Політика":
        await policy_command(update, context)
        return
    if text in ALL_PACKS:
        user_states[uid] = {"pack": text, "step": "ID"}
        await update.message.reply_text("🎮 Введіть ваш ігровий ID:", reply_markup=ReplyKeyboardRemove())
        return
    if isinstance(state, dict) and state.get("step") == "ID":
        state["pid"] = text; state["step"] = "OK"
        await update.message.reply_text(f"📝 {state['pack']}\nID: {text}\nНапишіть 'ОК' для оплати.")
        return
    if isinstance(state, dict) and state.get("step") == "OK" and text.upper() in ["ОК", "OK"]:
        oid = str(uuid.uuid4())[:8]
        db_exec("INSERT INTO orders (id, user, pack, status, chat_id, player_id, created_at) VALUES (?,?,?,?,?,?,?)", (oid, update.effective_user.username, state["pack"], "pending", uid, state["pid"], created_at_now()))
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Я оплатив", callback_data=f"paid_{oid}")]])
        await update.message.reply_text(f"💳 Карта: `{PAYMENT_CARD}`", reply_markup=btn, parse_mode="Markdown")
        user_states[uid] = None

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split("_")
    act = data[0]

    if act == "leave":
        user_states[q.from_user.id] = "WAIT_REVIEW"
        await context.bot.send_message(q.from_user.id, "✍️ Напишіть ваш відгук:")
        return

    oid = data[1]
    res = db_query_one("SELECT chat_id, pack, user, player_id FROM orders WHERE id=?", (oid,))
    if not res: return

    if act == "paid":
        if MY_ID != 0:
            try:
                btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data=f"ok_{oid}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"no_{oid}")]])
                await context.bot.send_message(MY_ID, f"💰 ОПЛАТА!\n🆔 {oid}\n👤 {user_label(res[2], res[0])}\n🎁 {res[1]}\n🎮 ID: {res[3]}\n💵 Сума: {get_pack_price(res[1])} грн", reply_markup=btns)
            except: pass
        await q.edit_message_text("✅ Очікуйте нарахування!")
    elif act == "ok":
        db_exec("UPDATE orders SET status='done', completed_at=? WHERE id=?", (created_at_now(), oid))
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
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("find", find_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(callback))
    print("🚀 БОТ ЗАПУЩЕНИЙ!")
    app.run_polling(drop_pending_updates=True)