# Nezuko UC Bot

Telegram shop bot for UC (Unknown Cash), Telegram Stars, and Telegram Premium.
Monobank receipt verification + FazerCards auto-delivery.

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/Maximzy/my-telegram-bot.git
cd my-telegram-bot
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 2. Configure `.env`

```bash
cp .env.example .env
```

Fill in your values:

| Variable | Where to get |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `OWNER_ID` | [@userinfobot](https://t.me/userinfobot) → your numeric ID |
| `ADMIN_PASSWORD` | Any password you want for admin panel |
| `MONOBANK_TOKEN` | [api.monobank.ua](https://api.monobank.ua/) → get personal token |
| `MONOBANK_ACCOUNT_ID` | Run `test_apis.py` after setting token — prints account ID |
| `PAYMENT_CARD` | Your Monobank card number (users send money here) |
| `FAZERCARDS_API_KEY` | [FazerCards B2B dashboard](https://fazercards.com) → API keys |
| `FAZERCARDS_WEBHOOK_SECRET` | Any random string (32+ chars) |

### 3. Start Cloudflare Tunnel (for public URL)

The mini app needs a public HTTPS URL. Use Cloudflare Quick Tunnel:

```bash
# Terminal 1
cloudflared tunnel --url http://localhost:8080
```

Copy the `https://xxx.trycloudflare.com` URL from output.

Update `.env`:
```
WEBAPP_URL=https://xxx.trycloudflare.com/app
```

> Note: Quick tunnel URLs change on restart. For production, use a named tunnel:
> ```bash
> cloudflared tunnel create nezuko
> cloudflared tunnel route dns nezuko your-subdomain.your-domain.com
> cloudflared tunnel run --config cloudflared.yml nezuko
> ```

### 4. Start the Bot

```bash
# Terminal 2
cd /path/to/my-telegram-bot
venv/bin/python run.py
```

Or use the start script (starts tunnel + bot together):
```bash
./start.sh
```

### 5. Register Monobank Webhook

After the bot starts (it auto-registers webhooks), verify:

1. Open mini app via bot → `/start` → click "🛒 Магазин"
2. Create a test order → pay the exact amount to your card
3. Click "✅ Я оплатив" → "⏳ Перевірити оплату"
4. Bot checks Monobank statement for matching amount (±1 UAH tolerance, 15 min window)

### 6. Deposit FazerCards Balance

Auto-delivery requires FazerCards balance. Deposit at least $5-10 at [fazercards.com](https://fazercards.com).

## How Payment Flow Works

```
User selects pack → enters PUBG player ID → sees price
  ↓
User pays to Monobank card → clicks "Я оплатив"
  ↓
Server creates order (status=pending)
  ↓
User clicks "Перевірити оплату"
  ↓
verify_payment(): checks Monobank /personal/statement/{account}/{from}/{to}
  Match by amount (±1 UAH, 15 min window)
  ↓
If matched → verify_and_deliver():
  ├── Auto-deliverable (60+ UC): create FazerCards topup order → poll status (60s)
  └── Manual (30/120/180 UC): notify admin for manual processing
  ↓
FazerCards delivers UC → status=done → user notified
```

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Bot handlers + HTTP server (ThreadingHTTPServer on PORT) |
| `payment_service.py` | Monobank + FazerCards API clients, `verify_and_deliver()` |
| `miniapp.html` | Telegram Mini App frontend (served by HTTP server) |
| `bot.db` | SQLite database (auto-created, not in repo) |
| `run.py` | Wrapper: loads .env → runs main.py as `__main__` |
| `start.sh` | Starts Cloudflare tunnel + bot |
| `test_apis.py` | Tests Monobank + FazerCards API connections |

## Price Configuration

Prices are set via bot admin commands or mini app admin panel:
- `/setprice <pack_name> <price>` — override price for a pack
- Mini app → Admin → 💰 Ціни UC

Discounts are configured per-user via promo codes:
- Mini app → Admin → 🎁 Промокоди

## Troubleshooting

### "Платіж не знайдено" (Payment not found)
- Check tolerance: `payment_service.py` uses ±1 UAH (100 kopiykas)
- Check Monobank token is valid: `venv/bin/python test_apis.py`
- Check the user paid the **exact** amount shown in mini app
- Check logs: unmatched transactions are logged with amounts

### Mini app not accessible
- Cloudflare tunnel URL changes on restart — send `/start` to get fresh keyboard
- Check `WEBAPP_URL` in `.env` matches current tunnel URL

### FazerCards auto-delivery fails
- Check balance at fazercards.com
- Check `FAZERCARDS_API_KEY` in `.env`
- 30/120/180 UC packs are **manual** delivery (no auto)

### Bot exits silently
- Check Python version: requires 3.12+ (python-telegram-bot 22.x)
- Run `venv/bin/python run.py` (not `python main.py` directly — .env won't load)

## Tech Stack

- Python 3.12+ / [python-telegram-bot 22.x](https://docs.python-telegram-bot.org/)
- **PostgreSQL** (via Railway, auto-detected from `DATABASE_URL`) or SQLite fallback
- Monobank Personal API
- FazerCards B2B API v2
- Cloudflare Quick Tunnels
- No Docker required (runs in VS Code / terminal)

## Database

The bot auto-detects the database backend:

- If `DATABASE_URL` is set in the environment → **PostgreSQL** (via psycopg2)
- If not set → **SQLite** fallback (local `bot.db`)

All SQL is written in SQLite syntax — `db_compat.py` translates it to PostgreSQL
at runtime (`?`→`%s`, `INSERT OR REPLACE`→`ON CONFLICT`, `AUTOINCREMENT`→`SERIAL`,
`PRAGMA`→skip/emulate, `lastrowid`→`RETURNING id`).

### Railway PostgreSQL Setup

1. Railway → New → Database → PostgreSQL
2. Bot service → Variables → add `DATABASE_URL = ${{Postgres.DATABASE_URL}}`
3. Push to GitHub → Railway auto-deploys
4. Check logs: `db_compat: using PostgreSQL backend`

### Test orders without point deduction

Set `TEST_NO_POINTS_DEDUCT=1` in Railway Variables. Purchases will not deduct
points. **Remove this variable after testing!**
