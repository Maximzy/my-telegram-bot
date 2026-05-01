# Project Overview

Python Telegram bot in Ukrainian for selling PUBG Mobile UC, Prime, and Prime Plus packs. The bot uses `python-telegram-bot` and a local SQLite database `bot.db`.

# Runtime

- Main entry point: `main.py`
- Workflow: `Start application`
- Command: `python main.py`
- Policy web page runs on port 5000 at `/policy`

# Features

- User flows: `/start`, `/shop`, `/buy`, `/support`, `/policy`, `/status`, `/history`, `/reviews`, `/help`
- Admin flows: `/admin`, `/orders`, `/stats`, `/find`, `/broadcast`
- Product categories: UC, Prime, Prime Plus
- Order statuses: `pending`, `done`, `canceled`
- Admin notification after payment includes order ID, user, product, game ID, amount, and action buttons
- Admin statistics include completed orders, rejected orders, total sales amount, and today's sales amount
- Users can view recent orders with the `📋 Мої замовлення` button
- Store policy is available from `/policy` and `📄 Політика`

# Database

SQLite tables:
- `orders`: id, user, pack, status, chat_id, player_id, created_at, completed_at
- `admins`: id
- `reviews`: user, text

# Preferences

- Keep the bot UI in Ukrainian.
- The user prefers additive changes only: do not remove or rewrite existing logic unless explicitly requested.
- Always restart the bot and check logs after code changes.
