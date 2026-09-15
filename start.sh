#!/bin/bash
# ============================================================
#  Nezuko UC Bot — local startup script
#  Starts Cloudflare tunnel + bot in one command
# ============================================================
set -e
cd "$(dirname "$0")"

PORT=8080
ENV_FILE=".env"

echo "=== Nezuko UC Bot — Startup ==="
echo ""

# ── 1. Check dependencies ──
if ! command -v cloudflared &>/dev/null; then
    echo "ERROR: cloudflared not installed. Install: brew install cloudflared"
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

# ── 2. Install Python deps if needed ──
echo "[1/4] Checking Python dependencies..."
pip3 install -q -r requirements.txt 2>/dev/null || pip install -q -r requirements.txt 2>/dev/null || true
echo "      Done"

# ── 3. Start Cloudflare tunnel ──
echo "[2/4] Starting Cloudflare tunnel (port $PORT)..."
TUNNEL_LOG="/tmp/nezuko_cf_tunnel.log"
rm -f "$TUNNEL_LOG"
cloudflared tunnel --url "http://localhost:$PORT" > "$TUNNEL_LOG" 2>&1 &
CF_PID=$!
echo "      Tunnel PID: $CF_PID"

# Wait for URL to appear in logs
echo "      Waiting for tunnel URL..."
TUNNEL_URL=""
for i in $(seq 1 15); do
    sleep 1
    TUNNEL_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)
    if [ -n "$TUNNEL_URL" ]; then
        break
    fi
done

if [ -n "$TUNNEL_URL" ]; then
    echo "      Tunnel URL: $TUNNEL_URL"
    # Update WEBAPP_URL in .env
    if grep -q "^WEBAPP_URL=" "$ENV_FILE"; then
        sed -i '' "s|^WEBAPP_URL=.*|WEBAPP_URL=$TUNNEL_URL/app|" "$ENV_FILE"
    else
        echo "WEBAPP_URL=$TUNNEL_URL/app" >> "$ENV_FILE"
    fi
    echo "      Updated WEBAPP_URL in .env"
else
    echo "      WARNING: Could not capture tunnel URL. Using existing .env"
    echo "      Check $TUNNEL_LOG manually"
fi

echo ""
echo "[3/4] Starting bot..."
echo ""
# ── 4. Start bot ──
python3 run.py
BOT_PID=$!

# ── Cleanup on exit ──
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $CF_PID 2>/dev/null || true
    kill $BOT_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

# Wait for bot to exit
wait $BOT_PID 2>/dev/null
cleanup
