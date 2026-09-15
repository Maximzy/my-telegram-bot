"""
Payment service — Monobank receipt verification + FazerCards auto UC delivery.

Architecture: sync functions using urllib.request (works in both the sync
HTTP server and the async Telegram bot via asyncio.to_thread).

Monobank flow:
  1. Client pays to our Monobank card
  2. Client clicks "Я оплатив" → we check /personal/statement for matching tx
  3. If match → payment verified → create FazerCards order

FazerCards flow:
  1. POST /api/v2/topups/order with category_id, offer_id, player_id
  2. Poll /api/v2/orders/:id until status = completed/failed
  3. Or receive webhook → update order status

Env vars:
  MONOBANK_TOKEN, MONOBANK_CARD, MONOBANK_ACCOUNT_ID, MONOBANK_WEBHOOK_URL
  FAZERCARDS_API_KEY, FAZERCARDS_BASE_URL, FAZERCARDS_WEBHOOK_SECRET
  FAZERCARDS_PUBG_CATEGORY, OFFER_60UC, OFFER_325UC, ...
"""

import os
import json
import time
import hmac
import hashlib
import logging
import urllib.request
import urllib.error
import urllib.parse
from typing import Any

# ─── Config from env ──────────────────────────────────────────────────────────

MONOBANK_TOKEN = os.environ.get("MONOBANK_TOKEN", "")
MONOBANK_CARD = os.environ.get("MONOBANK_CARD", "4874070020367247")
MONOBANK_ACCOUNT_ID = os.environ.get("MONOBANK_ACCOUNT_ID", "")
MONOBANK_WEBHOOK_URL = os.environ.get("MONOBANK_WEBHOOK_URL", "")

FAZERCARDS_API_KEY = os.environ.get("FAZERCARDS_API_KEY", "")
FAZERCARDS_BASE_URL = os.environ.get("FAZERCARDS_BASE_URL", "https://api.fzr.cards/api/v2")
FAZERCARDS_WEBHOOK_SECRET = os.environ.get("FAZERCARDS_WEBHOOK_SECRET", "")
FAZERCARDS_PUBG_CATEGORY = os.environ.get("FAZERCARDS_PUBG_CATEGORY", "pubg_mobile_auto")
FAZERCARDS_MIN_BALANCE = float(os.environ.get("FAZERCARDS_MIN_BALANCE", "0"))

# UC amount → FazerCards offer_id mapping
UC_TO_OFFER = {
    60: os.environ.get("OFFER_60UC", "60_uc"),
    325: os.environ.get("OFFER_325UC", "325_uc"),
    660: os.environ.get("OFFER_660UC", "660_uc"),
    1800: os.environ.get("OFFER_1800UC", "1800_uc"),
    3850: os.environ.get("OFFER_3850UC", "3850_uc"),
    8100: os.environ.get("OFFER_8100UC", "8100_uc"),
}

# Prime / Prime Plus offer IDs
PRIME_OFFERS = {
    "prime_1m": os.environ.get("OFFER_PRIME_1M", "prime_1_month"),
    "prime_3m": os.environ.get("OFFER_PRIME_3M", "prime_3_months"),
    "prime_6m": os.environ.get("OFFER_PRIME_6M", "prime_6_months"),
    "prime_12m": os.environ.get("OFFER_PRIME_12M", "prime_12_months"),
}
PRIME_PLUS_OFFERS = {
    "prime_plus_1m": os.environ.get("OFFER_PRIME_PLUS_1M", "prime_plus_1_month"),
    "prime_plus_3m": os.environ.get("OFFER_PRIME_PLUS_3M", "prime_plus_3_months"),
    "prime_plus_6m": os.environ.get("OFFER_PRIME_PLUS_6M", "prime_plus_6_months"),
    "prime_plus_12m": os.environ.get("OFFER_PRIME_PLUS_12M", "prime_plus_12_months"),
}

# Max UC per single FazerCards order
MAX_FC_UC = 8100


# ─── HTTP helper (sync, urllib) ───────────────────────────────────────────────

def _http_request(method: str, url: str, *, headers: dict = None,
                  data: dict = None, timeout: int = 30) -> dict | list:
    """Make an HTTP request using urllib. Returns parsed JSON."""
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        logging.error(f"HTTP {e.code} from {url}: {raw[:300]}")
        raise
    except Exception as e:
        logging.error(f"HTTP request failed to {url}: {e}")
        raise


# ─── Monobank API ─────────────────────────────────────────────────────────────

class MonobankClient:
    """Sync client for Monobank Personal API."""

    BASE_URL = "https://api.monobank.ua"

    @property
    def _headers(self):
        return {"X-Token": MONOBANK_TOKEN}

    def get_statement(self, from_ts: int = None, to_ts: int = None) -> list[dict]:
        """
        GET /personal/statement/{account}/{from}/{to}
        Returns list of transactions. Amount is in kopiyky (1 UAH = 100).
        Positive = income, negative = expense.
        """
        to_ts = to_ts or int(time.time())
        from_ts = from_ts or (to_ts - 900)  # default 15 min
        url = f"{self.BASE_URL}/personal/statement/{MONOBANK_ACCOUNT_ID}/{from_ts}/{to_ts}"
        data = _http_request("GET", url, headers=self._headers)
        return data if isinstance(data, list) else []

    def verify_payment(self, expected_amount_uah: float,
                       tolerance_seconds: int = 900,
                       transaction_id: str = None,
                       tolerance_kopiykas: int = 100) -> dict | None:
        """
        Check statement for an incoming payment matching expected_amount_uah.

        Returns matching transaction dict or None.
        tolerance_kopiykas: how many kopiykas the payment can differ from
            expected (default ±100 = ±1 UAH). Covers discount rounding
            and minor bank fee differences.
        """
        if not MONOBANK_TOKEN or not MONOBANK_ACCOUNT_ID:
            logging.warning("Monobank not configured (token/account missing)")
            return None

        now_ts = int(time.time())
        from_ts = now_ts - tolerance_seconds

        try:
            statements = self.get_statement(from_ts=from_ts, to_ts=now_ts)
        except Exception as e:
            logging.error(f"Monobank statement failed: {e}")
            return None

        expected_kop = round(expected_amount_uah * 100)

        for tx in statements:
            tx_amount = tx.get("amount", 0)
            tx_id = tx.get("id", "")

            # Only incoming (positive)
            if tx_amount <= 0:
                continue

            # Match by transaction ID if provided
            if transaction_id and tx_id != transaction_id:
                continue

            # Match by amount (±tolerance kopiykas)
            diff = abs(tx_amount - expected_kop)
            if diff <= tolerance_kopiykas:
                logging.info(
                    f"Monobank payment matched: tx={tx_id} "
                    f"amount={tx_amount/100:.2f} UAH (expected {expected_amount_uah:.2f})"
                )
                return tx

        # Log all incoming transactions seen for debugging
        incoming = [f"{tx.get('amount', 0)/100:.2f}" for tx in statements if tx.get('amount', 0) > 0]
        logging.info(
            f"No Monobank payment found for {expected_amount_uah:.2f} UAH "
            f"(±{tolerance_kopiykas/100:.2f} UAH) "
            f"in last {tolerance_seconds}s. "
            f"Incoming txs seen: {incoming}"
        )
        return None

    def register_webhook(self, webhook_url: str) -> dict:
        """POST /personal/webhook — register webhook for real-time tx notifications."""
        url = f"{self.BASE_URL}/personal/webhook"
        return _http_request("POST", url, headers=self._headers,
                             data={"webHookUrl": webhook_url})

    def get_client_info(self) -> dict:
        """GET /personal/client-info — accounts list."""
        url = f"{self.BASE_URL}/personal/client-info"
        data = _http_request("GET", url, headers=self._headers)
        return data if isinstance(data, dict) else {}


monobank = MonobankClient()


# ─── FazerCards API ───────────────────────────────────────────────────────────

class FazerCardsClient:
    """Sync client for FazerCards B2B API v2."""

    def __init__(self):
        self._headers = {
            "X-API-Key": FAZERCARDS_API_KEY,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, *, data: dict = None,
                 extra_headers: dict = None, timeout: int = 30):
        url = f"{FAZERCARDS_BASE_URL}{path}"
        headers = dict(self._headers)
        if extra_headers:
            headers.update(extra_headers)
        result = _http_request(method, url, headers=headers, data=data, timeout=timeout)
        return result

    def get_balance(self) -> float:
        """GET /balance — wallet balance in USD."""
        data = self._request("GET", "/balance")
        balance = float(data.get("balance", 0)) if isinstance(data, dict) else 0.0
        return balance

    def create_topup_order(self, category_id: str, offer_id: str,
                           player_id: str, idempotency_key: str = None) -> dict:
        """
        POST /topups/order — create UC top-up order.

        Body: {category_id, offer_id, fields: {player_id: "..."}}
        Returns: {id, status, ...}
        """
        import uuid as _uuid
        idem_key = idempotency_key or str(_uuid.uuid4())
        data = self._request("POST", "/topups/order",
            data={
                "category_id": category_id,
                "offer_id": offer_id,
                "fields": {"player_id": player_id},
            },
            extra_headers={"Idempotency-Key": idem_key})
        order = data.get("order", data) if isinstance(data, dict) else {}
        logging.info(
            f"FazerCards order created: cat={category_id} offer={offer_id} "
            f"player={player_id} -> id={order.get('id')} status={order.get('status')}"
        )
        return order if isinstance(order, dict) else {}

    def get_order(self, order_id: str) -> dict:
        """GET /orders/:id — order detail with status."""
        data = self._request("GET", f"/orders/{order_id}")
        return data.get("order", data) if isinstance(data, dict) else {}

    def set_webhook(self, url: str, enabled: bool = True) -> dict:
        """PUT /account/webhook — set webhook URL."""
        return self._request("PUT", "/account/webhook",
                             data={"url": url, "enabled": enabled})

    def validate_player_id(self, category_id: str, player_id: str) -> dict:
        """POST /topups/validate-id — validate player ID before ordering."""
        data = self._request("POST", "/topups/validate-id",
            data={"category_id": category_id,
                  "fields": {"player_id": player_id}})
        return data if isinstance(data, dict) else {}


fazercards = FazerCardsClient()


# ─── Pack → FazerCards offer mapping ──────────────────────────────────────────

def extract_uc_amount(pack_name: str) -> int:
    """Extract UC amount from pack name like '325 UC - 219 грн' → 325."""
    try:
        # Try to find the number before "UC"
        parts = pack_name.split("UC")
        if len(parts) > 0:
            num_str = parts[0].strip().split()[-1]
            # Handle "🎁 475 UC" → extract 475
            num_str = "".join(c for c in num_str if c.isdigit())
            return int(num_str) if num_str else 0
    except Exception:
        pass
    return 0


def get_offer_id_for_pack(pack_name: str) -> str | None:
    """
    Map a pack name to a FazerCards offer_id.
    Returns None for packs that need manual delivery (30, 120, 180 UC, TG gifts, etc.)
    """
    uc = extract_uc_amount(pack_name)
    if uc > 0:
        return UC_TO_OFFER.get(uc)
    # Prime packs
    if "Prime 1" in pack_name and "Plus" not in pack_name:
        return PRIME_OFFERS.get("prime_1m")
    if "Prime 3" in pack_name and "Plus" not in pack_name:
        return PRIME_OFFERS.get("prime_3m")
    if "Prime 6" in pack_name and "Plus" not in pack_name:
        return PRIME_OFFERS.get("prime_6m")
    if "Prime 12" in pack_name and "Plus" not in pack_name:
        return PRIME_OFFERS.get("prime_12m")
    if "Prime Plus 1" in pack_name:
        return PRIME_PLUS_OFFERS.get("prime_plus_1m")
    if "Prime Plus 3" in pack_name:
        return PRIME_PLUS_OFFERS.get("prime_plus_3m")
    if "Prime Plus 6" in pack_name:
        return PRIME_PLUS_OFFERS.get("prime_plus_6m")
    if "Prime Plus 12" in pack_name:
        return PRIME_PLUS_OFFERS.get("prime_plus_12m")
    return None


def is_auto_deliverable(pack_name: str) -> bool:
    """Check if this pack can be auto-delivered via FazerCards."""
    return get_offer_id_for_pack(pack_name) is not None


# ─── Main payment flow ───────────────────────────────────────────────────────

def verify_and_deliver(order_id: str, pack: str, player_id: str,
                       amount_uah: float, chat_id: int,
                       bot_token: str = None) -> dict:
    """
    Main payment verification + auto-delivery flow.

    1. Check Monobank statement for matching payment
    2. If found → create FazerCards order
    3. Return result dict with status + message

    This is a SYNC function — call via asyncio.to_thread() from async context.

    Returns:
        {
            "verified": bool,
            "delivered": bool,  # True if FazerCards order was created
            "message": str,     # User-facing message (Ukrainian)
            "monobank_tx_id": str | None,
            "fazercards_order_id": str | None,
            "fazercards_status": str | None,
        }
    """
    result = {
        "verified": False,
        "delivered": False,
        "message": "",
        "monobank_tx_id": None,
        "fazercards_order_id": None,
        "fazercards_status": None,
    }

    # ─── Step 1: Verify Monobank payment ────────────────────────────────
    tx = monobank.verify_payment(
        expected_amount_uah=amount_uah,
        tolerance_seconds=900,  # 15 min lookback
    )

    if tx is None:
        result["message"] = (
            "❌ Платіж не знайдено.\n\n"
            "Переконайтеся, що ви перевели правильну суму "
            "та натиснули «Я оплатив» після переказу.\n"
            "Якщо ви щойно оплатили — зачекайте 1-2 хвилини "
            "і спробуйте знову."
        )
        return result

    result["verified"] = True
    result["monobank_tx_id"] = tx.get("id", "")

    # ─── Step 2: Determine delivery strategy ────────────────────────────
    offer_id = get_offer_id_for_pack(pack)

    if offer_id is None:
        # Manual delivery (30/120/180 UC, TG gifts, Rise bundles, etc.)
        result["message"] = (
            "✅ Оплату підтверджено!\n\n"
            f"📦 {pack}\n"
            "⏳ Ваше замовлення передано на ручну обробку.\n"
            "Адмін нарахує UC найближчим часом."
        )
        return result

    # ─── Step 3: Auto-deliver via FazerCards ─────────────────────────────
    try:
        fc_order = fazercards.create_topup_order(
            category_id=FAZERCARDS_PUBG_CATEGORY,
            offer_id=offer_id,
            player_id=player_id,
            idempotency_key=f"order_{order_id}",
        )
        fc_order_id = fc_order.get("id", "")
        fc_status = fc_order.get("status", "")

        result["delivered"] = True
        result["fazercards_order_id"] = fc_order_id
        result["fazercards_status"] = fc_status

        if fc_status == "completed":
            result["message"] = (
                "✅ Оплата пройшла успішно!\n\n"
                f"📦 {pack}\n"
                f"🎮 Player ID: {player_id}\n"
                "🎉 UC вже нараховано на ваш акаунт!\n\n"
                "Дякуємо за покупку! 🌸"
            )
        else:
            result["message"] = (
                "✅ Оплата пройшла успішно!\n\n"
                f"📦 {pack}\n"
                f"🎮 Player ID: {player_id}\n"
                "⏳ Зачекайте на поповнення UC...\n"
                "Зазвичай це займає 1-5 хвилин.\n"
                "Ви отримаєте сповіщення коли UC будуть зараховані."
            )
    except Exception as e:
        logging.error(f"FazerCards order creation failed for {order_id}: {e}")
        result["message"] = (
            "✅ Оплата підтверджена!\n\n"
            f"📦 {pack}\n"
            "⚠️ Сталася помилка при автоматичній видачі UC.\n"
            "Адмін перевірить і нарахує UC вручну найближчим часом.\n"
            f"Помилка: {str(e)[:100]}"
        )

    return result


# ─── Webhook signature verification ──────────────────────────────────────────

def verify_fazercards_webhook(body: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature from FazerCards webhook."""
    if not secret:
        return True  # Dev mode — accept without verification

    expected = "sha256=" + hmac.new(
        key=secret.encode(),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    received = signature.strip()
    if not received.startswith("sha256="):
        received = "sha256=" + received

    return hmac.compare_digest(expected, received)


# ─── Notification helper ─────────────────────────────────────────────────────

def notify_telegram(bot_token: str, chat_id: int, text: str,
                    reply_markup: str = None) -> bool:
    """Send a message via Telegram Bot API (sync, urllib)."""
    if not bot_token:
        return False
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=data
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        logging.error(f"Telegram notify failed: {e}")
        return False


# ─── End of payment_service.py ───────────────────────────────────────────────
