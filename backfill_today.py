#!/usr/bin/env python3
"""
backfill_today.py - Backfill today's closed trades from Binance Futures API

Fetches trade fills via /fapi/v1/userTrades, reconstructs positions,
calculates actual P&L, and inserts into SQLite database.

Usage: python3 backfill_today.py
"""
import json, os, sys, time, hmac, hashlib, sqlite3
from datetime import datetime, timezone, timedelta
from urllib.request import Request, ProxyHandler, build_opener
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError

# ============================================================
# Paths
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_DIR = os.path.join(BASE_DIR, "memory")
ENV_PATH = os.path.join(os.path.dirname(BASE_DIR), ".env")
DB_PATH = os.path.join(MEMORY_DIR, "trades.db")

# ============================================================
# Load .env with explicit line parsing (no os.environ.get)
# ============================================================
BINANCE_API_KEY = ""
BINANCE_API_SECRET = ""

def load_env():
    global BINANCE_API_KEY, BINANCE_API_SECRET
    if not os.path.exists(ENV_PATH):
        print(f"ERROR: .env not found at {ENV_PATH}")
        sys.exit(1)
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("BINANCE_API_KEY="):
                BINANCE_API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("BINANCE_API_SECRET="):
                BINANCE_API_SECRET = line.split("=", 1)[1].strip().strip('"').strip("'")

load_env()

if not BINANCE_API_KEY or not BINANCE_API_SECRET:
    print("ERROR: BINANCE_API_KEY or BINANCE_API_SECRET missing in .env")
    sys.exit(1)

print(f"✅ API keys loaded (key={BINANCE_API_KEY[:8]}...)")

# ============================================================
# Proxy + API helpers
# ============================================================
PROXY_URL = "http://YOUR_PROXY:PORT"
proxy_handler = ProxyHandler({
    "http": PROXY_URL,
    "https": PROXY_URL,
})
opener = build_opener(proxy_handler)

BASE_URL = "https://fapi.binance.com"


def sign_request(params):
    query = urlencode(params)
    sig = hmac.new(
        BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    return query + "&signature=" + sig


def api_get(endpoint, params=None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    query = sign_request(params)
    url = f"{BASE_URL}{endpoint}?{query}"
    req = Request(url, headers={"X-MBX-APIKEY": BINANCE_API_KEY})
    try:
        with opener.open(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"  API GET {endpoint} HTTP {e.code}: {body[:200]}")
        return None
    except Exception as e:
        print(f"  API GET {endpoint} err: {e}")
        return None


def api_get_unsigned(url, timeout=15):
    """Fetch a public (unsigned) URL through proxy."""
    req = Request(url)
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"  fetch err {url[:80]}: {e}")
        return None


# ============================================================
# SQLite helpers
# ============================================================
def init_db():
    os.makedirs(MEMORY_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trade_fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT UNIQUE NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            quantity REAL NOT NULL,
            pnl_usd REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            commission REAL DEFAULT 0,
            entry_time TEXT,
            exit_time TEXT,
            hold_hours REAL DEFAULT 0,
            exit_type TEXT DEFAULT 'unknown',
            peak_pnl_pct REAL DEFAULT 0,
            source TEXT DEFAULT 'backfill',
            raw_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS algo_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algo_id TEXT UNIQUE,
            symbol TEXT NOT NULL,
            side TEXT,
            algo_type TEXT,
            trigger_price REAL,
            close_position TEXT,
            working_type TEXT,
            position_side TEXT,
            status TEXT,
            raw_json TEXT,
            fetched_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_fills_symbol ON trade_fills(symbol);
        CREATE INDEX IF NOT EXISTS idx_fills_trade_id ON trade_fills(trade_id);
    """)
    conn.commit()
    return conn


def insert_trade(conn, trade):
    try:
        conn.execute("""
            INSERT OR IGNORE INTO trade_fills
            (trade_id, symbol, direction, entry_price, exit_price, quantity,
             pnl_usd, pnl_pct, commission, entry_time, exit_time, hold_hours,
             exit_type, peak_pnl_pct, source, raw_json)
            VALUES (:trade_id, :symbol, :direction, :entry_price, :exit_price,
                    :quantity, :pnl_usd, :pnl_pct, :commission, :entry_time,
                    :exit_time, :hold_hours, :exit_type, :peak_pnl_pct,
                    :source, :raw_json)
        """, trade)
        return conn.total_changes
    except sqlite3.IntegrityError:
        return 0


def insert_algo(conn, algo):
    try:
        conn.execute("""
            INSERT OR REPLACE INTO algo_orders
            (algo_id, symbol, side, algo_type, trigger_price, close_position,
             working_type, position_side, status, raw_json, fetched_at)
            VALUES (:algo_id, :symbol, :side, :algo_type, :trigger_price,
                    :close_position, :working_type, :position_side, :status,
                    :raw_json, :fetched_at)
        """, algo)
        return conn.total_changes
    except Exception:
        return 0


# ============================================================
# Fetch today's trade fills
# ============================================================
# Symbols we know were traded today (from log)
KNOWN_SYMBOLS = [
    "XRPUSDT", "CATIUSDT", "EDGEUSDT", "ENAUSDT",
    "SIRENUSDT", "FHEUSDT", "STGUSDT", "ESPORTSUSDT",
]

# Also check these if they show up in open positions
EXTRA_SYMBOLS = [
    "COSUSDT",
]

def get_fetch_start_ms():
    """Get start time for fetching: 7 days back to capture full trade lifecycles."""
    now = datetime.now()
    start = now - timedelta(days=7)
    return int(start.timestamp() * 1000)


def fetch_user_trades(symbol, start_time_ms=None):
    """Fetch trade fills for a symbol."""
    params = {"symbol": symbol, "limit": 500}
    if start_time_ms:
        params["startTime"] = start_time_ms
    return api_get("/fapi/v1/userTrades", params) or []


def fetch_positions():
    """Fetch current open positions."""
    data = api_get("/fapi/v3/positionRisk")
    positions = []
    if data:
        for p in data:
            amt = float(p["positionAmt"])
            if amt != 0:
                positions.append({
                    "symbol": p["symbol"],
                    "direction": "LONG" if amt > 0 else "SHORT",
                    "amount": abs(amt),
                    "entry_price": float(p["entryPrice"]),
                    "mark_price": float(p["markPrice"]),
                    "unrealized_pnl": float(p.get("unRealizedProfit", 0)),
                })
    return positions


def fetch_algo_orders():
    """Fetch open algo orders (conditional/stop-loss)."""
    # /fapi/v1/openOrders returns all open orders including stop/TP
    # /fapi/v1/algoOrder requires a specific algoId, not a listing endpoint
    orders = []
    open_orders = api_get("/fapi/v1/openOrders")
    if open_orders and isinstance(open_orders, list):
        for o in open_orders:
            orders.append(o)
    # Also try /fapi/v1/conditionalAllOpenOrders (newer endpoint for algo)
    cond = api_get("/fapi/v1/conditionalAllOpenOrders")
    if cond and isinstance(cond, dict):
        for o in cond.get("orders", cond.get("data", [])):
            orders.append(o)
    return orders


# ============================================================
# Reconstruct positions from fills
# ============================================================
def reconstruct_closed_positions(all_fills):
    """
    Group fills by symbol+positionSide, match entry/exit fills,
    compute actual entry/exit prices and P&L for closed positions.

    In hedge mode:
      LONG: BUY+LONG = entry, SELL+LONG = exit
      SHORT: SELL+SHORT = entry, BUY+SHORT = exit
    """
    # Group by (symbol, positionSide)
    groups = {}
    for fill in all_fills:
        ps = fill.get("positionSide", "BOTH")
        key = (fill["symbol"], ps)
        if key not in groups:
            groups[key] = []
        groups[key].append(fill)

    closed = []
    for (symbol, pos_side), fills in groups.items():
        fills.sort(key=lambda f: f["time"])

        if pos_side == "LONG":
            entries = [f for f in fills if f["side"] == "BUY"]
            exits = [f for f in fills if f["side"] == "SELL"]
            direction = "LONG"
        elif pos_side == "SHORT":
            entries = [f for f in fills if f["side"] == "SELL"]
            exits = [f for f in fills if f["side"] == "BUY"]
            direction = "SHORT"
        else:
            # BOTH mode: infer from side sequence
            buys = [f for f in fills if f["side"] == "BUY"]
            sells = [f for f in fills if f["side"] == "SELL"]
            if not buys or not sells:
                continue
            first_buy = min(f["time"] for f in buys)
            first_sell = min(f["time"] for f in sells)
            if first_buy < first_sell:
                direction = "LONG"
                entries = buys
                exits = sells
            else:
                direction = "SHORT"
                entries = sells
                exits = buys

        if not entries or not exits:
            # Position still open (only entries, no exits)
            continue

        # Compute total entry qty and weighted avg entry price
        total_entry_qty = sum(float(f["qty"]) for f in entries)
        total_exit_qty = sum(float(f["qty"]) for f in exits)

        if total_entry_qty == 0:
            continue

        avg_entry = (
            sum(float(f["qty"]) * float(f["price"]) for f in entries)
            / total_entry_qty
        )

        # Only consider the matched exit quantity
        matched_exit_qty = min(total_entry_qty, total_exit_qty)
        if matched_exit_qty <= 0:
            continue

        # Weighted avg exit price for matched quantity
        remaining = matched_exit_qty
        exit_cost = 0.0
        for f in sorted(exits, key=lambda x: x["time"]):
            fq = float(f["qty"])
            take = min(fq, remaining)
            exit_cost += take * float(f["price"])
            remaining -= take
            if remaining <= 0:
                break

        avg_exit = exit_cost / matched_exit_qty if matched_exit_qty > 0 else 0

        # Commission
        total_commission = sum(float(f.get("commission", 0)) for f in entries + exits)

        # P&L calculation
        if direction == "LONG":
            pnl_per_unit = avg_exit - avg_entry
        else:
            pnl_per_unit = avg_entry - avg_exit

        pnl_usd = pnl_per_unit * matched_exit_qty - total_commission
        pnl_pct = pnl_per_unit / avg_entry if avg_entry > 0 else 0

        # Realized PnL from Binance (sum of all realizedPnl in matched fills)
        realized_pnl = sum(float(f.get("realizedPnl", 0)) for f in entries + exits)

        # Timestamps
        if direction == "LONG":
            entry_ts = min(f["time"] for f in entries)
            exit_ts = max(f["time"] for f in exits if f["time"] <= max(
                e["time"] for e in exits))
        else:
            entry_ts = min(f["time"] for f in entries)
            exit_ts = max(f["time"] for f in exits)

        # For SHORT, entry is when sells started, exit is when buys covered
        if direction == "SHORT":
            entry_ts = min(f["time"] for f in entries)
            exit_ts = max(f["time"] for f in exits)
        else:
            entry_ts = min(f["time"] for f in entries)
            exit_ts = max(f["time"] for f in exits)

        entry_dt = datetime.fromtimestamp(entry_ts / 1000)
        exit_dt = datetime.fromtimestamp(exit_ts / 1000)
        hold_hours = (exit_ts - entry_ts) / (1000 * 3600)

        # Use Binance realizedPnl if available (more accurate)
        if abs(realized_pnl) > 0.001:
            pnl_usd = realized_pnl - total_commission

        closed.append({
            "symbol": symbol,
            "direction": direction,
            "entry_price": round(avg_entry, 8),
            "exit_price": round(avg_exit, 8),
            "quantity": round(matched_exit_qty, 4),
            "pnl_usd": round(pnl_usd, 4),
            "pnl_pct": round(pnl_pct * 100, 4),
            "commission": round(total_commission, 4),
            "entry_time": entry_dt.isoformat(),
            "exit_time": exit_dt.isoformat(),
            "hold_hours": round(hold_hours, 2),
            "realized_pnl_binance": round(realized_pnl, 4),
            "fills_count": len(entries) + len(exits),
            "entry_fills": len(entries),
            "exit_fills": len(exits),
            "all_fills": entries + exits,
        })

    return closed


# ============================================================
# Also update JSON-based exit_memory for compatibility
# ============================================================
EXIT_MEMORY_PATH = os.path.join(MEMORY_DIR, "exit_memory.json")

def load_exit_memory():
    try:
        with open(EXIT_MEMORY_PATH) as f:
            return json.load(f)
    except:
        return []


def save_exit_memory(data):
    with open(EXIT_MEMORY_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def update_json_exit_memory(trade):
    """Update/replace placeholder entries in exit_memory.json."""
    data = load_exit_memory()
    changed = False
    symbol = trade["symbol"]
    direction = trade["direction"]

    for i, rec in enumerate(data):
        # Match placeholder records (0 entry/exit price) for the same symbol+direction
        if (rec.get("symbol") == symbol
                and rec.get("direction") == direction
                and rec.get("entry_price", 0) == 0
                and rec.get("exit_price", 0) == 0):
            # Replace with actual data
            data[i] = {
                "trade_id": f"BACKFILL_{symbol}_{direction}",
                "exit_time": trade["exit_time"],
                "symbol": symbol,
                "direction": direction,
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "pnl_pct": trade["pnl_pct"],
                "pnl_usd": trade["pnl_usd"],
                "exit_type": "backfill",
                "hold_hours": trade["hold_hours"],
                "peak_pnl_pct": 0,
                "loss_tags": [],
                "loss_tag_labels": [],
                "win": trade["pnl_pct"] > 0,
            }
            changed = True
            print(f"  📝 Updated JSON exit_memory: {symbol} {direction}")

    if changed:
        save_exit_memory(data)


# ============================================================
# Main
# ============================================================
def main():
    print("=" * 60)
    print("📥 Backfill Today's Trades")
    print("=" * 60)

    # Init DB
    conn = init_db()
    print(f"✅ SQLite database: {DB_PATH}")

    # Get today's start time
    fetch_start_ms = get_fetch_start_ms()
    start_dt = datetime.fromtimestamp(fetch_start_ms / 1000).strftime("%Y-%m-%d %H:%M")
    print(f"📅 Fetching trades from {start_dt} (7d window)")

    # 1. Fetch current positions (to know what's still held)
    print("\n📊 Fetching current positions...")
    positions = fetch_positions()
    open_symbols = set()
    if positions:
        for p in positions:
            emoji = "🟢" if p["unrealized_pnl"] > 0 else "🔴"
            print(f"  {emoji} {p['symbol']}({p['direction']}): "
                  f"entry={p['entry_price']:.6f} PnL={p['unrealized_pnl']:+.2f}U")
            open_symbols.add(p["symbol"])
    else:
        print("  (no open positions or API error)")

    # 2. Fetch algo orders (stop losses)
    print("\n🛡️ Fetching algo/open orders...")
    algo_orders = fetch_algo_orders()
    if algo_orders:
        now_iso = datetime.now().isoformat()
        for o in algo_orders:
            algo_id = str(o.get("algoId", o.get("orderId", "")))
            symbol = o.get("symbol", "")
            side = o.get("side", "")
            algo_type = o.get("algoType", o.get("type", ""))
            trigger = o.get("triggerPrice", o.get("stopPrice", "0"))
            wp = o.get("workingType", "")
            ps = o.get("positionSide", "")
            status = o.get("status", o.get("strategyStatus", ""))

            insert_algo(conn, {
                "algo_id": algo_id,
                "symbol": symbol,
                "side": side,
                "algo_type": algo_type,
                "trigger_price": float(trigger) if trigger else 0,
                "close_position": o.get("closePosition", ""),
                "working_type": wp,
                "position_side": ps,
                "status": status,
                "raw_json": json.dumps(o),
                "fetched_at": now_iso,
            })
            print(f"  🛡️ {symbol} {side} {algo_type} trigger={trigger} "
                  f"pos={ps} status={status}")
    else:
        print("  (no algo orders found)")
    conn.commit()

    # 3. Fetch trade fills for all known symbols
    all_symbols = list(set(KNOWN_SYMBOLS + EXTRA_SYMBOLS + list(open_symbols)))
    print(f"\n📥 Fetching fills for {len(all_symbols)} symbols...")
    all_fills = []
    for sym in all_symbols:
        fills = fetch_user_trades(sym, start_time_ms=fetch_start_ms)
        if fills:
            print(f"  {sym}: {len(fills)} fills")
            all_fills.extend(fills)
        time.sleep(0.15)  # Rate limit

    print(f"\n📊 Total fills: {len(all_fills)}")

    if not all_fills:
        print("⚠️ No trade fills found for today.")
        conn.close()
        return

    # 4. Reconstruct closed positions
    print("\n🔄 Reconstructing positions...")
    closed = reconstruct_closed_positions(all_fills)
    print(f"  Found {len(closed)} closed position(s)")

    # 5. Insert into SQLite and update JSON
    new_count = 0
    for trade in closed:
        sym = trade["symbol"]
        direction = trade["direction"]
        entry_time_short = trade["entry_time"][:19]
        exit_time_short = trade["exit_time"][:19]
        trade_id = f"BACKFILL_{sym}_{direction}_{trade['entry_time']}"

        pnl_pct = trade["pnl_pct"]
        pnl_usd = trade["pnl_usd"]
        emoji = "🟢" if pnl_pct > 0 else "🔴"

        print(f"\n  {emoji} {sym} {direction}")
        print(f"     Entry: {trade['entry_price']:.6f} @ {entry_time_short}")
        print(f"     Exit:  {trade['exit_price']:.6f} @ {exit_time_short}")
        print(f"     Qty:   {trade['quantity']:.4f}")
        print(f"     P&L:   {pnl_pct:+.2f}% ({pnl_usd:+.2f}U)")
        print(f"     Comm:  {trade['commission']:.4f}U")
        print(f"     Hold:  {trade['hold_hours']:.1f}h "
              f"({trade['entry_fills']}e + {trade['exit_fills']}x fills)")

        # Check if still open (no exit fills matched) - skip
        if trade["exit_fills"] == 0:
            print(f"     ⏭️ Skipping (no exit fills - position may still be open)")
            continue

        db_trade = {
            "trade_id": trade_id,
            "symbol": sym,
            "direction": direction,
            "entry_price": trade["entry_price"],
            "exit_price": trade["exit_price"],
            "quantity": trade["quantity"],
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "commission": trade["commission"],
            "entry_time": trade["entry_time"],
            "exit_time": trade["exit_time"],
            "hold_hours": trade["hold_hours"],
            "exit_type": "backfill",
            "peak_pnl_pct": max(pnl_pct, 0),
            "source": "backfill_today",
            "raw_json": json.dumps({
                "realized_pnl_binance": trade.get("realized_pnl_binance", 0),
                "fills_count": trade["fills_count"],
            }),
        }

        inserted = insert_trade(conn, db_trade)
        if inserted:
            new_count += 1
            print(f"     ✅ Inserted into SQLite")
        else:
            print(f"     ⏭️ Already in SQLite (skipped)")

        # Update JSON exit_memory
        update_json_exit_memory(trade)

    conn.commit()

    # 6. Summary
    print("\n" + "=" * 60)
    print(f"✅ Backfill complete: {new_count} new trades inserted")
    print(f"   Database: {DB_PATH}")

    # Show all trades in DB
    rows = conn.execute(
        "SELECT symbol, direction, pnl_pct, pnl_usd, exit_time "
        "FROM trade_fills ORDER BY exit_time DESC"
    ).fetchall()
    if rows:
        print(f"\n📋 All trades in database ({len(rows)}):")
        for r in rows:
            e = "🟢" if r[2] > 0 else "🔴"
            print(f"   {e} {r[0]} {r[1]}: {r[2]:+.2f}% ({r[3]:+.2f}U) @ {r[4][:19]}")

    # Show still-open positions
    if open_symbols:
        print(f"\n📌 Still open: {', '.join(sorted(open_symbols))}")

    # Show algo orders
    algo_rows = conn.execute(
        "SELECT symbol, side, algo_type, trigger_price, position_side, status "
        "FROM algo_orders ORDER BY fetched_at DESC"
    ).fetchall()
    if algo_rows:
        print(f"\n🛡️ Algo orders ({len(algo_rows)}):")
        for r in algo_rows:
            print(f"   {r[0]} {r[1]} {r[2]} trigger={r[3]} pos={r[4]} status={r[5]}")

    conn.close()
    print("\n🏁 Done.")


if __name__ == "__main__":
    main()
