"""
推送中心 - 统一的Telegram通知
"""
import json, os, time
from datetime import datetime
from urllib.request import urlopen, Request, ProxyHandler, build_opener
from urllib.parse import urlencode

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TG_BOT_TOKEN = ""
TG_CHAT_ID = ""
TG_PROXY = "http://YOUR_PROXY:PORT"


def _init():
    global TG_BOT_TOKEN, TG_CHAT_ID, TG_PROXY
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        env_path = os.path.join(os.path.dirname(BASE_DIR), ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    TG_BOT_TOKEN = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("TG_CHAT_ID="):
                    TG_CHAT_ID = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("TG_PROXY="):
                    TG_PROXY = line.split("=", 1)[1].strip().strip('"').strip("'")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        pass
    global opener
    proxy_handler = ProxyHandler({'http': TG_PROXY, 'https': TG_PROXY})
    opener = build_opener(proxy_handler)


_init()

# 代理设置
proxy_handler = ProxyHandler({
    'http': TG_PROXY,
    'https': TG_PROXY,
})
opener = build_opener(proxy_handler)


def send(text, silent=False):
    """发送TG消息"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = urlencode({
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": silent,
        }).encode()
        req = Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with opener.open(req, timeout=10) as r:
            return True
    except Exception as e:
        return False


def notify_trade_open(symbol, direction, price, score, margin, leverage, reasons=None, pattern=None, stop_loss_pct=None):
    """开仓通知"""
    arrow = "🟢" if direction == "LONG" else "🔴"
    mode = "做多" if direction == "LONG" else "做空"
    lines = [
        f"{arrow} <b>{mode}开仓</b>",
        f"合约: {symbol}",
        f"方向: {mode}",
        f"价格: {price}",
        f"评分: {score:.0f}/100",
        f"保证金: {margin:.1f}U × {leverage}x",
    ]
    if pattern:
        # 先尝试做多模式，再尝试做空模式
        from modes.long_mode import LONG_PATTERNS
        from modes.short_mode import SHORT_PATTERNS
        pattern_info = LONG_PATTERNS.get(pattern) or SHORT_PATTERNS.get(pattern)
        pattern_name = pattern_info.get("desc", "") if pattern_info else ""
        if pattern_name:
            lines.append(f"模式: {pattern_name}")
    if stop_loss_pct:
        sl_price = price * (1 + stop_loss_pct) if direction == "SHORT" else price * (1 - stop_loss_pct)
        lines.append(f"止损: {stop_loss_pct*100:.1f}% ({sl_price:.6f})")
    if reasons:
        lines.append(f"理由: {' | '.join(reasons[:3])}")
    send("\n".join(lines))


def notify_trade_exit(symbol, direction, pnl_pct, pnl_usd, exit_type, hold_hours):
    """平仓通知"""
    emoji = "💰" if pnl_pct > 0 else "💸"
    mode = "做多" if direction == "LONG" else "做空"
    lines = [
        f"{emoji} <b>{mode}平仓</b>",
        f"合约: {symbol}",
        f"盈亏: {pnl_pct:+.2f}% ({pnl_usd:+.2f}U)",
        f"类型: {exit_type}",
        f"持仓: {hold_hours:.1f}小时",
    ]
    send("\n".join(lines))


def notify_periodic_report(bal, positions, mode_stats, candidates=None, memory_summary=""):
    """定期报告"""
    now = datetime.now().strftime("%H:%M")
    total_pos = len(positions)

    lines = [f"📊 妖币猎手 v1.0 | {now}"]
    lines.append("")
    lines.append(f"💰 {bal['balance']:.0f}U | 可用:{bal['available']:.0f}U | 盈亏:{bal['unrealized_pnl']:+.1f}U")
    lines.append(f"📈 持仓: {total_pos}")

    # 持仓
    if positions:
        lines.append("")
        lines.append("━━━ 持仓 ━━━")
        for p in positions:
            pnl = p.get("pnl_pct", 0)
            emoji = "📈" if pnl > 0 else "📉"
            lines.append(f"{emoji} {p['symbol']}: {pnl:+.1f}% | 入:{p['entry_price']:.4f} 现:{p['mark_price']:.4f}")

    send("\n".join(lines))


def notify_review(review_text: str):
    """复盘通知"""
    send(review_text)


def notify_feedback(feedback_text: str):
    """反馈通知"""
    send(feedback_text)


def notify_scan_report(scan_results, bal):
    """扫描报告通知 - 做多Top5 + 做空Top5"""
    now = datetime.now().strftime("%H:%M")
    
    long_results = [r for r in scan_results if r["mode"] == "long"]
    short_results = [r for r in scan_results if r["mode"] == "short"]
    
    lines = [f"🔍 扫描报告 | {now}"]
    lines.append(f"💰 余额: {bal['balance']:.0f}U | 可用: {bal['available']:.0f}U")
    lines.append("")
    
    # 做多 Top5
    lines.append("━━━ 📈 做多 Top5 ━━━")
    if long_results:
        for i, r in enumerate(long_results[:5], 1):
            star = "⭐️" if r["score"] >= 90 else "🟢"
            pattern = r.get("pattern")
            pattern_name = ""
            if pattern:
                from modes.long_mode import LONG_PATTERNS
                pattern_info = LONG_PATTERNS.get(pattern)
                if pattern_info:
                    pattern_name = pattern_info.get("desc", "")
            reasons = " | ".join(r.get("reasons", [])[:2])
            lines.append(f"{star} #{i} {r['symbol']}: {r['score']:.0f}分 | {r['price']:.6f}")
            if pattern_name:
                lines.append(f"  🎯 {pattern_name}")
            if reasons:
                lines.append(f"  {reasons}")
    else:
        lines.append("  无信号")
    lines.append("")
    
    # 做空 Top5
    lines.append("━━━ 📉 做空 Top5 ━━━")
    if short_results:
        for i, r in enumerate(short_results[:5], 1):
            star = "⭐️" if r["score"] >= 90 else "🟢"
            pattern = r.get("pattern")
            pattern_name = ""
            if pattern:
                from modes.short_mode import SHORT_PATTERNS
                pattern_name = SHORT_PATTERNS.get(pattern, {}).get("name", "")
            reasons = " | ".join(r.get("reasons", [])[:2])
            lines.append(f"{star} #{i} {r['symbol']}: {r['score']:.0f}分 | {r['price']:.6f}")
            if pattern_name:
                lines.append(f"  🎯 {pattern_name}")
            if reasons:
                lines.append(f"  {reasons}")
    else:
        lines.append("  无信号")
    
    send("\n".join(lines))
