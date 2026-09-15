#!/usr/bin/env python3
"""Test script for Monobank + FazerCards APIs and payment logic."""
import os
import sys
import json

# Load .env
from pathlib import Path
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

# Now import payment_service
import payment_service
from payment_service import (
    monobank, fazercards,
    get_offer_id_for_pack, extract_uc_amount,
    is_auto_deliverable, verify_and_deliver,
)

print("=" * 60)
print("  PAYMENT SYSTEM TEST")
print("=" * 60)

# ── 1. Config check ──
print("\n[1] Configuration:")
print(f"  MONOBANK_TOKEN: {'✅ set' if payment_service.MONOBANK_TOKEN else '❌ missing'}")
print(f"  MONOBANK_CARD:  {payment_service.MONOBANK_CARD}")
print(f"  MONOBANK_ACCOUNT_ID: {payment_service.MONOBANK_ACCOUNT_ID or '❌ missing'}")
print(f"  FAZERCARDS_API_KEY: {'✅ set' if payment_service.FAZERCARDS_API_KEY else '❌ missing'}")
print(f"  FAZERCARDS_BASE_URL: {payment_service.FAZERCARDS_BASE_URL}")
print(f"  FAZERCARDS_WEBHOOK_SECRET: {'✅ set' if payment_service.FAZERCARDS_WEBHOOK_SECRET else '❌ missing'}")
print(f"  FAZERCARDS_PUBG_CATEGORY: {payment_service.FAZERCARDS_PUBG_CATEGORY}")

# ── 2. UC → Offer mapping ──
print("\n[2] UC → FazerCards offer mapping:")
test_packs = [
    "60 UC - 49 грн",
    "325 UC - 219 грн",
    "660 UC - 429 грн",
    "1800 UC - 1149 грн",
    "3850 UC - 2299 грн",
    "8100 UC - 4599 грн",
    "30 UC - 25 грн",       # manual
    "Prime 1 місяць",       # auto
    "Prime Plus 3 місяці",  # auto
    "Набір Підйом 0",       # manual
]
for pack in test_packs:
    uc = extract_uc_amount(pack)
    offer = get_offer_id_for_pack(pack)
    auto = is_auto_deliverable(pack)
    status = f"→ offer={offer} (auto)" if offer else "→ manual delivery"
    print(f"  {pack:30s} UC={uc:5d} {status}")

# ── 3. Monobank API test ──
print("\n[3] Monobank API:")
if payment_service.MONOBANK_TOKEN:
    try:
        info = monobank.get_client_info()
        name = info.get("name", "?")
        accounts = info.get("accounts", [])
        print(f"  ✅ Client: {name}")
        print(f"  Accounts: {len(accounts)}")
        for acc in accounts[:3]:
            acc_id = acc.get("id", "?")
            balance = acc.get("balance", 0) / 100
            cc = acc.get("maskedPan", ["?"])[0] if acc.get("maskedPan") else "?"
            cur = acc.get("currencyCode", "?")
            print(f"    {cc} (id={acc_id[:8]}...) balance={balance:.2f} cur={cur}")

        # Check if our account ID is in the list
        our_id = payment_service.MONOBANK_ACCOUNT_ID
        if our_id:
            match = any(a.get("id") == our_id for a in accounts)
            print(f"  Account ID '{our_id}': {'✅ found in accounts' if match else '⚠️ not found!'}")

        # Test get_statement (last 15 min)
        print(f"\n  Testing statement (last 15 min)...")
        stmt = monobank.get_statement()
        print(f"  Statement: {len(stmt)} transactions in last 15 min")
        for tx in stmt[:5]:
            amt = tx.get("amount", 0) / 100
            desc = tx.get("description", "?")
            print(f"    {amt:+.2f} UAH — {desc}")
    except Exception as e:
        print(f"  ❌ Monobank API error: {e}")
else:
    print("  ⚠️ MONOBANK_TOKEN not set — skip")

# ── 4. FazerCards API test ──
print("\n[4] FazerCards API:")
if payment_service.FAZERCARDS_API_KEY:
    try:
        balance = fazercards.get_balance()
        print(f"  ✅ Balance: ${balance:.2f} USD")
    except Exception as e:
        print(f"  ❌ FazerCards API error: {e}")

    # Test order retrieval (should fail gracefully with fake ID)
    try:
        order = fazercards.get_order("test_nonexistent_order")
        print(f"  Get order (fake ID): {order}")
    except Exception as e:
        print(f"  Get order (fake ID): expected error — {str(e)[:100]}")
else:
    print("  ⚠️ FAZERCARDS_API_KEY not set — skip")

# ── 5. verify_and_deliver (dry run) ──
print("\n[5] verify_and_deliver (dry run with fake amount):")
try:
    result = verify_and_deliver(
        order_id="TEST_001",
        pack="325 UC - 219 грн",
        player_id="1234567890",
        amount_uah=999.99,  # unlikely to match anything
        chat_id=0,
        bot_token="",
    )
    print(f"  Result:")
    print(f"    verified: {result['verified']}")
    print(f"    delivered: {result['delivered']}")
    print(f"    message: {result['message'][:100]}")
    print(f"    monobank_tx_id: {result['monobank_tx_id']}")
    print(f"    fazercards_order_id: {result['fazercards_order_id']}")
    if not result['verified']:
        print("  → ✅ Expected: payment not found (no real payment of 999.99 UAH)")
except Exception as e:
    print(f"  ❌ Error: {e}")

print("\n" + "=" * 60)
print("  TEST COMPLETE")
print("=" * 60)
