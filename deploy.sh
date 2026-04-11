#!/usr/bin/env bash
# ============================================================
#  Railway MCP Deploy Script — claude-mcps project
#  Run AFTER `railway login`
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "=== Railway MCP Deployer (working from: $SCRIPT_DIR) ==="

# ── Step 1: Create the Railway project ──────────────────────
echo ""
echo ">>> Creating Railway project 'claude-mcps'..."
cd "$SCRIPT_DIR"
railway init --name claude-mcps
echo "Project created and linked to $SCRIPT_DIR"

# ── Step 2: Create all 4 services ────────────────────────────
echo ""
echo ">>> Creating services..."
railway add --service wolfram-alpha
railway add --service irctc
railway add --service zomato
railway add --service swiggy
echo "Services created."

# ── Step 3: Deploy each service from its subdirectory ────────
echo ""
echo ">>> Deploying wolfram-alpha..."
(cd "$SCRIPT_DIR/wolfram-alpha" && railway up --service wolfram-alpha --detach)

echo ">>> Deploying irctc..."
(cd "$SCRIPT_DIR/irctc" && railway up --service irctc --detach)

echo ">>> Deploying zomato..."
(cd "$SCRIPT_DIR/zomato" && railway up --service zomato --detach)

echo ">>> Deploying swiggy..."
(cd "$SCRIPT_DIR/swiggy" && railway up --service swiggy --detach)

echo ""
echo ">>> All 4 deploys queued. Generating public URLs (may take 30s for build)..."
sleep 5

# ── Step 4: Generate public domains ──────────────────────────
cd "$SCRIPT_DIR"
echo ""
WA_URL=$(railway domain --service wolfram-alpha --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('domain',''))" 2>/dev/null || echo "")
IRCTC_URL=$(railway domain --service irctc --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('domain',''))" 2>/dev/null || echo "")
ZOMATO_URL=$(railway domain --service zomato --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('domain',''))" 2>/dev/null || echo "")
SWIGGY_URL=$(railway domain --service swiggy --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('domain',''))" 2>/dev/null || echo "")

echo ""
echo "=================================================================="
echo "DEPLOYMENT COMPLETE"
echo ""
echo "Paste these URLs into claude.ai → Settings → Connectors:"
echo ""
echo "  Wolfram Alpha MCP : https://${WA_URL}/sse"
echo "  IRCTC MCP         : https://${IRCTC_URL}/sse"
echo "  Zomato MCP        : https://${ZOMATO_URL}/sse"
echo "  Swiggy MCP        : https://${SWIGGY_URL}/sse"
echo ""
echo "=================================================================="
echo "REQUIRED: Set your Wolfram Alpha API key:"
echo "  railway variable set WOLFRAM_APP_ID=YOUR_KEY --service wolfram-alpha"
echo ""
echo "Get free Wolfram key at: https://developer.wolframalpha.com/access"
echo ""
echo "OPTIONAL: Set RapidAPI key for live IRCTC PNR/train search:"
echo "  railway variable set RAPIDAPI_KEY=YOUR_KEY --service irctc"
echo ""
echo "Get free RapidAPI key at: https://rapidapi.com"
echo "Subscribe to: https://rapidapi.com/Adeel_25/api/irctc1 (500 free calls/month)"
echo "=================================================================="
