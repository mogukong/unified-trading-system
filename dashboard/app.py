"""
Trade Dashboard — Flask app with full API
"""
import json, os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for
from database import get_db, sync_from_json

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

def row2dict(row):
    if row is None: return {}
    return dict(row)

def rows2dicts(rows):
    return [dict(r) for r in rows]

# ===== Page Routes =====

@app.route("/")
def index():
    db = get_db()
    try:
        stats = db.execute("""
            SELECT COUNT(*) as total,
                SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN win=0 AND status='closed' THEN 1 ELSE 0 END) as losses,
                SUM(pnl_usd) as total_pnl,
                AVG(CASE WHEN status='closed' THEN pnl_pct END) as avg_pct,
                MAX(pnl_usd) as best_trade,
                MIN(pnl_usd) as worst_trade,
                AVG(CASE WHEN win=1 AND status='closed' THEN pnl_pct END) as avg_win_pct,
                AVG(CASE WHEN win=0 AND status='closed' THEN pnl_pct END) as avg_loss_pct
            FROM trades WHERE status='closed'
        """).fetchone()
        
        recent = db.execute("""
            SELECT id, trade_id, symbol, direction, entry_price, exit_price,
                pnl_usd, pnl_pct, total_score, exit_type, hold_hours, win, entry_time, loss_tags
            FROM trades WHERE status='closed' ORDER BY exit_time DESC LIMIT 20
        """).fetchall()
        
        monthly = db.execute("""
            SELECT strftime('%Y-%m', exit_time) as month,
                SUM(pnl_usd) as pnl, COUNT(*) as trades,
                SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as wins
            FROM trades WHERE status='closed' AND exit_time != ''
            GROUP BY month ORDER BY month
        """).fetchall()
        
        by_direction = db.execute("""
            SELECT direction, COUNT(*) as trades, SUM(pnl_usd) as pnl,
                SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as wins
            FROM trades WHERE status='closed' GROUP BY direction
        """).fetchall()
        
        top_symbols = db.execute("""
            SELECT symbol, COUNT(*) as trades, SUM(pnl_usd) as pnl,
                ROUND(AVG(pnl_pct),1) as avg_pct,
                SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as wins
            FROM trades WHERE status='closed' GROUP BY symbol ORDER BY pnl DESC LIMIT 10
        """).fetchall()
        
        # Chart data
        closed = db.execute("""
            SELECT exit_time, pnl_usd FROM trades WHERE status='closed' AND exit_time != ''
            ORDER BY exit_time
        """).fetchall()
        cumulative = []
        running = 0
        for r in closed:
            running += (r["pnl_usd"] or 0)
            cumulative.append({"date": r["exit_time"][:10], "pnl": round(running, 2)})
        
        # By hour
        by_hour = db.execute("""
            SELECT CAST(strftime('%H', exit_time) AS INTEGER) as hour,
                SUM(pnl_usd) as pnl, COUNT(*) as trades
            FROM trades WHERE status='closed' AND exit_time != ''
            GROUP BY hour ORDER BY hour
        """).fetchall()
        
        # Exit types
        exit_types = db.execute("""
            SELECT exit_type, COUNT(*) as count, SUM(pnl_usd) as pnl
            FROM trades WHERE status='closed' AND exit_type != ''
            GROUP BY exit_type ORDER BY count DESC
        """).fetchall()
        
        # Loss tags
        loss_tag_rows = db.execute("""
            SELECT loss_tags FROM trades WHERE status='closed' AND loss_tags != '[]' AND loss_tags != ''
        """).fetchall()
        tag_counts = {}
        for row in loss_tag_rows:
            try:
                tags = json.loads(row["loss_tags"]) if isinstance(row["loss_tags"], str) else row["loss_tags"]
                for t in tags:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
            except Exception: pass
        loss_tags_sorted = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        
        s = row2dict(stats)
        s["win_rate"] = round(s["wins"]/s["total"]*100, 1) if s["total"] else 0
        return render_template("index.html", stats=s, recent=rows2dicts(recent),
            monthly=rows2dicts(monthly), by_direction=rows2dicts(by_direction),
            top_symbols=rows2dicts(top_symbols), cumulative=cumulative,
            by_hour=rows2dicts(by_hour), exit_types=rows2dicts(exit_types), loss_tags=loss_tags_sorted)
    finally:
        db.close()

@app.route("/trades")
def trades():
    db = get_db()
    try:
        where = ["1=1"]
        params = []
        
        sym = request.args.get("symbol", "")
        if sym: where.append("symbol LIKE ?"); params.append(f"%{sym}%")
        direction = request.args.get("direction", "")
        if direction: where.append("direction=?"); params.append(direction)
        exit_type = request.args.get("exit_type", "")
        if exit_type: where.append("exit_type=?"); params.append(exit_type)
        min_score = request.args.get("min_score", type=float)
        if min_score: where.append("total_score>=?"); params.append(min_score)
        result = request.args.get("result", "")
        if result == "win": where.append("win=1")
        elif result == "loss": where.append("win=0 AND status='closed'")
        
        page = request.args.get("page", 1, type=int)
        per_page = 50
        offset = (page - 1) * per_page
        
        total = db.execute(f"SELECT COUNT(*) as c FROM trades WHERE {' AND '.join(where)}", params).fetchone()["c"]
        rows = db.execute(f"""SELECT id, trade_id, symbol, direction, entry_price, exit_price,
            pnl_usd, pnl_pct, total_score, exit_type, hold_hours, win, entry_time, exit_time,
            loss_tags, leverage, margin_usd FROM trades WHERE {' AND '.join(where)}
            ORDER BY entry_time DESC LIMIT ? OFFSET ?""", params + [per_page, offset]).fetchall()
        
        # Get reflection counts
        ref_counts = {}
        for r in rows:
            cnt = db.execute("SELECT COUNT(*) as c FROM reflections WHERE trade_id=?", (r["id"],)).fetchone()["c"]
            ref_counts[r["id"]] = cnt
        
        filters = {"symbol": sym, "direction": direction, "exit_type": exit_type, "result": result}
        return render_template("trades.html", trades=rows2dicts(rows), ref_counts=ref_counts,
            page=page, per_page=per_page, total=total, request=request, filters=filters)
    finally:
        db.close()

@app.route("/trade/<int:tid>")
def trade_detail(tid):
    db = get_db()
    try:
        trade = db.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone()
        if not trade: return redirect(url_for("trades"))
        
        # Get tags
        tags = db.execute("""
            SELECT t.id, t.name, t.category, t.color, t.icon FROM trade_tags tt
            JOIN tag_definitions t ON tt.tag_id=t.id WHERE tt.trade_id=?
        """, (tid,)).fetchall()
        
        # Get reflections
        reflections = db.execute("SELECT * FROM reflections WHERE trade_id=? ORDER BY created_at DESC", (tid,)).fetchall()
        
        # All available tags
        all_tags = db.execute("SELECT * FROM tag_definitions ORDER BY category, name").fetchall()
        
        # Trade factors
        factors = {}
        try: factors = json.loads(trade["factors"]) if trade["factors"] else {}
        except Exception: pass
        reasons = []
        try: reasons = json.loads(trade["entry_reasons"]) if trade["entry_reasons"] else []
        except Exception: pass
        loss_tags = []
        try: loss_tags = json.loads(trade["loss_tags"]) if trade["loss_tags"] else []
        except Exception: pass
        
        return render_template("trade_detail.html", trade=row2dict(trade), tags=rows2dicts(tags),
            reflections=rows2dicts(reflections), all_tags=rows2dicts(all_tags), factors=factors,
            reasons=reasons, loss_tags=loss_tags)
    finally:
        db.close()

@app.route("/tags")
def tags_page():
    db = get_db()
    try:
        tags = db.execute("""
            SELECT t.*, COUNT(tt.trade_id) as trade_count,
                SUM(CASE WHEN tr.win=1 THEN 1 ELSE 0 END) as wins,
                SUM(tr.pnl_usd) as total_pnl,
                AVG(tr.pnl_usd) as avg_pnl
            FROM tag_definitions t
            LEFT JOIN trade_tags tt ON t.id=tt.tag_id
            LEFT JOIN trades tr ON tt.trade_id=tr.id AND tr.status='closed'
            GROUP BY t.id ORDER BY t.category, t.name
        """).fetchall()
        tags_list = rows2dicts(tags)
        tags_by_cat = {}
        for t in tags_list:
            cat = t.get("category", "other")
            if cat not in tags_by_cat: tags_by_cat[cat] = []
            tags_by_cat[cat].append(t)
        return render_template("tags.html", tags=tags_list, tags_by_category=tags_by_cat)
    finally:
        db.close()

@app.route("/analytics")
def analytics():
    db = get_db()
    try:
        closed = db.execute("SELECT * FROM trades WHERE status='closed'").fetchall()
        
        # Profit factor
        gross_profit = sum(r["pnl_usd"] for r in closed if (r["pnl_usd"] or 0) > 0)
        gross_loss = abs(sum(r["pnl_usd"] for r in closed if (r["pnl_usd"] or 0) < 0))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0
        
        # Max drawdown
        running = 0; peak = 0; max_dd = 0
        for r in sorted(closed, key=lambda x: x["exit_time"] or ""):
            running += (r["pnl_usd"] or 0)
            peak = max(peak, running)
            dd = peak - running
            max_dd = max(max_dd, dd)
        
        # Score distribution
        score_dist = db.execute("""
            SELECT CASE
                WHEN total_score < 50 THEN '0-50'
                WHEN total_score < 70 THEN '50-70'
                WHEN total_score < 90 THEN '70-90'
                WHEN total_score < 110 THEN '90-110'
                ELSE '110+'
            END as band, COUNT(*) as trades, SUM(pnl_usd) as pnl,
            SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as wins
            FROM trades WHERE status='closed' AND total_score > 0
            GROUP BY band ORDER BY band
        """).fetchall()
        
        # By weekday
        by_weekday = db.execute("""
            SELECT CAST(strftime('%w', exit_time) AS INTEGER) as dow,
                SUM(pnl_usd) as pnl, COUNT(*) as trades
            FROM trades WHERE status='closed' AND exit_time != ''
            GROUP BY dow ORDER BY dow
        """).fetchall()
        
        # By hour
        by_hour = db.execute("""
            SELECT CAST(strftime('%H', exit_time) AS INTEGER) as hour,
                SUM(pnl_usd) as pnl, COUNT(*) as trades
            FROM trades WHERE status='closed' AND exit_time != ''
            GROUP BY hour ORDER BY hour
        """).fetchall()
        
        # Exit types
        exit_types = db.execute("""
            SELECT exit_type, COUNT(*) as count, SUM(pnl_usd) as pnl,
                AVG(hold_hours) as avg_hold
            FROM trades WHERE status='closed' AND exit_type != ''
            GROUP BY exit_type ORDER BY count DESC
        """).fetchall()
        
        # Loss tags
        loss_tag_rows = db.execute("""
            SELECT loss_tags, pnl_usd FROM trades WHERE status='closed' AND loss_tags != '[]'
        """).fetchall()
        tag_stats = {}
        for row in loss_tag_rows:
            try:
                tags = json.loads(row["loss_tags"]) if isinstance(row["loss_tags"], str) else []
                for t in tags:
                    if t not in tag_stats: tag_stats[t] = {"count": 0, "pnl": 0}
                    tag_stats[t]["count"] += 1
                    tag_stats[t]["pnl"] += (row["pnl_usd"] or 0)
            except Exception: pass
        loss_tags_sorted = sorted(tag_stats.items(), key=lambda x: x[1]["count"], reverse=True)
        
        # Score vs PnL scatter
        scatter = db.execute("""
            SELECT total_score, pnl_usd, pnl_pct, symbol, direction
            FROM trades WHERE status='closed' AND total_score > 0
        """).fetchall()
        
        # Tag performance
        tag_perf = db.execute("""
            SELECT td.name, td.color, COUNT(tt.trade_id) as trades,
                SUM(CASE WHEN tr.win=1 THEN 1 ELSE 0 END) as wins,
                SUM(tr.pnl_usd) as total_pnl, AVG(tr.pnl_usd) as avg_pnl
            FROM trade_tags tt
            JOIN tag_definitions td ON tt.tag_id=td.id
            JOIN trades tr ON tt.trade_id=tr.id AND tr.status='closed'
            GROUP BY td.id ORDER BY total_pnl
        """).fetchall()
        
        # Win/loss stats
        wins = [r for r in closed if r["win"]]
        losses = [r for r in closed if not r["win"]]
        avg_win = sum(r["pnl_usd"] for r in wins) / len(wins) if wins else 0
        avg_loss = sum(r["pnl_usd"] for r in losses) / len(losses) if losses else 0
        avg_hold = sum(r["hold_hours"] or 0 for r in closed) / len(closed) if closed else 0
        
        analytics_stats = {"profit_factor": profit_factor, "max_dd": round(max_dd, 2),
            "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
            "avg_hold": round(avg_hold, 1), "total_trades": len(closed)}
        return render_template("analytics.html",
            stats=analytics_stats, profit_factor=profit_factor, max_dd=round(max_dd, 2),
            avg_win=round(avg_win, 2), avg_loss=round(avg_loss, 2),
            avg_hold=round(avg_hold, 1), score_dist=rows2dicts(score_dist),
            by_weekday=rows2dicts(by_weekday), by_hour=rows2dicts(by_hour), exit_types=rows2dicts(exit_types),
            loss_tags=loss_tags_sorted, scatter=rows2dicts(scatter), tag_perf=rows2dicts(tag_perf),
            total_trades=len(closed))
    finally:
        db.close()

@app.route("/notes")
def notes():
    db = get_db()
    try:
        rating = request.args.get("rating", type=int)
        search = request.args.get("q", "")
        
        where = ["1=1"]
        params = []
        if rating: where.append("r.rating=?"); params.append(rating)
        if search: where.append("r.content LIKE ?"); params.append(f"%{search}%")
        
        reflections = db.execute(f"""
            SELECT r.*, t.symbol, t.direction, t.pnl_usd, t.pnl_pct, t.total_score, t.exit_type
            FROM reflections r JOIN trades t ON r.trade_id=t.id
            WHERE {' AND '.join(where)} ORDER BY r.created_at DESC LIMIT 100
        """, params).fetchall()
        
        stats = db.execute("""
            SELECT COUNT(*) as total, AVG(rating) as avg_rating,
                SUM(CASE WHEN rating>=4 THEN 1 ELSE 0 END) as positive
            FROM reflections
        """).fetchone()
        return render_template("notes.html", reflections=rows2dicts(reflections), stats=row2dict(stats), request=request)
    finally:
        db.close()

# ===== API Routes =====

@app.route("/api/sync", methods=["POST"])
def api_sync():
    result = sync_from_json()
    return jsonify(result)

@app.route("/api/trade/<int:tid>/tag", methods=["POST"])
def api_add_tag(tid):
    data = request.get_json()
    tag_id = data.get("tag_id")
    db = get_db()
    try:
        db.execute("INSERT OR IGNORE INTO trade_tags(trade_id, tag_id) VALUES(?,?)", (tid, tag_id))
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        db.close()

@app.route("/api/trade/<int:tid>/tag/<int:tag_id>", methods=["DELETE"])
def api_remove_tag(tid, tag_id):
    db = get_db()
    try:
        db.execute("DELETE FROM trade_tags WHERE trade_id=? AND tag_id=?", (tid, tag_id))
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()

@app.route("/api/tag", methods=["POST"])
def api_create_tag():
    data = request.get_json()
    db = get_db()
    try:
        db.execute("INSERT INTO tag_definitions(name,category,color,icon,description) VALUES(?,?,?,?,?)",
            (data["name"], data.get("category","custom"), data.get("color","#6c757d"),
             data.get("icon",""), data.get("description","")))
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        db.close()

@app.route("/api/tag/<int:tag_id>", methods=["DELETE"])
def api_delete_tag(tag_id):
    db = get_db()
    try:
        db.execute("DELETE FROM tag_definitions WHERE id=?", (tag_id,))
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()

@app.route("/api/trade/<int:tid>/reflection", methods=["POST"])
def api_add_reflection(tid):
    data = request.get_json()
    db = get_db()
    try:
        db.execute("INSERT INTO reflections(trade_id,content,rating,lesson) VALUES(?,?,?,?)",
            (tid, data["content"], data.get("rating",3), data.get("lesson","")))
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()

@app.route("/api/reflection/<int:rid>", methods=["PUT"])
def api_update_reflection(rid):
    data = request.get_json()
    db = get_db()
    try:
        db.execute("UPDATE reflections SET content=?,rating=?,lesson=?,updated_at=datetime('now') WHERE id=?",
            (data["content"], data.get("rating",3), data.get("lesson",""), rid))
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()

@app.route("/api/reflection/<int:rid>", methods=["DELETE"])
def api_delete_reflection(rid):
    db = get_db()
    try:
        db.execute("DELETE FROM reflections WHERE id=?", (rid,))
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()

@app.route("/api/stats")
def api_stats():
    db = get_db()
    try:
        # Read tags from trades.loss_tags JSON column
        rows = db.execute("""
            SELECT loss_tags, pnl_usd, win FROM trades 
            WHERE status='closed' AND loss_tags IS NOT NULL AND loss_tags != '[]'
        """).fetchall()
        
        tag_stats = {}
        for row in rows:
            try:
                tags = json.loads(row["loss_tags"])
                for tag in tags:
                    if tag not in tag_stats:
                        tag_stats[tag] = {"name": tag, "trades": 0, "wins": 0, "pnl": 0}
                    tag_stats[tag]["trades"] += 1
                    if row["win"]:
                        tag_stats[tag]["wins"] += 1
                    tag_stats[tag]["pnl"] += row["pnl_usd"] or 0
            except Exception:
                pass
        
        by_tag = sorted(tag_stats.values(), key=lambda x: x["trades"], reverse=True)
        return jsonify({"by_tag": by_tag})
    finally:
        db.close()

@app.route("/api/trades/chart")
def api_chart():
    days = request.args.get("days", 30, type=int)
    db = get_db()
    try:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = db.execute("""
            SELECT exit_time, pnl_usd FROM trades
            WHERE status='closed' AND exit_time != '' AND exit_time >= ?
            ORDER BY exit_time
        """, (cutoff,)).fetchall()
        cumulative = []; running = 0
        for r in rows:
            running += (r["pnl_usd"] or 0)
            cumulative.append({"date": r["exit_time"][:10], "value": round(running, 2)})
        return jsonify({"cumulative": cumulative})
    finally:
        db.close()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
