---
name: Stars prize flow
description: How Telegram Stars prizes work as promo bonuses
---

## Rule
Stars prizes (stars_50_prize, stars_100_prize, stars_150_prize, stars_200_prize) are manual delivery bonuses. User provides their @Telegram tag, admin is notified and sends Stars manually.

**Why:** Telegram Stars cannot be sent programmatically bot-to-user without an invoice flow. The simplest approach is admin manual transfer.

**How to apply:**
- Bonus types are in BONUS_TYPES dict in main.py
- Claim endpoint: `/api/claim-stars` (POST) — accepts `tg_tag`, creates pending order, notifies admin
- Frontend: `claimStars(count, bonus_type)` uses `showInputModal` to ask for @tag
- In bonuses section, stars_*_prize bonuses show golden "⭐ Отримати N Stars" buttons
- Delivery order is stored in `orders` table with `payment='bonus'` and `player_id=tg_tag`
