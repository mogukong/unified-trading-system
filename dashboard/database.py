"""
SQLite database for trade dashboard — complete schema
All fields from record_entry (35+) and record_exit (15)
"""
import sqlite3, os, json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id TEXT UNIQUE,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        strategy_version TEXT DEFAULT '',
        leverage INTEGER DEFAULT 10,
        margin_usd REAL DEFAULT 0,
        notional_usd REAL DEFAULT 0,
        entry_price REAL DEFAULT 0,
        exit_price REAL DEFAULT 0,
        quantity REAL DEFAULT 0,
        entry_time TEXT,
        exit_time TEXT,
        status TEXT DEFAULT 'open',
        total_score REAL DEFAULT 0,
        data_coverage_score REAL DEFAULT 0,
        professional_score REAL DEFAULT 0,
        market_quality_score REAL DEFAULT 0,
        entry_timing_score REAL DEFAULT 0,
        funding_flow_score REAL DEFAULT 0,
        execution_risk_score REAL DEFAULT 0,
        resonance_count INTEGER DEFAULT 0,
        candidate_source TEXT DEFAULT '',
        entry_reasons TEXT DEFAULT '[]',
        factors TEXT DEFAULT '{}',
        estimated_ev REAL DEFAULT 0,
        estimated_win_rate REAL DEFAULT 0,
        estimated_rr REAL DEFAULT 0,
        position_scale REAL DEFAULT 1.0,
        factor_attribution TEXT DEFAULT '',
        pnl_usd REAL DEFAULT 0,
        pnl_pct REAL DEFAULT 0,
        exit_type TEXT DEFAULT '',
        hold_hours REAL DEFAULT 0,
        peak_pnl_pct REAL DEFAULT 0,
        loss_tags TEXT DEFAULT '[]',
        loss_tag_labels TEXT DEFAULT '[]',
        win INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS tag_definitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        category TEXT DEFAULT 'custom',
        color TEXT DEFAULT '#6c757d',
        icon TEXT DEFAULT '',
        description TEXT DEFAULT ''
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS trade_tags (
        trade_id INTEGER REFERENCES trades(id) ON DELETE CASCADE,
        tag_id INTEGER REFERENCES tag_definitions(id) ON DELETE CASCADE,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_id, tag_id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS reflections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER REFERENCES trades(id) ON DELETE CASCADE,
        content TEXT NOT NULL,
        rating INTEGER DEFAULT 3,
        lesson TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sync_time TEXT DEFAULT (datetime('now')),
        trades_synced INTEGER DEFAULT 0,
        exits_synced INTEGER DEFAULT 0
    )""")

    c.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_trades_direction ON trades(direction)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time)")

    # Seed default tags
    default_tags = [
        ("quick_loss", "speed", "#ff1744", "⚡", "快速亏损(<4h)"),
        ("small_loss", "speed", "#ff9100", "📉", "小额亏损(<3%)"),
        ("timeout_loss", "speed", "#ffea00", "⏰", "超时亏损(>24h)"),
        ("chase_high", "pattern", "#d500f9", "🔺", "追高回撤"),
        ("false_breakout", "pattern", "#651fff", "💥", "假突破"),
        ("funding_headwind", "market", "#2979ff", "💸", "资金费逆风"),
        ("smart_money_headwind", "market", "#00bfa5", "🏦", "聪明钱逆风"),
        ("oi_divergence", "market", "#76ff03", "📊", "OI背离"),
        ("kronos_headwind", "external", "#ff6d00", "🌀", "Kronos逆风"),
        ("trend_against", "market", "#ff1744", "📉", "趋势逆风"),
        ("volatility_spike", "market", "#f50057", "🌊", "波动暴增"),
        ("liquidity_trap", "market", "#6200ea", "🕳️", "流动性陷阱"),
        ("weak_direction", "pattern", "#ffab00", "🧭", "弱方向亏损"),
        ("prediction_market_headwind", "external", "#dd2c00", "🔮", "预测市场逆风"),
        ("low_coverage_loss", "data", "#78909c", "📊", "低数据覆盖亏损"),
        ("data_gap_loss", "data", "#546e7a", "🕳️", "数据缺口亏损"),
        ("high_risk_execution", "execution", "#d50000", "⚠️", "执行风险过高"),
        ("slippage_loss", "execution", "#c51162", "💧", "滑点亏损"),
        ("crowd_squeeze", "market", "#aa00ff", "🏃", "拥挤踩踏"),
    ]
    for name, cat, color, icon, desc in default_tags:
        try:
            c.execute("INSERT OR IGNORE INTO tag_definitions(name,category,color,icon,description) VALUES(?,?,?,?,?)",
                      (name, cat, color, icon, desc))
        except Exception:
            pass

    conn.commit()
    conn.close()

def sync_from_json():
    """Sync trade_memory.json + exit_memory.json -> SQLite"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    trade_file = os.path.join(base, "memory", "trade_memory.json")
    exit_file = os.path.join(base, "memory", "exit_memory.json")

    trades_json = []
    exits_json = []
    try:
        with open(trade_file) as f: trades_json = json.load(f)
    except Exception as e: print(f"⚠️ 读取失败: {e}")
    try:
        with open(exit_file) as f: exits_json = json.load(f)
    except Exception as e: print(f"⚠️ 读取失败: {e}")

    conn = get_db()
    try:
        c = conn.cursor()
        synced_t = 0
        synced_e = 0

        # Sync open trades
        for t in trades_json:
            tid = t.get("trade_id", "")
            factors = t.get("factors", {})
            reasons = t.get("entry_reasons", [])
            if isinstance(reasons, str): reasons = [reasons]
            c.execute("SELECT id FROM trades WHERE trade_id=?", (tid,))
            row = c.fetchone()
            if row:
                c.execute("""UPDATE trades SET symbol=?, direction=?, strategy_version=?, leverage=?,
                    margin_usd=?, notional_usd=?, entry_price=?, quantity=?, entry_time=?,
                    total_score=?, data_coverage_score=?, professional_score=?, market_quality_score=?,
                    entry_timing_score=?, funding_flow_score=?, execution_risk_score=?, resonance_count=?,
                    candidate_source=?, entry_reasons=?, factors=?, estimated_ev=?, estimated_win_rate=?,
                    estimated_rr=?, position_scale=?, factor_attribution=?, status='open', updated_at=datetime('now')
                    WHERE trade_id=?""",
                    (t.get("symbol"), t.get("direction"), t.get("strategy_version",""),
                     t.get("leverage",10), t.get("margin_usd",0), t.get("notional_usd",0),
                     t.get("entry_price",0), t.get("quantity",0), t.get("timestamp",""),
                     t.get("total_score",0), t.get("data_coverage_score",0), t.get("professional_score",0),
                     t.get("market_quality_score",0), t.get("entry_timing_score",0), t.get("funding_flow_score",0),
                     t.get("execution_risk_score",0), t.get("resonance_count",0), t.get("candidate_source",""),
                     json.dumps(reasons, ensure_ascii=False), json.dumps(factors, ensure_ascii=False),
                     t.get("estimated_ev",0), t.get("estimated_win_rate",0), t.get("estimated_rr",0),
                     t.get("position_scale",1.0), t.get("factor_attribution",""), tid))
            else:
                c.execute("""INSERT INTO trades(trade_id, symbol, direction, strategy_version, leverage,
                    margin_usd, notional_usd, entry_price, quantity, entry_time, status,
                    total_score, data_coverage_score, professional_score, market_quality_score,
                    entry_timing_score, funding_flow_score, execution_risk_score, resonance_count,
                    candidate_source, entry_reasons, factors, estimated_ev, estimated_win_rate,
                    estimated_rr, position_scale, factor_attribution)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (tid, t.get("symbol"), t.get("direction"), t.get("strategy_version",""),
                     t.get("leverage",10), t.get("margin_usd",0), t.get("notional_usd",0),
                     t.get("entry_price",0), t.get("quantity",0), t.get("timestamp",""), "open",
                     t.get("total_score",0), t.get("data_coverage_score",0), t.get("professional_score",0),
                     t.get("market_quality_score",0), t.get("entry_timing_score",0), t.get("funding_flow_score",0),
                     t.get("execution_risk_score",0), t.get("resonance_count",0), t.get("candidate_source",""),
                     json.dumps(reasons, ensure_ascii=False), json.dumps(factors, ensure_ascii=False),
                     t.get("estimated_ev",0), t.get("estimated_win_rate",0), t.get("estimated_rr",0),
                     t.get("position_scale",1.0), t.get("factor_attribution","")))
            synced_t += 1

        # Sync exits
        for e in exits_json:
            tid = e.get("trade_id", "")
            tags = e.get("loss_tags", e.get("tags", []))
            tag_labels = e.get("loss_tag_labels", [])
            c.execute("SELECT id FROM trades WHERE trade_id=?", (tid,))
            row = c.fetchone()
            if row:
                c.execute("""UPDATE trades SET exit_price=?, exit_time=?, pnl_usd=?, pnl_pct=?,
                    exit_type=?, hold_hours=?, peak_pnl_pct=?, loss_tags=?, loss_tag_labels=?,
                    win=?, status='closed', updated_at=datetime('now') WHERE trade_id=?""",
                    (e.get("exit_price",0), e.get("exit_time",""), e.get("pnl_usd",0),
                     e.get("pnl_pct",0), e.get("exit_type",""), e.get("hold_hours",0),
                     e.get("peak_pnl_pct",0), json.dumps(tags, ensure_ascii=False),
                     json.dumps(tag_labels, ensure_ascii=False), 1 if e.get("win") else 0, tid))
            else:
                # Exit without matching trade — create a minimal trade record
                c.execute("""INSERT INTO trades(trade_id, symbol, direction, entry_price, exit_price,
                    exit_time, pnl_usd, pnl_pct, exit_type, hold_hours, peak_pnl_pct,
                    loss_tags, loss_tag_labels, win, status)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (tid, e.get("symbol","?"), e.get("direction","?"), e.get("entry_price",0),
                     e.get("exit_price",0), e.get("exit_time",""), e.get("pnl_usd",0),
                     e.get("pnl_pct",0), e.get("exit_type",""), e.get("hold_hours",0),
                     e.get("peak_pnl_pct",0), json.dumps(tags, ensure_ascii=False),
                     json.dumps(tag_labels, ensure_ascii=False), 1 if e.get("win") else 0, "closed"))
            synced_e += 1

        c.execute("INSERT INTO sync_log(trades_synced, exits_synced) VALUES(?,?)", (synced_t, synced_e))
        conn.commit()
        return {"trades": synced_t, "exits": synced_e}
    finally:
        conn.close()

# Auto-init
init_db()
