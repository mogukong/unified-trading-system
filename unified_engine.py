"""
妖币猎手统一系统 v1.0 - 主引擎
双模式(做多+做空) + 四层记忆 + 推送中心 + 三级扫描
"""
import json, os, sys, time, hmac, hashlib
import concurrent.futures
import traceback
from datetime import datetime
from urllib.request import urlopen, Request, ProxyHandler, build_opener
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from modes.long_mode import calc_long_score_v2 as calc_long_score, get_entry_reasons_v2 as long_reasons
from modes.short_mode import calc_short_score_v2 as calc_short_score, get_entry_reasons_v2 as short_reasons
from modes.oi_flow_analyzer import get_oi_quality_score, analyze_oi_price_phase
from memory.trade_memory import record_entry, get_open_trades, update_trade, _load as load_trades
from memory.trade_memory import _save as save_trades
from memory.exit_memory import record_exit, get_loss_stats
from memory.review_history import run_review, get_review_summary
from memory.feedback_engine import apply_review_feedback, adjust_score, get_feedback_summary

def load_trade_memory():
    """加载交易记忆"""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'memory', 'trade_memory.json')
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []

from notifier import send, notify_trade_open, notify_trade_exit, notify_periodic_report, notify_review, notify_feedback, notify_scan_report

# ============================================================
# 配置加载
# ============================================================
def load_config():
    with open(os.path.join(BASE_DIR, "config.json")) as f:
        return json.load(f)

# ============================================================
# .env 加载
# ============================================================
BINANCE_API_KEY=""
BINANCE_API_SECRET = ""
TG_BOT_TOKEN = ""
TG_CHAT_ID = ""
TG_PROXY = "http://YOUR_PROXY:PORT"

def _load_env():
    global BINANCE_API_KEY, BINANCE_API_SECRET, TG_BOT_TOKEN, TG_CHAT_ID, TG_PROXY
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        env_path = os.path.join(os.path.dirname(BASE_DIR), ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BINANCE_API_KEY=") and not BINANCE_API_KEY:
                    BINANCE_API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("BINANCE_API_SECRET=") and not BINANCE_API_SECRET:
                    BINANCE_API_SECRET = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("TELEGRAM_BOT_TOKEN=") and not TG_BOT_TOKEN:
                    TG_BOT_TOKEN = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("TG_CHAT_ID=") and not TG_CHAT_ID:
                    TG_CHAT_ID = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("TG_PROXY="):
                    TG_PROXY = line.split("=", 1)[1].strip().strip('"').strip("'")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        pass

_load_env()

# ============================================================
# Binance API
# ============================================================
BASE_URL = "https://fapi.binance.com"
STATE_FILE = os.path.join(BASE_DIR, "engine_state.json")
LOG_FILE = os.path.join(BASE_DIR, "engine_log.txt")
_exchange_info_cache = {}  # symbol -> {step_size, min_qty}
_exchange_info_loaded = False

def _load_exchange_info():
    """加载exchangeInfo并缓存，避免每次开仓都请求2-3MB数据"""
    global _exchange_info_cache, _exchange_info_loaded
    if _exchange_info_loaded:
        return
    info = fetch_json(f"{BASE_URL}/fapi/v1/exchangeInfo")
    if info:
        for s in info.get("symbols", []):
            sym = s["symbol"]
            for f in s.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    _exchange_info_cache[sym] = {
                        "step_size": float(f["stepSize"]),
                        "min_qty": float(f["minQty"]),
                    }
                    break
    _exchange_info_loaded = True
    log(f"  📦 exchangeInfo缓存加载完成: {len(_exchange_info_cache)}个symbol")

_current_state = None  # 全局状态引用，供open_position等函数使用

# 代理设置
PROXY_URL = TG_PROXY
proxy_handler = ProxyHandler({
    'http': PROXY_URL,
    'https': PROXY_URL,
})
opener = build_opener(proxy_handler)

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except (OSError, IOError): pass

def sign_request(params):
    query = urlencode(params)
    sig = hmac.new(BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + "&signature=" + sig

def api_get(endpoint, params=None):
    if params is None: params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    query = sign_request(params)
    url = f"{BASE_URL}{endpoint}?{query}"
    req = Request(url, headers={"X-MBX-APIKEY": BINANCE_API_KEY})
    try:
        with opener.open(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        log(f"  API GET err: {e}")
        return None

def api_post(endpoint, params=None):
    if params is None: params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    query = sign_request(params)
    url = f"{BASE_URL}{endpoint}?{query}"
    req = Request(url, data=b"", headers={"X-MBX-APIKEY": BINANCE_API_KEY}, method="POST")
    try:
        with opener.open(req, timeout=10) as r:
            result = json.loads(r.read())
            return result
    except HTTPError as e:
        try:
            body = e.read().decode()
            result = json.loads(body) if body else None
            if result:
                return result  # Return error body (e.g. -4130 "stop order exists")
        except (ValueError, UnicodeDecodeError):
            pass
        log(f"  API POST err: {e}")
        return None
    except Exception as e:
        log(f"  API POST err: {e}")
        return None

def api_delete(endpoint, params=None):
    if params is None: params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    query = sign_request(params)
    url = f"{BASE_URL}{endpoint}?{query}"
    req = Request(url, headers={"X-MBX-APIKEY": BINANCE_API_KEY}, method="DELETE")
    try:
        with opener.open(req, timeout=10) as r:
            result = json.loads(r.read())
            return result
    except HTTPError as e:
        try:
            body = e.read().decode()
            result = json.loads(body) if body else None
            if result:
                return result
        except (ValueError, UnicodeDecodeError):
            pass
        log(f"  API DELETE err: {e}")
        return None
    except Exception as e:
        log(f"  API DELETE err: {e}")
        return None

def fetch_json(url, timeout=10):
    """获取JSON数据"""
    req = Request(url)
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return None

# ============================================================
# 交易操作
# ============================================================
def get_balance():
    data = api_get("/fapi/v2/balance")
    if data:
        for item in data:
            if item["asset"] == "USDT":
                return {
                    "balance": float(item["balance"]),
                    "available": float(item["availableBalance"]),
                    "unrealized_pnl": float(item.get("crossUnPnl", 0))
                }
    return None

def get_positions():
    data = api_get("/fapi/v2/positionRisk")
    positions = []
    if data:
        for p in data:
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                direction = "LONG" if amt > 0 else "SHORT"
                positions.append({
                    "symbol": p["symbol"],
                    "direction": direction,
                    "amount": abs(amt),
                    "entry_price": float(p["entryPrice"]),
                    "mark_price": float(p["markPrice"]),
                    "unrealized_pnl": float(p["unRealizedProfit"]),
                    "pnl_pct": (float(p["markPrice"]) - float(p["entryPrice"])) / float(p["entryPrice"]) * 100 * (1 if direction == "LONG" else -1),
                    "leverage": int(p.get("leverage", 10)),
                })
    return positions

def get_open_orders():
    """获取所有挂单"""
    data = api_get("/fapi/v1/openOrders")
    if data and isinstance(data, list):
        return data
    return []

def cancel_order(symbol, order_id):
    """取消指定挂单"""
    result = api_delete("/fapi/v1/order", {"symbol": symbol, "orderId": order_id})
    return result

def get_algo_orders():
    """获取所有条件委托 (Algo Orders)"""
    # Binance 官方: GET /fapi/v1/openAlgoOrders 查询当前条件委托
    data = api_get("/fapi/v1/openAlgoOrders")
    if data and isinstance(data, dict) and "orders" in data:
        return data["orders"]
    elif data and isinstance(data, list):
        return data
    # 兜底：尝试旧路径
    data = api_get("/fapi/v1/algo/openOrders")
    if data and isinstance(data, dict) and "orders" in data:
        return data["orders"]
    elif data and isinstance(data, list):
        return data
    return []

def cancel_algo_order(symbol, algo_id):
    """取消条件委托"""
    result = api_delete("/fapi/v1/algoOrder", {"symbol": symbol, "algoId": algo_id})
    return result

def cancel_stale_orders(positions, state=None):
    """取消没有持仓的币种的所有挂单和条件委托
    
    铁律: 没有持仓就不应该有挂单
    1. 普通挂单: 通过 openOrders API 查询并取消
    2. 条件委托: 通过本地存储的 algoId 查询并取消 (algo API已废弃)
    """
    position_symbols = set(p["symbol"] for p in positions)
    cancelled_count = 0
    algo_count = len(state.get("algo_orders", {})) if state else 0
    log(f"  🔍 检查挂单: {len(positions)}个持仓, {algo_count}个已记录条件委托")
    
    # 1. 检查普通挂单 (LIMIT等)
    open_orders = get_open_orders()
    for order in open_orders:
        sym = order.get("symbol", "")
        order_id = order.get("orderId")
        if sym not in position_symbols and order_id:
            result = cancel_order(sym, order_id)
            if result and "orderId" in result:
                cancelled_count += 1
                log(f"  🗑️ 取消挂单: {sym} orderId={order_id}")
            elif result and result.get("code") == -2011:
                pass
            else:
                err = result.get("msg", "") if result else "no response"
                log(f"  ⚠️ 取消挂单失败: {sym} {err}")
    
    # 2. 检查条件委托 (本地存储的algoId)
    if state and "algo_orders" in state:
        stale_symbols = []
        for sym, algo_ids in state["algo_orders"].items():
            if sym not in position_symbols:
                for algo_id in algo_ids:
                    result = cancel_algo_order(sym, algo_id)
                    if result and not result.get("code"):
                        cancelled_count += 1
                        log(f"  🗑️ 取消条件委托: {sym} algoId={algo_id}")
                    elif result and result.get("code") == -2011:
                        pass
                    elif result and result.get("code") == -4130:
                        pass  # 条件委托不存在
                    else:
                        err = result.get("msg", "") if result else "no response"
                        log(f"  ⚠️ 取消条件委托失败: {sym} {err}")
                stale_symbols.append(sym)
        # 清理已取消的条目
        for sym in stale_symbols:
            del state["algo_orders"][sym]
    
    if cancelled_count > 0:
        log(f"  ✅ 共取消 {cancelled_count} 个无持仓挂单/条件委托")

def open_position(symbol, direction, margin, leverage, price, score, reasons, details, stop_loss_pct=None, pattern=None):
    """开仓"""
    # 设置杠杆
    lev_result = api_post("/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})
    if not lev_result or lev_result.get("code"):
        log(f"  ❌ leverage设置失败: {symbol} x{leverage} → {lev_result}")
        return None
    
    # 计算数量
    size = margin * leverage
    qty = size / price
    
    # 获取精度
    _load_exchange_info()
    sym_info = _exchange_info_cache.get(symbol)
    if sym_info:
        step = sym_info["step_size"]
        qty = int(qty / step) * step
        min_qty = sym_info["min_qty"]
        if qty < min_qty:
            log(f"  ❌ 数量{qty}低于最小下单量{min_qty}")
            return None
    
    # 下单
    side = "BUY" if direction == "LONG" else "SELL"
    position_side = direction
    
    result = api_post("/fapi/v1/order", {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": qty,
        "positionSide": position_side,
    })
    
    if result and "orderId" in result:
        # ★ 关键修复：查询真实成交价，不依赖扫描价
        actual_price = price  # 兜底用扫描价
        actual_qty = qty
        try:
            order_detail = api_get(f"/fapi/v1/order", {"symbol": symbol, "orderId": result["orderId"]})
            if order_detail and order_detail.get("avgPrice") and float(order_detail["avgPrice"]) > 0:
                actual_price = float(order_detail["avgPrice"])
                actual_qty = float(order_detail.get("executedQty", qty))
                log(f"  ✅ 真实成交价: {actual_price:.6f} (扫描价: {price:.6f}, 偏差: {abs(actual_price-price)/price*100:.2f}%)")
            else:
                # 如果 order 接口没返回 avgPrice，查持仓
                time.sleep(0.3)
                positions = api_get("/fapi/v2/positionRisk", {"symbol": symbol})
                if positions:
                    for p in positions:
                        if p.get("symbol") == symbol and p.get("positionSide") == direction:
                            pos_amt = abs(float(p.get("positionAmt", 0)))
                            if pos_amt > 0:
                                actual_price = float(p.get("entryPrice", price))
                                actual_qty = pos_amt
                                log(f"  ✅ 从持仓获取成交价: {actual_price:.6f}")
                                break
        except Exception as e:
            log(f"  ⚠️ 查询成交价失败，使用扫描价: {e}")
        
        # 记录开仓
        trade_id = f"{symbol}_{direction}"
        
        # 从 details 构建 factors 字典
        factors = {}
        if details:
            factors = {
                "price_change_24h": details.get("price_change_24h", 0),
                "price_change_4h": details.get("price_change_4h", 0),
                "oi_change": details.get("oi_change", 0),
                "oi_change_4h": details.get("oi_change_4h", 0),
                "oi_change_24h": details.get("oi_change_24h", 0),
                "volume_ratio": details.get("volume_ratio", 1),
                "funding_rate": details.get("funding_rate", 0),
                "long_ratio": details.get("long_ratio", 50),
                "rsi": details.get("rsi", 50),
                "ema20": details.get("ema20", 0),
                "ema_status": details.get("ema_status", ""),
                "consecutive_up": details.get("consecutive_up", 0),
                "consecutive_red": details.get("consecutive_red", 0),
                "oi_pattern": details.get("oi_pattern", ""),
                "taker_sell_ratio": details.get("taker_sell_ratio", 0),
                "raw_score": details.get("raw_score", 0),
                "oi_phase": details.get("oi_phase", ""),
                "oi_quality": details.get("oi_quality", 0),
            }
        
        # ★ 铁律：止损失败必须重试+兜底平仓
        # record_entry 移至止损确认后（防止兜底平仓留下假开仓记忆）
        if stop_loss_pct:
            if direction == "LONG":
                sl_price = actual_price * (1 - stop_loss_pct)
            else:
                sl_price = actual_price * (1 + stop_loss_pct)
            
            sl_price = round(sl_price, 6)
            sl_side = "SELL" if direction == "LONG" else "BUY"
            sl_placed = False
            
            for attempt in range(3):
                sl_result = api_post("/fapi/v1/algoOrder", {
                    "symbol": symbol,
                    "side": sl_side,
                    "type": "STOP_MARKET",
                    "algoType": "CONDITIONAL",
                    "triggerPrice": sl_price,
                    "closePosition": "true",
                    "positionSide": position_side,
                    "workingType": "MARK_PRICE",
                })
                
                if sl_result and "algoId" in sl_result:
                    log(f"  ✅ 止损单已挂: {sl_price:.6f} (第{attempt+1}次)")
                    if _current_state is not None:
                        if "algo_orders" not in _current_state:
                            _current_state["algo_orders"] = {}
                        if symbol not in _current_state["algo_orders"]:
                            _current_state["algo_orders"][symbol] = []
                        _current_state["algo_orders"][symbol].clear()
                        _current_state["algo_orders"][symbol].append(str(sl_result["algoId"]))
                        save_state(_current_state)
                    sl_placed = True
                    break
                elif sl_result and sl_result.get("code") == -4130:
                    log(f"  ✅ 止损单已存在: {sl_price:.6f}")
                    sl_placed = True
                    break
                else:
                    err_code = sl_result.get("code", "") if sl_result else "no response"
                    err_msg = sl_result.get("msg", "") if sl_result else ""
                    log(f"  ⚠️ 止损单第{attempt+1}次失败: {err_code} {err_msg}")
                    if attempt < 2:
                        time.sleep(1)
            
            if not sl_placed:
                # 铁律：止损挂不上 → 立即平仓
                log(f"  🚨 止损3次全失败，立即市价平仓 {symbol} {direction}")
                close_result = close_position_market(symbol, direction, actual_qty)
                if close_result:
                    log(f"  ✅ 兜底平仓成功: {symbol} {direction}")
                    # 防御性清理：确保trade_memory无残留（正常情况下record_entry尚未调用）
                    try:
                        trades = load_trade_memory()
                        before = len(trades)
                        trades = [t for t in trades if not (t.get("symbol") == symbol and t.get("direction") == direction)]
                        if len(trades) < before:
                            save_trades(trades)
                            log(f"  🗑️ 清理残留trade_memory记录: {symbol} {direction}")
                    except Exception:
                        pass
                    notify_trade_exit(symbol, direction, 0, 0, "止损失败强制平仓", 0)
                else:
                    log(f"  ❌ 兜底平仓也失败！请立即手动处理: {symbol} {direction}")
                    # 发紧急通知
                    try:
                        from notifier import send
                        send(f"🚨🚨🚨 紧急: {symbol} {direction} 止损+平仓都失败，请立即手动处理！")
                    except:
                        pass
                return None
        
        # ★ 止损确认后才记录开仓（防止兜底平仓留下假开仓记忆）
        record_entry(
            symbol=symbol,
            direction=direction,
            strategy_version="unified_v1",
            leverage=leverage,
            margin_usd=margin,
            notional_usd=margin * leverage,
            entry_price=actual_price,  # ★ 使用真实成交价
            quantity=actual_qty,       # ★ 使用真实成交量
            total_score=score,
            entry_reasons=reasons,
            market_quality_score=details.get("oi_quality", 0) if details else 0,
            funding_flow_score=details.get("oi_change", 0) if details else 0,
            resonance_count=sum(1 for r in reasons if r) if reasons else 0,
            candidate_source=f"tier3_{direction.lower()}",
            factors=factors,
            factor_attribution=pattern or "",
            extra={
                "pattern": pattern,
                "stop_loss_pct": stop_loss_pct,
                "scan_price": price,  # 记录扫描价供参考
                "actual_price": actual_price,
                "price_deviation": abs(actual_price - price) / price * 100 if price > 0 else 0,
                "details_raw": {k: v for k, v in details.items() if k not in factors} if details else {},
            },
        )
        
        # 通知
        notify_trade_open(symbol, direction, actual_price, score, margin, leverage, reasons, pattern, stop_loss_pct)
        
        return result
    return None

# ============================================================
# 冷却机制：同一币种连续亏损 → 冷却期不再开仓
# ============================================================
COOLDOWN_CONSECUTIVE_LOSSES = 2    # 连续亏几次触发冷却
COOLDOWN_SECONDS = 7200            # 冷却时长 2 小时

def check_cooldown(symbol, direction):
    """检查币种是否在冷却期，返回 (blocked, reason)"""
    if _current_state is None:
        return False, ""
    cooldowns = _current_state.get("cooldowns", {})
    key = f"{symbol}_{direction}"
    if key in cooldowns:
        info = cooldowns[key]
        until = info.get("until", 0)
        if time.time() < until:
            remaining = int((until - time.time()) / 60)
            return True, f"冷却中(还剩{remaining}min, 连续亏{info.get('losses',0)}次)"
        else:
            # 冷却已过期，清除
            del cooldowns[key]
            save_state(_current_state)
    return False, ""

def record_cooldown(symbol, direction, pnl_usd):
    """平仓时调用：亏损则累计连续亏损次数，盈利则重置"""
    if _current_state is None:
        return
    if "cooldowns" not in _current_state:
        _current_state["cooldowns"] = {}
    cooldowns = _current_state["cooldowns"]
    key = f"{symbol}_{direction}"
    
    if pnl_usd < 0:
        # 亏损：累计次数
        if key in cooldowns:
            losses = cooldowns[key].get("losses", 0) + 1
        else:
            losses = 1
        
        if losses >= COOLDOWN_CONSECUTIVE_LOSSES:
            until = time.time() + COOLDOWN_SECONDS
            cooldowns[key] = {"losses": losses, "until": until, "time": datetime.now().strftime("%m-%d %H:%M")}
            log(f"  🚫 {symbol} {direction} 连续亏{losses}次，冷却{COOLDOWN_SECONDS//60}分钟")
        else:
            cooldowns[key] = {"losses": losses, "until": 0, "time": datetime.now().strftime("%m-%d %H:%M")}
    else:
        # 盈利：重置
        if key in cooldowns:
            del cooldowns[key]
    
    save_state(_current_state)

# ============================================================
# 全局风控暂停：基于 config.json 的 daily_loss_limit_pct / max_consecutive_losses
# ============================================================
def check_global_risk_pause(config, balance_usd):
    """
    检查全局风控规则，返回 (blocked, reason)
    - daily_loss_limit_pct: 当日已实现亏损超过余额的该比例 → 暂停开仓
    - max_consecutive_losses: 最近连续亏损次数超过阈值 → 暂停开仓
    """
    risk_cfg = config.get("risk", {})
    daily_loss_limit = risk_cfg.get("daily_loss_limit_pct", 0)
    max_consec = risk_cfg.get("max_consecutive_losses", 0)

    if not daily_loss_limit and not max_consec:
        return False, ""

    try:
        from memory.exit_memory import _load as load_exits
        exits = load_exits()
    except Exception:
        return False, ""

    if not exits:
        return False, ""

    today_str = datetime.now().strftime("%Y-%m-%d")

    # --- 1. 当日亏损总额 ---
    if daily_loss_limit and balance_usd > 0:
        daily_loss = sum(
            e.get("pnl_usd", 0) for e in exits
            if e.get("exit_time", "").startswith(today_str) and e.get("pnl_usd", 0) < 0
        )
        loss_pct = abs(daily_loss) / balance_usd * 100
        if loss_pct >= daily_loss_limit * 100:
            return True, f"当日亏损{loss_pct:.1f}% >= 限额{daily_loss_limit*100:.0f}%，暂停开仓"

    # --- 2. 连续亏损次数 ---
    if max_consec:
        consec = 0
        for e in reversed(exits):  # 从最新往旧遍历
            if e.get("pnl_usd", 0) < 0:
                consec += 1
            else:
                break
        if consec >= max_consec:
            return True, f"连续亏损{consec}次 >= 限额{max_consec}次，暂停开仓"

    return False, ""


def close_position_market(symbol, direction, qty):
    """市价平仓"""
    side = "SELL" if direction == "LONG" else "BUY"
    position_side = direction
    
    result = api_post("/fapi/v1/order", {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": qty,
        "positionSide": position_side,
    })
    
    return result


def find_weakest_position(mode_positions, mode):
    """找到指定方向中最弱的持仓
    
    评分逻辑: 原始入场分 + 当前盈亏调整
    - 盈利持仓: 加分(表现好)
    - 亏损持仓: 减分(表现差), 亏损越大扣越多
    - 持仓时间长且不赚钱: 额外扣分
    """
    trade_mem = load_trade_memory()
    weakest = None
    weakest_composite = float('inf')
    
    for pos in mode_positions:
        sym = pos["symbol"]
        direction = pos["direction"]
        pnl_pct = pos["pnl_pct"]  # 百分比, 如 -5.2 或 +12.3
        
        # 从trade_memory获取原始入场评分
        entry_record = next(
            (t for t in trade_mem if t.get("symbol") == sym and t.get("direction") == direction),
            {}
        )
        entry_score = entry_record.get("total_score", 50)  # 默认50
        
        # 计算持仓时间
        hold_hours = 0
        entry_time_str = entry_record.get("timestamp", "")
        if entry_time_str:
            try:
                entry_dt = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
                hold_hours = (datetime.now() - entry_dt).total_seconds() / 3600
            except (ValueError, TypeError):
                pass
        
        # 综合评分 = 入场分 + 盈亏调整 + 时间惩罚
        pnl_adjustment = 0
        if pnl_pct < 0:
            # 亏损: 每亏1%扣2分, 最多扣30分
            pnl_adjustment = max(-30, pnl_pct * 2)
        elif pnl_pct > 0:
            # 盈利: 每赚1%加1分, 最多加20分
            pnl_adjustment = min(20, pnl_pct * 1)
        
        # 时间惩罚: 持仓超过6小时且不赚钱, 每小时扣1分
        time_penalty = 0
        if hold_hours > 6 and pnl_pct < 3:
            time_penalty = -(hold_hours - 6) * 1
        
        composite = entry_score + pnl_adjustment + time_penalty
        
        if composite < weakest_composite:
            weakest_composite = composite
            weakest = {
                **pos,
                "entry_score": entry_score,
                "composite_score": composite,
                "hold_hours": hold_hours,
                "pnl_adjustment": pnl_adjustment,
                "time_penalty": time_penalty,
            }
    
    return weakest


def try_replace_position(best, mode, positions, bal, config, held_symbols):
    """尝试用高分新信号替换最弱持仓
    
    流程:
    1. 找到该方向最弱持仓
    2. 判断新信号是否足够强(超过阈值)
    3. 平掉最弱持仓
    4. 开新仓位
    5. 记录退出和进入
    """
    mode_cfg = config["modes"][mode]
    mode_positions = [p for p in positions if p["direction"] == mode.upper()]
    
    if not mode_positions:
        return False
    
    # 找到最弱持仓
    weakest = find_weakest_position(mode_positions, mode)
    if not weakest:
        return False
    
    new_score = best["score"]
    threshold = mode_cfg.get("replace_threshold", 15)
    weakest_score = weakest["composite_score"]
    score_diff = new_score - weakest_score
    
    # 判断是否值得替换
    worth_replace = False
    reason = ""
    
    if score_diff >= threshold:
        worth_replace = True
        reason = f"新信号{new_score:.0f}分 vs 最弱{weakest['symbol']}={weakest_score:.0f}分, 差值{score_diff:.0f}≥{threshold}"
    elif weakest["pnl_pct"] < -3 and score_diff >= threshold * 0.6:
        # 最弱持仓亏损>3%且新信号有一定优势(阈值的60%)
        worth_replace = True
        reason = f"最弱{weakest['symbol']}亏损{weakest['pnl_pct']:.1f}%, 新信号{new_score:.0f}分有足够优势"
    
    if not worth_replace:
        log(f"  📊 {mode.upper()}满仓但新信号不够强: 新{new_score:.0f} vs 最弱{weakest['symbol']}={weakest_score:.0f} (差值{score_diff:.0f}<{threshold})")
        return False
    
    # === 执行替换 ===
    log(f"  🔄 优胜劣汰: {reason}")
    log(f"  📤 平掉: {weakest['symbol']} {weakest['direction']} (入场分{weakest['entry_score']}, 综合{weakest_score:.0f}, 持仓{weakest['hold_hours']:.1f}h, 盈亏{weakest['pnl_pct']:+.1f}%)")
    
    # 1. 取消该币种的止损单
    state = _current_state or {}
    algo_orders = state.get("algo_orders", {}).get(weakest["symbol"], [])
    for algo_id in algo_orders:
        cancel_algo_order(weakest["symbol"], algo_id)
    
    # 2. 市价平仓
    close_result = close_position_market(
        weakest["symbol"], weakest["direction"], weakest["amount"]
    )
    
    if not close_result or "orderId" not in close_result:
        log(f"  ❌ 平仓失败: {weakest['symbol']} → {close_result}")
        return False
    
    log(f"  ✅ 平仓成功: {weakest['symbol']}")
    
    # 3. 记录退出
    pos_key = f"{weakest['symbol']}_{weakest['direction']}"
    entry_record = next(
        (t for t in load_trade_memory() if t.get("symbol") == weakest["symbol"] and t.get("direction") == weakest["direction"]),
        {}
    )
    factors = entry_record.get("factors", {})
    market_ctx = {
        "funding_rate": factors.get("funding_rate", 0),
        "long_ratio": factors.get("long_ratio", 50),
        "oi_change": factors.get("oi_change", 0),
        "volume_ratio": factors.get("volume_ratio", 1),
        "rsi": factors.get("rsi", 50),
    }
    
    # 用manage_positions追踪的峰值, 没有则用当前值
    tracked_peak = manage_positions.peak_pnl.get(pos_key, weakest["pnl_pct"] / 100) if hasattr(manage_positions, 'peak_pnl') else weakest["pnl_pct"] / 100
    
    record_exit(
        trade_id=pos_key,
        symbol=weakest["symbol"],
        direction=weakest["direction"],
        entry_price=weakest["entry_price"],
        exit_price=weakest["mark_price"],
        pnl_pct=weakest["pnl_pct"] / 100,
        pnl_usd=weakest["unrealized_pnl"],
        exit_type="replaced",
        hold_hours=weakest["hold_hours"],
        peak_pnl=tracked_peak,
        entry_record=entry_record,
        market_context=market_ctx,
    )
    
    # 4. 从trade_memory移除
    try:
        trades = load_trade_memory()
        trades = [t for t in trades if not (t.get("symbol") == weakest["symbol"] and t.get("direction") == weakest["direction"])]
        save_trades(trades)
    except Exception as e:
        log(f"  ⚠️ trade_memory清理失败: {e}")
    
    # 5. 清理peak_pnl和tp1_done
    if hasattr(manage_positions, 'peak_pnl') and pos_key in manage_positions.peak_pnl:
        del manage_positions.peak_pnl[pos_key]
    if hasattr(manage_positions, 'tp1_done') and pos_key in manage_positions.tp1_done:
        manage_positions.tp1_done.discard(pos_key)
    
    # 6. 从last_positions移除
    global last_positions
    last_positions = [p for p in last_positions if f"{p['symbol']}_{p['direction']}" != pos_key]
    
    # 7. 通知平仓
    notify_trade_exit(
        weakest["symbol"], weakest["direction"],
        weakest["pnl_pct"] / 100, weakest["unrealized_pnl"],
        "优胜劣汰", weakest["hold_hours"]
    )
    
    # 8. 开新仓
    log(f"  📥 开新: {best['symbol']} {mode.upper()} 评分:{best['score']:.0f}")
    margin = bal["balance"] * mode_cfg.get("position_pct", 0.15)
    leverage = mode_cfg.get("leverage", 10)
    
    trade = open_position(
        best["symbol"], mode.upper(), margin, leverage,
        best["price"], best["score"], best["reasons"], best["details"],
        best.get("stop_loss_pct"), best.get("pattern")
    )
    
    if trade:
        log(f"  ✅ 替换完成: {weakest['symbol']} → {best['symbol']}")
        return True
    else:
        log(f"  ❌ 新仓开仓失败: {best['symbol']}")
        return False


# ============================================================
# 技术指标函数
# ============================================================
def calc_bollinger_bands(klines_raw: list, period: int = 20, std_dev: float = 2.0) -> dict:
    """
    计算布林带 (Bollinger Bands)
    输入: Binance kline 原始数据 [[open_time, open, high, low, close, volume, ...], ...]
    返回: {bb_upper, bb_middle, bb_lower, bb_bandwidth, bb_pct_b, bb_squeeze}
    """
    result = {}
    if not klines_raw or len(klines_raw) < period:
        return result

    closes = [float(k[4]) for k in klines_raw]
    highs = [float(k[2]) for k in klines_raw]
    lows = [float(k[3]) for k in klines_raw]

    # 中轨 = SMA(period)
    sma = sum(closes[-period:]) / period

    # 标准差
    variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
    std = variance ** 0.5

    upper = sma + std_dev * std
    lower = sma - std_dev * std
    bandwidth = (upper - lower) / sma * 100 if sma > 0 else 0

    current = closes[-1]
    pct_b = (current - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

    # 布林带挤压判断 (bandwidth < 历史20%分位)
    if len(closes) >= 60:
        historical_bw = []
        for i in range(20, len(closes)):
            chunk = closes[i - 20:i]
            s = sum(chunk) / 20
            v = sum((c - s) ** 2 for c in chunk) / 20
            st = v ** 0.5
            bw = (s + 2 * st - (s - 2 * st)) / s * 100 if s > 0 else 0
            historical_bw.append(bw)
        historical_bw.sort()
        idx_20 = int(len(historical_bw) * 0.2)
        squeeze = bandwidth < historical_bw[idx_20] if historical_bw else False
    else:
        squeeze = False

    result["bb_upper"] = upper
    result["bb_middle"] = sma
    result["bb_lower"] = lower
    result["bb_bandwidth"] = round(bandwidth, 2)
    result["bb_pct_b"] = round(pct_b, 3)
    result["bb_squeeze"] = squeeze

    return result


def calc_ema_multi(klines_raw: list) -> dict:
    """
    计算多周期EMA (EMA9, EMA21, EMA55) + EMA排列状态
    输入: Binance kline 原始数据
    返回: {ema9, ema21, ema55, ema_trend, ema_align, ema_spread}
    """
    result = {}
    if not klines_raw or len(klines_raw) < 55:
        return result

    closes = [float(k[4]) for k in klines_raw]

    def _ema(data, period):
        multiplier = 2 / (period + 1)
        ema = data[0]
        for price in data[1:]:
            ema = (price - ema) * multiplier + ema
        return ema

    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    ema55 = _ema(closes, 55)

    # EMA排列状态
    # 多头排列: ema9 > ema21 > ema55 (强趋势)
    # 空头排列: ema9 < ema21 < ema55
    # 纠缠: 其他情况
    if ema9 > ema21 > ema55:
        trend = "bullish"
        align = "多头排列"
    elif ema9 < ema21 < ema55:
        trend = "bearish"
        align = "空头排列"
    elif ema9 > ema21 and ema21 < ema55:
        trend = "recovering"
        align = "金叉形成中"
    elif ema9 < ema21 and ema21 > ema55:
        trend = "weakening"
        align = "死叉形成中"
    else:
        trend = "neutral"
        align = "均线纠缠"

    # EMA离散度 = (ema9 - ema55) / ema55 * 100
    spread = (ema9 - ema55) / ema55 * 100 if ema55 > 0 else 0

    # EMA斜率 (最近3根K线的EMA21变化方向)
    if len(closes) >= 58:
        ema21_prev = _ema(closes[:-3], 21)
        slope = (ema21 - ema21_prev) / ema21_prev * 100 if ema21_prev > 0 else 0
    else:
        slope = 0

    result["ema9"] = ema9
    result["ema21"] = ema21
    result["ema55"] = ema55
    result["ema_trend"] = trend
    result["ema_align"] = align
    result["ema_spread"] = round(spread, 2)
    result["ema_slope"] = round(slope, 3)

    return result


def calc_volume_quality(klines_raw: list) -> dict:
    """
    计算成交量质量指标
    输入: Binance kline 原始数据
    返回: {vol_ma20, vol_ratio_20, vol_trend, vol_buy_pct,
           vol_breakout, vol_divergence, vol_quality_score}
    """
    result = {}
    if not klines_raw or len(klines_raw) < 20:
        return result

    volumes = [float(k[5]) for k in klines_raw]
    closes = [float(k[4]) for k in klines_raw]
    opens = [float(k[1]) for k in klines_raw]
    highs = [float(k[2]) for k in klines_raw]
    lows = [float(k[3]) for k in klines_raw]

    # 20周期成交量均线
    vol_ma20 = sum(volumes[-20:]) / 20
    current_vol = volumes[-1]
    vol_ratio = current_vol / vol_ma20 if vol_ma20 > 0 else 1

    # 成交量趋势 (最近5根 vs 前5根)
    vol_recent_5 = sum(volumes[-5:]) / 5
    vol_prev_5 = sum(volumes[-10:-5]) / 5
    vol_trend = "increasing" if vol_recent_5 > vol_prev_5 * 1.2 else \
                "decreasing" if vol_recent_5 < vol_prev_5 * 0.8 else "stable"

    # 买盘占比估算 (close > open = 买盘主导)
    buy_vol = 0
    total_vol = 0
    for i in range(-min(20, len(klines_raw)), 0):
        if closes[i] >= opens[i]:
            buy_vol += volumes[i]
        total_vol += volumes[i]
    buy_pct = buy_vol / total_vol * 100 if total_vol > 0 else 50

    # 放量突破检测 (量比>2 + 价格突破近期高点)
    recent_high = max(highs[-20:-1]) if len(highs) >= 20 else 0
    vol_breakout = vol_ratio > 2.0 and closes[-1] > recent_high

    # 量价背离检测
    # 价格新高但成交量萎缩 = 顶背离 (bearish)
    # 价格新低但成交量萎缩 = 底背离 (bullish)
    price_high = max(closes[-10:])
    price_low = min(closes[-10:])
    vol_high_10 = max(volumes[-10:])
    divergence = "none"
    if closes[-1] >= price_high * 0.99 and volumes[-1] < vol_high_10 * 0.6:
        divergence = "bearish_top"  # 价涨量缩=顶背离
    elif closes[-1] <= price_low * 1.01 and volumes[-1] < vol_high_10 * 0.6:
        divergence = "bullish_bottom"  # 价跌量缩=底背离

    # 综合成交量质量评分 (0-100)
    quality_score = 50  # 基准分
    if vol_ratio > 2.0:
        quality_score += 20
    elif vol_ratio > 1.5:
        quality_score += 10
    elif vol_ratio < 0.5:
        quality_score -= 15  # 缩量扣分

    if buy_pct > 60:
        quality_score += 15
    elif buy_pct > 55:
        quality_score += 5
    elif buy_pct < 40:
        quality_score -= 10

    if vol_breakout:
        quality_score += 15

    if divergence == "bearish_top":
        quality_score -= 15
    elif divergence == "bullish_bottom":
        quality_score += 10

    result["vol_ma20"] = vol_ma20
    result["vol_ratio_20"] = round(vol_ratio, 2)
    result["vol_trend"] = vol_trend
    result["vol_buy_pct"] = round(buy_pct, 1)
    result["vol_breakout"] = vol_breakout
    result["vol_divergence"] = divergence
    result["vol_quality_score"] = max(0, min(100, quality_score))

    return result


def calc_support_resistance(klines_raw: list) -> dict:
    """
    计算支撑阻力位 (基于价格密集区 + 极值点)
    输入: Binance kline 原始数据 (建议 >= 60 根)
    返回: {sr_resistance, sr_support, sr_resistance_2, sr_support_2,
           sr_pivot, sr_range_pct, sr_position}
    """
    result = {}
    if not klines_raw or len(klines_raw) < 30:
        return result

    highs = [float(k[2]) for k in klines_raw]
    lows = [float(k[3]) for k in klines_raw]
    closes = [float(k[4]) for k in klines_raw]
    current = closes[-1]

    # === 方法1: 极值点法 ===
    # 找局部高点和低点 (前后3根K线的极值)
    swing_highs = []
    swing_lows = []
    lookback = 3
    for i in range(lookback, len(highs) - lookback):
        # 局部高点
        if all(highs[i] >= highs[i + j] for j in range(-lookback, lookback + 1) if j != 0):
            swing_highs.append(highs[i])
        # 局部低点
        if all(lows[i] <= lows[i + j] for j in range(-lookback, lookback + 1) if j != 0):
            swing_lows.append(lows[i])

    # === 方法2: 价格密集区法 (Volume Profile 近似) ===
    # 将价格区间分成20个bin，统计每个bin的K线数
    price_range = max(highs) - min(lows)
    if price_range <= 0:
        return result

    bin_count = 20
    bin_size = price_range / bin_count
    bins = [0] * bin_count
    for i in range(len(closes)):
        idx = int((closes[i] - min(lows)) / bin_size)
        idx = min(idx, bin_count - 1)
        bins[idx] += 1

    # 找密集区 (K线数最多的bin)
    max_bin_idx = bins.index(max(bins))
    dense_center = min(lows) + (max_bin_idx + 0.5) * bin_size

    # === 合并确定支撑阻力 ===
    # 阻力: 当前价格上方的 swing_highs + 密集区上沿
    above_prices = sorted([h for h in swing_highs if h > current])
    below_prices = sorted([l for l in swing_lows if l < current], reverse=True)

    # 密集区边界
    dense_upper = min(lows) + (max_bin_idx + 1) * bin_size
    dense_lower = min(lows) + max_bin_idx * bin_size

    # 主阻力位
    resistance_candidates = above_prices[:3] + [dense_upper] if dense_upper > current else above_prices[:3]
    resistance_candidates = sorted(set(resistance_candidates))
    r1 = resistance_candidates[0] if resistance_candidates else max(highs)
    r2 = resistance_candidates[1] if len(resistance_candidates) > 1 else r1 * 1.05

    # 主支撑位
    support_candidates = below_prices[:3] + [dense_lower] if dense_lower < current else below_prices[:3]
    support_candidates = sorted(set(support_candidates), reverse=True)
    s1 = support_candidates[0] if support_candidates else min(lows)
    s2 = support_candidates[1] if len(support_candidates) > 1 else s1 * 0.95

    # Pivot Point (经典)
    pivot = (highs[-1] + lows[-1] + closes[-1]) / 3

    # 价格在区间中的位置 (0%=在支撑, 100%=在阻力)
    sr_range = r1 - s1
    position_pct = ((current - s1) / sr_range * 100) if sr_range > 0 else 50

    # 区间宽度占比
    range_pct = sr_range / current * 100 if current > 0 else 0

    result["sr_resistance"] = r1
    result["sr_support"] = s1
    result["sr_resistance_2"] = r2
    result["sr_support_2"] = s2
    result["sr_pivot"] = round(pivot, 6)
    result["sr_range_pct"] = round(range_pct, 2)
    result["sr_position"] = round(position_pct, 1)  # 0-100, 越高越接近阻力
    result["sr_dense_center"] = round(dense_center, 6)

    return result


def calc_macd(klines_raw: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """
    计算MACD (Moving Average Convergence Divergence)
    输入: Binance kline 原始数据
    返回: {macd_line, signal_line, histogram, macd_cross, macd_trend, histogram_momentum}
    """
    result = {}
    if not klines_raw or len(klines_raw) < slow + signal:
        return result

    closes = [float(k[4]) for k in klines_raw]

    def _ema(data, period):
        multiplier = 2 / (period + 1)
        ema = data[0]
        for price in data[1:]:
            ema = (price - ema) * multiplier + ema
        return ema

    # 计算完整EMA序列 (用于MACD线序列)
    def _ema_series(data, period):
        multiplier = 2 / (period + 1)
        ema_vals = [data[0]]
        for price in data[1:]:
            ema_vals.append((price - ema_vals[-1]) * multiplier + ema_vals[-1])
        return ema_vals

    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)

    # MACD线 = EMA_fast - EMA_slow
    macd_line_series = [f - s for f, s in zip(ema_fast, ema_slow)]

    # Signal线 = EMA(MACD, signal)
    signal_series = _ema_series(macd_line_series, signal)

    # 柱状图 = MACD - Signal
    histogram_series = [m - s for m, s in zip(macd_line_series, signal_series)]

    current_macd = macd_line_series[-1]
    current_signal = signal_series[-1]
    current_hist = histogram_series[-1]
    prev_hist = histogram_series[-2] if len(histogram_series) >= 2 else 0

    # 金叉/死叉判断
    if len(macd_line_series) >= 2 and len(signal_series) >= 2:
        prev_macd = macd_line_series[-2]
        prev_signal = signal_series[-2]
        if prev_macd <= prev_signal and current_macd > current_signal:
            cross = "golden_cross"  # 金叉
        elif prev_macd >= prev_signal and current_macd < current_signal:
            cross = "death_cross"   # 死叉
        else:
            cross = "none"
    else:
        cross = "none"

    # MACD趋势方向
    if current_macd > 0 and current_signal > 0:
        trend = "bullish_zone"      # 零轴上方 = 多头区域
    elif current_macd < 0 and current_signal < 0:
        trend = "bearish_zone"      # 零轴下方 = 空头区域
    elif current_macd > current_signal:
        trend = "turning_bullish"   # 由空转多
    else:
        trend = "turning_bearish"   # 由多转空

    # 柱状图动量 (连续放大/缩小)
    if len(histogram_series) >= 3:
        h1, h2, h3 = histogram_series[-3], histogram_series[-2], histogram_series[-1]
        if abs(h3) > abs(h2) > abs(h1):
            momentum = "expanding"   # 柱状图放大 = 动能增强
        elif abs(h3) < abs(h2) < abs(h1):
            momentum = "contracting" # 柱状图缩小 = 动能减弱
        else:
            momentum = "mixed"
    else:
        momentum = "unknown"

    # 柱状图是否走平/拐头
    if abs(current_hist) < abs(prev_hist) * 0.5:
        hist_action = "flattening"   # 柱状图快速缩小 = 走平
    elif (prev_hist < 0 and current_hist > prev_hist) or (prev_hist > 0 and current_hist < prev_hist):
        hist_action = "reversing"    # 柱状图拐头
    else:
        hist_action = "continuing"

    result["macd_line"] = round(current_macd, 6)
    result["signal_line"] = round(current_signal, 6)
    result["histogram"] = round(current_hist, 6)
    result["macd_cross"] = cross
    result["macd_trend"] = trend
    result["histogram_momentum"] = momentum
    result["histogram_action"] = hist_action

    return result


def calc_volume_pattern(klines_raw: list) -> dict:
    """
    量价配合模式分析
    输入: Binance kline 原始数据
    返回: {vol_pattern, vol_health, vol_signal, vol_candle_analysis}
    模式:
      - vol_breakout: 放量突破 (量比>2 + 价格新高)
      - vol_shrink_pullback: 缩量回调 (价格回调但量能萎缩) = 健康调整
      - vol_expansion: 放量上涨 (量价齐升)
      - vol_divergence_top: 顶背离 (价涨量缩)
      - vol_divergence_bottom: 底背离 (价跌量缩)
      - vol_panic: 恐慌放量 (巨量下跌)
      - vol_dry: 无量横盘
    """
    result = {}
    if not klines_raw or len(klines_raw) < 20:
        return result

    volumes = [float(k[5]) for k in klines_raw]
    closes = [float(k[4]) for k in klines_raw]
    opens = [float(k[1]) for k in klines_raw]
    highs = [float(k[2]) for k in klines_raw]
    lows = [float(k[3]) for k in klines_raw]

    vol_ma20 = sum(volumes[-20:]) / 20
    vol_ma5 = sum(volumes[-5:]) / 5
    current_vol = volumes[-1]
    vol_ratio = current_vol / vol_ma20 if vol_ma20 > 0 else 1

    # 最近5根K线的趋势
    price_5_change = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] > 0 else 0

    # 最近10根的高低点
    high_10 = max(highs[-10:])
    low_10 = min(lows[-10:])
    price_range_10 = high_10 - low_10

    # 价格在10根区间中的位置
    price_position = (closes[-1] - low_10) / price_range_10 * 100 if price_range_10 > 0 else 50

    # 近5根成交量趋势
    vol_trend_5 = "up" if vol_ma5 > vol_ma20 * 1.2 else "down" if vol_ma5 < vol_ma20 * 0.8 else "flat"

    # === 模式判断 ===
    pattern = "normal"
    health = "neutral"
    signal = "none"

    # 放量突破: 量比>2 + 价格接近10根高点
    if vol_ratio > 2.0 and price_position > 80:
        pattern = "vol_breakout"
        health = "strong"
        signal = "bullish"

    # 缩量回调: 价格下跌但成交量萎缩 (最健康的调整)
    elif price_5_change < -2 and vol_ma5 < vol_ma20 * 0.7:
        pattern = "vol_shrink_pullback"
        health = "healthy"
        signal = "buy_dip"

    # 放量上涨: 量价齐升
    elif price_5_change > 3 and vol_ratio > 1.5:
        pattern = "vol_expansion"
        health = "strong"
        signal = "bullish"

    # 顶背离: 价格在高位但成交量萎缩
    elif price_position > 80 and vol_ratio < 0.7:
        pattern = "vol_divergence_top"
        health = "warning"
        signal = "bearish_divergence"

    # 底背离: 价格在低位但成交量萎缩 (抛压衰竭)
    elif price_position < 20 and vol_ratio < 0.7:
        pattern = "vol_divergence_bottom"
        health = "accumulation"
        signal = "bullish_divergence"

    # 恐慌放量: 巨量下跌
    elif vol_ratio > 3.0 and price_5_change < -5:
        pattern = "vol_panic"
        health = "danger"
        signal = "bearish"

    # 无量横盘
    elif vol_ratio < 0.5 and abs(price_5_change) < 2:
        pattern = "vol_dry"
        health = "stagnant"
        signal = "wait"

    # === 单根K线量能分析 ===
    last_candle = {
        "body_pct": abs(closes[-1] - opens[-1]) / opens[-1] * 100 if opens[-1] > 0 else 0,
        "is_bullish": closes[-1] >= opens[-1],
        "vol_vs_avg": round(vol_ratio, 2),
        "upper_shadow": (highs[-1] - max(opens[-1], closes[-1])) / (highs[-1] - lows[-1]) * 100 if (highs[-1] - lows[-1]) > 0 else 0,
        "lower_shadow": (min(opens[-1], closes[-1]) - lows[-1]) / (highs[-1] - lows[-1]) * 100 if (highs[-1] - lows[-1]) > 0 else 0,
    }

    result["vol_pattern"] = pattern
    result["vol_health"] = health
    result["vol_signal"] = signal
    result["vol_ratio_current"] = round(vol_ratio, 2)
    result["vol_ma5_vs_ma20"] = round(vol_ma5 / vol_ma20, 2) if vol_ma20 > 0 else 1
    result["vol_trend_5"] = vol_trend_5
    result["vol_price_position"] = round(price_position, 1)
    result["vol_last_candle"] = last_candle

    return result


def detect_consolidation_box(klines_raw: list, min_touches: int = 3) -> dict:
    """
    箱体/震荡区间检测
    输入: Binance kline 原始数据 (建议>=40根)
    返回: {box_high, box_low, box_mid, box_width_pct, box_touches,
           box_duration, box_position, box_status}
    箱体状态:
      - forming: 正在形成 (触碰次数少)
      - confirmed: 确认箱体 (多次触碰上下沿)
      - breaking_up: 向上突破
      - breaking_down: 向下突破
    """
    result = {}
    if not klines_raw or len(klines_raw) < 20:
        return result

    highs = [float(k[2]) for k in klines_raw]
    lows = [float(k[3]) for k in klines_raw]
    closes = [float(k[4]) for k in klines_raw]
    current = closes[-1]

    # 用最近30根K线检测箱体 (如果不够就用全部)
    lookback = min(30, len(klines_raw))
    recent_highs = highs[-lookback:]
    recent_lows = lows[-lookback:]
    recent_closes = closes[-lookback:]

    # 箱体上沿 = 近期高点的密集区域 (取top 20%的平均值)
    sorted_highs = sorted(recent_highs, reverse=True)
    top_n = max(2, len(sorted_highs) // 5)
    box_high = sum(sorted_highs[:top_n]) / top_n

    # 箱体下沿 = 近期低点的密集区域
    sorted_lows = sorted(recent_lows)
    bot_n = max(2, len(sorted_lows) // 5)
    box_low = sum(sorted_lows[:bot_n]) / bot_n

    box_mid = (box_high + box_low) / 2
    box_width = box_high - box_low
    box_width_pct = box_width / box_mid * 100 if box_mid > 0 else 0

    # 计算触碰上下沿的次数
    tolerance = box_width * 0.1  # 10%容差
    upper_touches = sum(1 for h in recent_highs if abs(h - box_high) < tolerance)
    lower_touches = sum(1 for l in recent_lows if abs(l - box_low) < tolerance)
    total_touches = upper_touches + lower_touches

    # 箱体持续时间 (K线数)
    box_duration = lookback

    # 当前价格在箱体中的位置 (0%=下沿, 100%=上沿)
    box_position = ((current - box_low) / box_width * 100) if box_width > 0 else 50

    # 箱体状态
    if current > box_high * 1.01:
        status = "breaking_up"
    elif current < box_low * 0.99:
        status = "breaking_down"
    elif total_touches >= min_touches * 2:
        status = "confirmed"     # 多次触碰 = 确认箱体
    elif total_touches >= min_touches:
        status = "forming"       # 正在形成
    else:
        status = "not_detected"  # 未形成明显箱体

    # 箱体质量评分 (触碰次数越多、宽度越合理，质量越高)
    quality = min(100, total_touches * 15 + (20 if 3 < box_width_pct < 15 else 0))

    result["box_high"] = round(box_high, 6)
    result["box_low"] = round(box_low, 6)
    result["box_mid"] = round(box_mid, 6)
    result["box_width_pct"] = round(box_width_pct, 2)
    result["box_upper_touches"] = upper_touches
    result["box_lower_touches"] = lower_touches
    result["box_duration"] = box_duration
    result["box_position"] = round(box_position, 1)
    result["box_status"] = status
    result["box_quality"] = quality

    return result


def calc_confluence_score(kline_data: dict, mode: str = "long") -> dict:
    """
    多指标共振评分 (0-100)
    综合: EMA排列 + MACD + 布林带位置 + 成交量质量 + 支撑阻力位置 + AI500评分
    返回: {confluence_score, confluence_level, confluence_direction,
           bullish_signals, bearish_signals, signal_details}
    """
    bullish = []
    bearish = []
    score = 50  # 基准分

    # === 1. EMA排列 (±15分) ===
    ema_trend = kline_data.get("ema_trend", "neutral")
    ema_spread = kline_data.get("ema_spread", 0)

    if ema_trend == "bullish":
        score += 15
        bullish.append(f"EMA多头排列 离散{ema_spread:+.1f}%")
    elif ema_trend == "bearish":
        score -= 15
        bearish.append(f"EMA空头排列 离散{ema_spread:+.1f}%")
    elif ema_trend == "recovering":
        score += 8
        bullish.append("EMA金叉形成中")
    elif ema_trend == "weakening":
        score -= 8
        bearish.append("EMA死叉形成中")

    # EMA斜率加分
    ema_slope = kline_data.get("ema_slope", 0)
    if ema_slope > 0.1:
        score += 5
        bullish.append(f"EMA21斜率向上 {ema_slope:+.2f}%")
    elif ema_slope < -0.1:
        score -= 5
        bearish.append(f"EMA21斜率向下 {ema_slope:+.2f}%")

    # === 2. MACD (±15分) ===
    macd_cross = kline_data.get("macd_cross", "none")
    macd_trend = kline_data.get("macd_trend", "neutral")
    histogram_action = kline_data.get("histogram_action", "continuing")

    if macd_cross == "golden_cross":
        score += 15
        bullish.append("MACD金叉")
    elif macd_cross == "death_cross":
        score -= 15
        bearish.append("MACD死叉")

    if macd_trend == "bullish_zone":
        score += 5
        bullish.append("MACD零轴上方")
    elif macd_trend == "bearish_zone":
        score -= 5
        bearish.append("MACD零轴下方")

    if histogram_action == "flattening" and macd_trend.startswith("turning"):
        # 柱状图走平 + 趋势转换中 = 即将金叉/死叉
        score += 3

    # === 3. 布林带位置 (±10分) ===
    bb_pct_b = kline_data.get("bb_pct_b", 0.5)
    bb_squeeze = kline_data.get("bb_squeeze", False)
    bb_bandwidth = kline_data.get("bb_bandwidth", 0)

    if bb_pct_b < 0.1:
        # 触及下轨 = 超卖
        score += 10 if mode == "long" else -5
        (bullish if mode == "long" else bearish).append(f"触及布林下轨 %B={bb_pct_b:.2f}")
    elif bb_pct_b > 0.9:
        # 触及上轨 = 超买
        score += 10 if mode == "short" else -5
        (bullish if mode == "short" else bearish).append(f"触及布林上轨 %B={bb_pct_b:.2f}")

    if bb_squeeze:
        score += 5
        bullish.append(f"布林带挤压 bandwidth={bb_bandwidth:.1f}% 即将变盘")

    # === 4. 成交量质量 (±10分) ===
    vol_pattern = kline_data.get("vol_pattern", "normal")
    vol_signal = kline_data.get("vol_signal", "none")
    vol_quality = kline_data.get("vol_quality_score", 50)

    if vol_pattern == "vol_breakout":
        score += 10
        bullish.append("放量突破")
    elif vol_pattern == "vol_shrink_pullback":
        score += 8
        bullish.append("缩量回调=健康调整")
    elif vol_pattern == "vol_expansion":
        score += 7
        bullish.append("放量上涨")
    elif vol_pattern == "vol_divergence_top":
        score -= 10
        bearish.append("量价顶背离")
    elif vol_pattern == "vol_panic":
        score -= 12
        bearish.append("恐慌放量")
    elif vol_pattern == "vol_dry":
        score -= 3

    # === 5. 支撑阻力位置 (±8分) ===
    sr_position = kline_data.get("sr_position", 50)
    sr_support = kline_data.get("sr_support", 0)
    sr_resistance = kline_data.get("sr_resistance", 0)

    if mode == "long":
        if sr_position < 25:
            score += 8
            bullish.append(f"接近支撑位 位置{sr_position:.0f}%")
        elif sr_position > 80:
            score -= 5
            bearish.append(f"接近阻力位 位置{sr_position:.0f}%")
    else:  # short
        if sr_position > 80:
            score += 8
            bullish.append(f"接近阻力位 位置{sr_position:.0f}%")
        elif sr_position < 25:
            score -= 5
            bearish.append(f"接近支撑位 位置{sr_position:.0f}%")

    # === 6. 箱体位置 (±5分) ===
    box_status = kline_data.get("box_status", "not_detected")
    box_position = kline_data.get("box_position", 50)

    if box_status == "confirmed":
        if mode == "long" and box_position < 30:
            score += 5
            bullish.append("箱体下沿附近")
        elif mode == "short" and box_position > 70:
            score += 5
            bullish.append("箱体上沿附近")
    elif box_status in ("breaking_up", "breaking_down"):
        score += 8
        bullish.append(f"箱体{box_status}")

    # === 7. RSI (±5分) ===
    rsi = kline_data.get("rsi", 50)
    if mode == "long":
        if rsi < 30:
            score += 5
            bullish.append(f"RSI超卖 {rsi:.0f}")
        elif rsi > 75:
            score -= 5
            bearish.append(f"RSI超买 {rsi:.0f}")
    else:
        if rsi > 70:
            score += 5
            bullish.append(f"RSI超买 {rsi:.0f}")
        elif rsi < 30:
            score -= 5
            bearish.append(f"RSI超卖 {rsi:.0f}")

    # 限制在 0-100
    score = max(0, min(100, score))

    # 共振等级
    if score >= 80:
        level = "strong_bullish" if mode == "long" else "strong_bearish"
    elif score >= 65:
        level = "bullish" if mode == "long" else "bearish"
    elif score >= 45:
        level = "neutral"
    elif score >= 30:
        level = "bearish" if mode == "long" else "bullish"
    else:
        level = "strong_bearish" if mode == "long" else "strong_bullish"

    # 方向一致性
    if len(bullish) > len(bearish) * 2:
        direction = "aligned_bullish"
    elif len(bearish) > len(bullish) * 2:
        direction = "aligned_bearish"
    else:
        direction = "mixed"

    return {
        "confluence_score": score,
        "confluence_level": level,
        "confluence_direction": direction,
        "bullish_count": len(bullish),
        "bearish_count": len(bearish),
        "bullish_signals": bullish,
        "bearish_signals": bearish,
    }


def generate_position_advice(kline_data: dict, mode: str, current_price: float,
                              entry_price: float = 0, ai500_score: float = 0) -> dict:
    """
    持仓管理建议生成器
    输入: 指标数据、模式、当前价、入场价、AI500评分
    返回: {action, stop_loss, take_profit_1, take_profit_2, trailing_stop,
           risk_level, reasoning}
    """
    result = {}

    # 价格为0或负数的保护
    if current_price <= 0:
        return {
            "action": "无数据", "stop_loss": 0, "stop_loss_pct": 0,
            "take_profit_1": 0, "take_profit_2": 0, "trailing_stop": None,
            "risk_level": "unknown", "reasoning": ["价格数据缺失"]
        }

    sr_support = kline_data.get("sr_support", 0)
    sr_resistance = kline_data.get("sr_resistance", 0)
    sr_support_2 = kline_data.get("sr_support_2", 0)
    sr_resistance_2 = kline_data.get("sr_resistance_2", 0)
    ema21 = kline_data.get("ema21", 0)
    ema55 = kline_data.get("ema55", 0)
    bb_middle = kline_data.get("bb_middle", 0)
    bb_upper = kline_data.get("bb_upper", 0)
    bb_lower = kline_data.get("bb_lower", 0)
    confluence = kline_data.get("confluence_score", 50)
    box_high = kline_data.get("box_high", 0)
    box_low = kline_data.get("box_low", 0)
    rsi = kline_data.get("rsi", 50)
    macd_cross = kline_data.get("macd_cross", "none")
    vol_pattern = kline_data.get("vol_pattern", "normal")

    reasoning = []
    risk_level = "medium"

    if mode == "long":
        # === 止损位计算 ===
        # 方法1: 支撑位下方
        stop_candidates = []
        if sr_support > 0:
            stop_candidates.append(sr_support * 0.98)
        if ema55 > 0:
            stop_candidates.append(ema55 * 0.98)
        if box_low > 0:
            stop_candidates.append(box_low * 0.97)
        if bb_lower > 0:
            stop_candidates.append(bb_lower * 0.97)

        if stop_candidates:
            # 取最接近当前价的止损位 (不超过8%)
            valid_stops = [s for s in stop_candidates if (current_price - s) / current_price <= 0.08]
            stop_loss = max(valid_stops) if valid_stops else current_price * 0.93
        else:
            stop_loss = current_price * 0.93

        stop_loss_pct = (current_price - stop_loss) / current_price * 100

        # === 止盈位计算 ===
        tp1_candidates = []
        tp2_candidates = []
        if sr_resistance > 0:
            tp1_candidates.append(sr_resistance)
        if sr_resistance_2 > 0:
            tp2_candidates.append(sr_resistance_2)
        if box_high > 0 and box_high > current_price:
            tp1_candidates.append(box_high)
        if bb_upper > 0:
            tp1_candidates.append(bb_upper)

        take_profit_1 = min(tp1_candidates) if tp1_candidates else current_price * 1.08
        take_profit_2 = min(tp2_candidates) if tp2_candidates else current_price * 1.15

        # 移动止损: 如果浮盈>5%, 止损移到成本价
        trailing_stop = entry_price if entry_price > 0 and current_price > entry_price * 1.05 else None

        # === 操作建议 ===
        if ai500_score >= 85 and confluence >= 70:
            action = "加仓"
            reasoning.append(f"AI500={ai500_score:.0f}≥85 + 共振={confluence}≥70")
        elif ai500_score >= 75 and confluence >= 60:
            action = "持仓"
            reasoning.append(f"AI500={ai500_score:.0f}≥75 + 共振={confluence}≥60 持仓理由充分")
        elif ai500_score < 60:
            action = "离场"
            risk_level = "high"
            reasoning.append(f"AI500={ai500_score:.0f}<60 评分崩塌")
        elif confluence < 40:
            action = "减仓"
            risk_level = "high"
            reasoning.append(f"共振={confluence}<40 多指标转空")
        else:
            action = "观望持仓"
            reasoning.append(f"AI500={ai500_score:.0f} 共振={confluence} 无明确信号")

        # 额外风险提示
        if macd_cross == "death_cross":
            reasoning.append("⚠️ MACD死叉 关注回调深度")
            risk_level = "elevated"
        if vol_pattern == "vol_divergence_top":
            reasoning.append("⚠️ 量价顶背离 注意反转")
            risk_level = "elevated"
        if vol_pattern == "vol_panic":
            reasoning.append("🔴 恐慌放量 考虑减仓")
            risk_level = "high"
        if rsi > 80:
            reasoning.append(f"⚠️ RSI={rsi:.0f} 极度超买")

    else:  # short mode
        stop_candidates = []
        if sr_resistance > 0:
            stop_candidates.append(sr_resistance * 1.02)
        if ema21 > 0:
            stop_candidates.append(ema21 * 1.02)
        if box_high > 0:
            stop_candidates.append(box_high * 1.03)
        if bb_upper > 0:
            stop_candidates.append(bb_upper * 1.03)

        if stop_candidates:
            valid_stops = [s for s in stop_candidates if (s - current_price) / current_price <= 0.08]
            stop_loss = min(valid_stops) if valid_stops else current_price * 1.07
        else:
            stop_loss = current_price * 1.07

        stop_loss_pct = (stop_loss - current_price) / current_price * 100

        tp1_candidates = []
        tp2_candidates = []
        if sr_support > 0:
            tp1_candidates.append(sr_support)
        if sr_support_2 > 0:
            tp2_candidates.append(sr_support_2)
        if box_low > 0 and box_low < current_price:
            tp1_candidates.append(box_low)
        if bb_lower > 0:
            tp1_candidates.append(bb_lower)

        take_profit_1 = max(tp1_candidates) if tp1_candidates else current_price * 0.92
        take_profit_2 = max(tp2_candidates) if tp2_candidates else current_price * 0.85

        trailing_stop = entry_price if entry_price > 0 and current_price < entry_price * 0.95 else None

        if ai500_score >= 85 and confluence >= 70:
            action = "加仓"
            reasoning.append(f"AI500={ai500_score:.0f}≥85 + 共振={confluence}≥70")
        elif ai500_score >= 75 and confluence >= 60:
            action = "持仓"
            reasoning.append(f"AI500={ai500_score:.0f}≥75 + 共振={confluence}≥60")
        elif ai500_score < 60:
            action = "离场"
            risk_level = "high"
            reasoning.append(f"AI500={ai500_score:.0f}<60 评分崩塌")
        elif confluence < 40:
            action = "减仓"
            risk_level = "high"
            reasoning.append(f"共振={confluence}<40 多指标转多")
        else:
            action = "观望持仓"
            reasoning.append(f"AI500={ai500_score:.0f} 共振={confluence} 无明确信号")

        if macd_cross == "golden_cross":
            reasoning.append("⚠️ MACD金叉 关注反弹力度")
            risk_level = "elevated"
        if vol_pattern == "vol_shrink_pullback":
            reasoning.append("⚠️ 缩量回调 可能反弹")
            risk_level = "elevated"

    # 动态追踪止盈建议
    if entry_price > 0 and mode == "long":
        profit_pct = (current_price - entry_price) / entry_price * 100
        if profit_pct > 15:
            trailing_stop = current_price * 0.95  # 锁定大部分利润
            reasoning.append(f"浮盈{profit_pct:.1f}% 追踪止盈={trailing_stop:.4f}")
        elif profit_pct > 8:
            trailing_stop = current_price * 0.97
            reasoning.append(f"浮盈{profit_pct:.1f}% 追踪止盈={trailing_stop:.4f}")
    elif entry_price > 0 and mode == "short":
        profit_pct = (entry_price - current_price) / entry_price * 100
        if profit_pct > 15:
            trailing_stop = current_price * 1.05
            reasoning.append(f"浮盈{profit_pct:.1f}% 追踪止盈={trailing_stop:.4f}")
        elif profit_pct > 8:
            trailing_stop = current_price * 1.03
            reasoning.append(f"浮盈{profit_pct:.1f}% 追踪止盈={trailing_stop:.4f}")

    result["action"] = action
    result["stop_loss"] = round(stop_loss, 6)
    result["stop_loss_pct"] = round(stop_loss_pct, 1)
    result["take_profit_1"] = round(take_profit_1, 6)
    result["take_profit_2"] = round(take_profit_2, 6)
    result["trailing_stop"] = round(trailing_stop, 6) if trailing_stop else None
    result["risk_level"] = risk_level
    result["reasoning"] = reasoning

    return result


# ============================================================
# 扫描系统
# ============================================================
def fetch_symbol_data(sym, quick=False):
    """获取单个币种的详细数据
    quick=True: 只获取基础数据（1-2个API调用）
    quick=False: 获取完整数据（7个API调用）
    """
    if quick:
        # 快速模式：只获取1h K线和OI（2个API调用）
        klines_1h = fetch_json(f"{BASE_URL}/fapi/v1/klines?symbol={sym}&interval=1h&limit=24")
        oi_hist = fetch_json(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}&period=1h&limit=24")
        klines_4h = None
        funding = None
        top_ls = None
        global_ls = None
        taker = None
    else:
        # 完整模式：获取所有数据（7个API调用）
        # 1h取100根(EMA55需要>=55, 布林带挤压判断需要>=60)
        klines_4h = fetch_json(f"{BASE_URL}/fapi/v1/klines?symbol={sym}&interval=4h&limit=30")
        klines_1h = fetch_json(f"{BASE_URL}/fapi/v1/klines?symbol={sym}&interval=1h&limit=100")
        funding = fetch_json(f"{BASE_URL}/fapi/v1/fundingRate?symbol={sym}&limit=8")
        oi_hist = fetch_json(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}&period=1h&limit=24")
        top_ls = fetch_json(f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={sym}&period=1h&limit=24")
        global_ls = fetch_json(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sym}&period=1h&limit=24")
        taker = fetch_json(f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={sym}&period=1h&limit=24")
    
    # 计算指标
    kline_data = {}
    if klines_1h and len(klines_1h) > 0:
        close_prices = [float(k[4]) for k in klines_1h]
        if len(close_prices) >= 14:
            # 计算RSI
            gains = []
            losses = []
            for i in range(1, len(close_prices)):
                diff = close_prices[i] - close_prices[i-1]
                if diff > 0:
                    gains.append(diff)
                    losses.append(0)
                else:
                    gains.append(0)
                    losses.append(abs(diff))
            avg_gain = sum(gains[-14:]) / 14
            avg_loss = sum(losses[-14:]) / 14
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                kline_data["rsi"] = 100 - (100 / (1 + rs))
            else:
                kline_data["rsi"] = 100
        
        if len(close_prices) >= 2:
            kline_data["price_change_24h"] = (close_prices[-1] - close_prices[0]) / close_prices[0] * 100
        
        if len(close_prices) >= 4:
            kline_data["price_change_4h"] = (close_prices[-1] - close_prices[-4]) / close_prices[-4] * 100
        
        if len(close_prices) >= 20:
            ema20 = close_prices[0]
            multiplier = 2 / (20 + 1)
            for cp in close_prices[1:]:
                ema20 = (cp - ema20) * multiplier + ema20
            kline_data["ema20"] = ema20
        
        if len(close_prices) >= 50:
            ema50 = close_prices[0]
            multiplier50 = 2 / (50 + 1)
            for cp in close_prices[1:]:
                ema50 = (cp - ema50) * multiplier50 + ema50
            kline_data["ema50"] = ema50
        
        kline_data["close"] = close_prices[-1]

        # 计算连续阴线 (consecutive_red)
        consecutive_red = 0
        for i in range(len(klines_1h)-1, 0, -1):
            o = float(klines_1h[i][1])
            c = float(klines_1h[i][4])
            if c < o:
                consecutive_red += 1
            else:
                break
        kline_data["consecutive_red"] = consecutive_red

        # === 布林带 ===
        bb_data = calc_bollinger_bands(klines_1h, period=20, std_dev=2.0)
        kline_data.update(bb_data)

        # === 多周期EMA (EMA9/21/55 + 排列状态) ===
        ema_multi = calc_ema_multi(klines_1h)
        kline_data.update(ema_multi)

        # === 成交量质量 ===
        vol_quality = calc_volume_quality(klines_1h)
        kline_data.update(vol_quality)

        # === 支撑阻力位 ===
        sr_data = calc_support_resistance(klines_1h)
        kline_data.update(sr_data)

        # === MACD ===
        macd_data = calc_macd(klines_1h, fast=12, slow=26, signal=9)
        kline_data.update(macd_data)

        # === 量价配合模式 ===
        vol_pattern = calc_volume_pattern(klines_1h)
        kline_data.update(vol_pattern)

        # === 箱体/震荡区间检测 ===
        box_data = detect_consolidation_box(klines_1h, min_touches=3)
        kline_data.update(box_data)

    # 传递4h K线原始数据给 long_mode 连续上涨检测
    if klines_4h:
        kline_data["klines_4h"] = klines_4h

    funding_data = {}
    if funding and len(funding) > 0:
        funding_data["rate"] = float(funding[-1]["fundingRate"])
    
    oi_data = {}
    if oi_hist and len(oi_hist) >= 2:
        oi_start = float(oi_hist[0]["sumOpenInterest"])
        oi_end = float(oi_hist[-1]["sumOpenInterest"])
        if oi_start > 0:
            oi_data["oi_change_pct"] = (oi_end - oi_start) / oi_start * 100
            oi_data["oi_change_24h"] = oi_data["oi_change_pct"]  # alias for short_mode compatibility
        
        if len(oi_hist) >= 4:
            oi_4h_ago = float(oi_hist[-4]["sumOpenInterest"])
            if oi_4h_ago > 0:
                oi_data["oi_change_4h_pct"] = (oi_end - oi_4h_ago) / oi_4h_ago * 100
                oi_data["oi_change_4h"] = oi_data["oi_change_4h_pct"]  # alias for short_mode compatibility
    
    ls_data = {}
    if top_ls and len(top_ls) > 0:
        ls_data["long_ratio"] = float(top_ls[-1]["longAccount"]) * 100
    
    taker_data = {}
    if taker and len(taker) > 0:
        buy_vol = float(taker[-1].get("buySellRatio", 1))
        taker_data["volume_ratio"] = buy_vol
    
    return kline_data, funding_data, oi_data, ls_data, taker_data

def calculate_score(sym, mode, kline_data, funding_data, oi_data, ls_data, taker_data):
    """
    计算评分 v2.0 (2026-06-12 增强)
    新增: 启动阶段检测 + 逼空检测 + 流动性检测 + 风控预警
    """
    # 处理缺失数据 (快速模式下可能为空)
    if not funding_data:
        funding_data = {"rate": 0}
    if not ls_data:
        ls_data = {"long_ratio": 50}  # 默认50%
    if not taker_data:
        taker_data = {"volume_ratio": 1}  # 默认1:1
    
    # Build ema_data from kline_data for short_mode
    ema_data = {
        "ema20": kline_data.get("ema20", 0),
        "ema50": kline_data.get("ema50", 0),
    }
    
    if mode == "long":
        score, details, reasons, pattern, stop_loss_pct = calc_long_score(sym, kline_data, funding_data, oi_data, ls_data, taker_data)
    else:
        score, details, reasons, pattern, stop_loss_pct = calc_short_score(sym, kline_data, funding_data, oi_data, ls_data, taker_data, ema_data)
    
    # OI资金流分析
    oi_bonus, oi_reasons, oi_analysis = get_oi_quality_score(sym, mode, BINANCE_API_KEY, BINANCE_API_SECRET)
    if oi_bonus != 0:
        score += oi_bonus
        reasons.extend(oi_reasons)
        details['oi_phase'] = oi_analysis.get('phase', 'unknown')
        details['oi_quality'] = oi_analysis.get('quality_score', 0)
    
    # 方向过滤 - v3.2: 从直接过滤改为扣分
    if oi_analysis.get('signal_direction') != 'neutral' and oi_analysis['signal_direction'] != mode:
        if oi_analysis['quality_score'] >= 20:
            # 不再直接返回None，而是扣分
            penalty = min(oi_analysis['quality_score'] // 2, 20)
            score -= penalty
            reasons.append(f"⚠️ OI信号方向{oi_analysis['signal_direction']}≠{mode} 扣{penalty}分")
    
    # 应用记忆反馈(传入因子数据以支持因子扣分)
    factors_for_feedback = {
        "funding_score": details.get("funding_rate", 0),
        "smart_money_score": details.get("oi_quality", 0),
        "oi_change": details.get("oi_change", 0),
    }
    score = adjust_score(sym, mode.upper(), score, factors_for_feedback)
    
    # === 多指标共振评分 ===
    confluence = calc_confluence_score(kline_data, mode=mode)
    details["confluence"] = confluence
    
    # === 持仓管理建议 ===
    current_price = kline_data.get("close", 0)
    position_advice = generate_position_advice(
        kline_data, mode, current_price,
        entry_price=0,  # 入场价由调用方传入
        ai500_score=score
    )
    details["position_advice"] = position_advice
    
    # === 2026-06-12 增强检测 ===
    
    # 启动阶段检测 (需要K线数据)
    if kline_data:
        startup = detect_startup_phase(kline_data, oi_data or [])
        if startup["score"] > 0:
            score += startup["score"]
            reasons.extend([f"📊{r}" for r in startup["reasons"]])
            details["startup_phase"] = startup["phase"]
            details["startup_score"] = startup["score"]
            # 启动前兆信号加成
            if startup["phase"] == "pre_burst":
                score += 10  # 额外加成
                reasons.append("🎯 启动前兆!")
    
    # === v3.2 新增三种启动模式检测 ===
    if kline_data:
        startup_patterns = detect_startup_patterns(kline_data, oi_data)
        if startup_patterns["pattern"] != "none":
            pattern_score = startup_patterns["score"]
            pattern_name = startup_patterns["pattern"]
            pattern_reasons = startup_patterns["reasons"]
            
            # 加分（与原有启动检测取较高分）
            pattern_bonus = min(pattern_score // 5, 20)  # 最高加20分
            score += pattern_bonus
            reasons.extend([f"🚀{r}" for r in pattern_reasons[:3]])  # 最多显示3个原因
            
            details["startup_pattern"] = pattern_name
            details["startup_pattern_score"] = pattern_score
            details["startup_pattern_details"] = startup_patterns["details"]
            
            # 模式加成
            if pattern_name == "washout_recovery":
                score += 5
                reasons.append("🔄 清洗后反转模式")
            elif pattern_name == "silent_accumulation":
                score += 8
                reasons.append("📦 静默积累模式")
            elif pattern_name == "silent_start":
                score += 3
                reasons.append("🔇 无量启动模式")
    
    # 成交量异动检测
    if kline_data:
        vol_anomaly = detect_volume_anomaly(kline_data)
        if vol_anomaly["is_anomaly"]:
            details["volume_anomaly"] = vol_anomaly["ratio"]
            details["volume_signal"] = vol_anomaly["signal"]
            if vol_anomaly["ratio"] > 3:
                reasons.append(f"🔥 {vol_anomaly['signal']}")
            elif vol_anomaly["ratio"] < 0.5:
                reasons.append(f"⚠️ {vol_anomaly['signal']}")
    
    # 缩量逼空检测 (做空模式)
    if mode == "short" and kline_data and funding_data:
        squeeze = detect_short_squeeze(kline_data, funding_data)
        if squeeze["score"] > 0:
            details["squeeze_risk"] = squeeze["risk"]
            details["squeeze_score"] = squeeze["score"]
            if squeeze["risk"] in ("high", "extreme"):
                score += squeeze["score"]
                reasons.extend([f"🧲{r}" for r in squeeze["reasons"]])
    
    # === v3.2 回调vs反转分析 (做空模式) ===
    if mode == "short" and kline_data:
        # 获取资金费率
        funding_rate = funding_data.get("rate", 0) if funding_data else 0
        
        # 获取多空比
        ls_ratio_data = None
        if ls_data:
            ls_ratio_data = {
                "long": ls_data.get("long_ratio", 50),
                "short": 100 - ls_data.get("long_ratio", 50),
                "ratio": ls_data.get("long_ratio", 50) / (100 - ls_data.get("long_ratio", 50)) if ls_data.get("long_ratio", 50) < 100 else 1,
                "retail_ratio": ls_data.get("retail_ratio", 1.0)
            }
        
        # 运行回调vs反转分析
        pullback_analysis = analyze_pullback_vs_reversal(
            kline_data, oi_data, funding_rate, ls_ratio_data
        )
        
        details["pullback_analysis"] = pullback_analysis
        
        # 根据分析结果调整做空评分
        if pullback_analysis["verdict"] == "pullback":
            # 判断为回调，做空扣分
            penalty = min(pullback_analysis["score"] // 2, 25)
            score -= penalty
            reasons.extend([f"📈{r}" for r in pullback_analysis["reasons"][:3]])
        elif pullback_analysis["verdict"] == "reversal":
            # 判断为反转，做空加分
            bonus = min(abs(pullback_analysis["score"]) // 2, 20)
            score += bonus
            reasons.extend([f"📉{r}" for r in pullback_analysis["reasons"][:3]])
        else:
            # 不确定，小幅扣分
            score -= 5
            reasons.append("⚪ 回调/反转信号不明，谨慎做空")
    
    # === v3.2 上涨质量分析 (做多模式) ===
    if mode == "long" and kline_data:
        # 获取资金费率
        funding_rate = funding_data.get("rate", 0) if funding_data else 0
        
        # 获取多空比
        ls_ratio_data = None
        if ls_data:
            ls_ratio_data = {
                "long": ls_data.get("long_ratio", 50),
                "short": 100 - ls_data.get("long_ratio", 50),
                "ratio": ls_data.get("long_ratio", 50) / (100 - ls_data.get("long_ratio", 50)) if ls_data.get("long_ratio", 50) < 100 else 1,
                "retail_ratio": ls_data.get("retail_ratio", 1.0)
            }
        
        # 运行上涨质量分析
        uptrend_analysis = analyze_uptrend_quality(
            kline_data, oi_data, funding_rate, ls_ratio_data
        )
        
        details["uptrend_analysis"] = uptrend_analysis
        
        # 根据分析结果调整做多评分
        if uptrend_analysis["verdict"] == "healthy":
            # 判断为健康上涨，做多加分
            bonus = min(uptrend_analysis["score"] // 3, 15)
            score += bonus
            reasons.extend([f"📈{r}" for r in uptrend_analysis["reasons"][:3]])
        elif uptrend_analysis["verdict"] == "overheated":
            # 判断为过热，做多扣分
            penalty = min(abs(uptrend_analysis["score"]) // 2, 25)
            score -= penalty
            reasons.extend([f"🔥{r}" for r in uptrend_analysis["reasons"][:3]])
        elif uptrend_analysis["verdict"] == "weak":
            # 判断为弱势上涨，做多扣分
            penalty = min(abs(uptrend_analysis["score"]) // 2, 15)
            score -= penalty
            reasons.extend([f"📉{r}" for r in uptrend_analysis["reasons"][:3]])
        else:
            # 不确定，小幅扣分
            score -= 3
            reasons.append("⚪ 上涨质量不明，谨慎做多")
    
    # 流动性风险检测
    if kline_data:
        liq = detect_liquidity_risk(kline_data)
        if liq["risk"] in ("high", "extreme"):
            score -= liq["score"]  # 流动性差扣分
            details["liquidity_risk"] = liq["risk"]
            reasons.extend([f"💧{r}" for r in liq["reasons"]])
    
    # 风控预警
    risk_alerts = check_risk_alerts(score, details, mode)
    if risk_alerts:
        details["risk_alerts"] = risk_alerts
    
    # 评分上限保护
    score = min(score, 100)
    
    return score, details, reasons, pattern, stop_loss_pct, None


# ============================================================
# 增强检测模块 v2.0 (2026-06-12)
# ============================================================

def detect_volume_anomaly(klines: dict) -> dict:
    """
    成交量异动检测
    返回: {"is_anomaly": bool, "ratio": float, "signal": str}
    """
    closes = klines.get("closes", [])
    volumes = klines.get("volumes", [])
    
    if len(volumes) < 20:
        return {"is_anomaly": False, "ratio": 0, "signal": "数据不足"}
    
    # 计算20周期均量
    avg_vol = sum(volumes[-20:]) / 20
    current_vol = volumes[-1]
    
    if avg_vol <= 0:
        return {"is_anomaly": False, "ratio": 0, "signal": "均量为零"}
    
    ratio = current_vol / avg_vol
    
    signal = "正常"
    if ratio > 5:
        signal = "天量异动"
    elif ratio > 3:
        signal = "大幅放量"
    elif ratio > 2:
        signal = "显著放量"
    elif ratio < 0.3:
        signal = "极度缩量"
    elif ratio < 0.5:
        signal = "明显缩量"
    
    return {
        "is_anomaly": ratio > 2 or ratio < 0.5,
        "ratio": ratio,
        "signal": signal,
    }


def detect_startup_phase(klines: dict, oi_data: list) -> dict:
    """
    启动阶段检测: OI积累 + 成交量放大 + 价格横盘 = 吸筹完成信号
    返回: {"phase": str, "score": int, "reasons": list}
    """
    score = 0
    reasons = []
    phase = "unknown"
    
    closes = klines.get("closes", [])
    volumes = klines.get("volumes", [])
    
    if len(closes) < 10 or len(volumes) < 10:
        return {"phase": "insufficient_data", "score": 0, "reasons": ["数据不足"]}
    
    # 1. 价格横盘检测: 最近10根K线振幅 < 5%
    recent_closes = closes[-10:]
    price_range = (max(recent_closes) - min(recent_closes)) / min(recent_closes) * 100 if min(recent_closes) > 0 else 999
    
    if price_range < 3:
        score += 15
        reasons.append(f"窄幅横盘{price_range:.1f}%")
    elif price_range < 5:
        score += 10
        reasons.append(f"小幅整理{price_range:.1f}%")
    
    # 2. 成交量放大检测: 当前量 > 20周期均量的1.5倍
    if len(volumes) >= 20:
        avg_vol = sum(volumes[-20:]) / 20
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 0
        
        if vol_ratio > 3:
            score += 20
            reasons.append(f"量比{vol_ratio:.1f}x 天量异动")
        elif vol_ratio > 2:
            score += 15
            reasons.append(f"量比{vol_ratio:.1f}x 显著放量")
        elif vol_ratio > 1.5:
            score += 10
            reasons.append(f"量比{vol_ratio:.1f}x 温和放量")
    
    # 3. OI积累检测: OI连续增加
    if oi_data and len(oi_data) >= 3:
        oi_values = [float(o.get("sumOpenInterestValue", 0)) for o in oi_data if "sumOpenInterestValue" in o]
        if len(oi_values) >= 3:
            oi_increasing = all(oi_values[i] > oi_values[i-1] for i in range(-3, 0))
            oi_change = (oi_values[-1] - oi_values[-3]) / oi_values[-3] * 100 if oi_values[-3] > 0 else 0
            
            if oi_increasing and oi_change > 10:
                score += 20
                reasons.append(f"OI连续3周期增加{oi_change:.1f}%")
            elif oi_increasing and oi_change > 5:
                score += 15
                reasons.append(f"OI持续流入{oi_change:.1f}%")
    
    # 4. 综合判断
    if score >= 40:
        phase = "pre_burst"  # 爆发前兆
    elif score >= 25:
        phase = "accumulation"  # 吸筹阶段
    elif score >= 10:
        phase = "consolidation"  # 整理阶段
    else:
        phase = "inactive"  # 无明显信号
    
    return {"phase": phase, "score": score, "reasons": reasons}


def detect_short_squeeze(klines: dict, funding: dict) -> dict:
    """
    缩量逼空检测: 高费率 + 成交量萎缩 = 空头被轧风险
    返回: {"risk": str, "score": int, "reasons": list}
    """
    score = 0
    reasons = []
    risk = "low"
    
    funding_rate = funding.get("rate", 0)
    volumes = klines.get("volumes", [])
    
    if len(volumes) < 10:
        return {"risk": "unknown", "score": 0, "reasons": ["数据不足"]}
    
    # 1. 资金费率异常高
    if funding_rate > 0.005:  # >0.5%
        score += 25
        reasons.append(f"费率极端高{funding_rate*100:.3f}%")
        risk = "extreme"
    elif funding_rate > 0.002:  # >0.2%
        score += 15
        reasons.append(f"费率偏高{funding_rate*100:.3f}%")
        risk = "high"
    elif funding_rate > 0.001:  # >0.1%
        score += 5
        reasons.append(f"费率偏高{funding_rate*100:.3f}%")
    
    # 2. 成交量萎缩 (当前量 < 10周期均量的0.7倍)
    if len(volumes) >= 10:
        avg_vol_10 = sum(volumes[-10:]) / 10
        vol_ratio = volumes[-1] / avg_vol_10 if avg_vol_10 > 0 else 1
        
        if vol_ratio < 0.5:
            score += 20
            reasons.append(f"缩量严重{vol_ratio:.1f}x")
        elif vol_ratio < 0.7:
            score += 10
            reasons.append(f"缩量{vol_ratio:.1f}x")
    
    # 3. 综合判断
    if score >= 40:
        risk = "extreme"
    elif score >= 25:
        risk = "high"
    elif score >= 15:
        risk = "medium"
    else:
        risk = "low"
    
    return {"risk": risk, "score": score, "reasons": reasons}


def detect_liquidity_risk(klines: dict) -> dict:
    """
    流动性风险检测: 成交量过低 = 可能无法及时止损
    返回: {"risk": str, "score": int, "reasons": list}
    """
    score = 0
    reasons = []
    risk = "low"
    
    volumes = klines.get("volumes", [])
    
    if len(volumes) < 20:
        return {"risk": "unknown", "score": 0, "reasons": ["数据不足"]}
    
    avg_vol_20 = sum(volumes[-20:]) / 20
    current_vol = volumes[-1]
    
    if avg_vol_20 <= 0:
        return {"risk": "unknown", "score": 0, "reasons": ["均量为零"]}
    
    vol_ratio = current_vol / avg_vol_20
    
    if vol_ratio < 0.2:
        score += 25
        reasons.append(f"流动性枯竭{vol_ratio:.1f}x")
        risk = "extreme"
    elif vol_ratio < 0.3:
        score += 20
        reasons.append(f"流动性极差{vol_ratio:.1f}x")
        risk = "high"
    elif vol_ratio < 0.5:
        score += 10
        reasons.append(f"流动性偏低{vol_ratio:.1f}x")
        risk = "medium"
    
    return {"risk": risk, "score": score, "reasons": reasons}


def detect_washout_recovery(klines: dict, oi_data: list = None) -> dict:
    """
    模式1：清洗后反转检测
    特征：前6h跌幅≥8%，高波动≥15%，启动时量比≥1.0x，价格企稳
    返回: {"detected": bool, "score": int, "reasons": list}
    """
    score = 0
    reasons = []
    
    closes = klines.get("closes", [])
    volumes = klines.get("volumes", [])
    highs = klines.get("highs", [])
    lows = klines.get("lows", [])
    
    # 需要至少24根1h K线（6小时=6根，但用更多数据更准）
    if len(closes) < 24 or len(volumes) < 24:
        return {"detected": False, "score": 0, "reasons": ["数据不足"]}
    
    # 1. 计算前6小时跌幅（用最近24根中的前6根）
    pre_6h_closes = closes[-12:-6]  # 6小时前的数据
    pre_6h_min = min(closes[-12:-6]) if len(closes) >= 12 else closes[0]
    current_price = closes[-1]
    
    if pre_6h_closes[0] > 0:
        pre_6h_drop = (pre_6h_min - pre_6h_closes[0]) / pre_6h_closes[0] * 100
    else:
        pre_6h_drop = 0
    
    # 2. 计算前6小时波动（振幅）
    if len(highs) >= 12 and len(lows) >= 12:
        pre_6h_highs = highs[-12:-6]
        pre_6h_lows = lows[-12:-6]
        pre_6h_volatility = (max(pre_6h_highs) - min(pre_6h_lows)) / min(pre_6h_lows) * 100 if min(pre_6h_lows) > 0 else 0
    else:
        pre_6h_volatility = 0
    
    # 3. 计算启动时量比
    avg_vol = sum(volumes[-24:]) / 24 if len(volumes) >= 24 else sum(volumes) / len(volumes)
    current_vol = volumes[-1]
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0
    
    # 4. 价格企稳检测：最近3根K线不再创新低
    recent_lows = lows[-3:] if len(lows) >= 3 else lows
    price_stable = all(recent_lows[i] >= recent_lows[i-1] for i in range(1, len(recent_lows))) if len(recent_lows) >= 2 else False
    
    # 评分逻辑
    if pre_6h_drop <= -15:
        score += 25
        reasons.append(f"深度清洗{pre_6h_drop:.1f}%")
    elif pre_6h_drop <= -10:
        score += 20
        reasons.append(f"显著清洗{pre_6h_drop:.1f}%")
    elif pre_6h_drop <= -8:
        score += 15
        reasons.append(f"中度清洗{pre_6h_drop:.1f}%")
    
    if pre_6h_volatility >= 20:
        score += 20
        reasons.append(f"高波动{pre_6h_volatility:.1f}%")
    elif pre_6h_volatility >= 15:
        score += 15
        reasons.append(f"波动放大{pre_6h_volatility:.1f}%")
    elif pre_6h_volatility >= 10:
        score += 10
        reasons.append(f"波动正常{pre_6h_volatility:.1f}%")
    
    if vol_ratio >= 2.0:
        score += 20
        reasons.append(f"放量启动{vol_ratio:.1f}x")
    elif vol_ratio >= 1.5:
        score += 15
        reasons.append(f"量比放大{vol_ratio:.1f}x")
    elif vol_ratio >= 1.0:
        score += 10
        reasons.append(f"量比正常{vol_ratio:.1f}x")
    
    if price_stable:
        score += 15
        reasons.append("价格企稳")
    
    # 综合判断
    detected = score >= 50 and pre_6h_drop <= -8 and vol_ratio >= 1.0
    
    return {"detected": detected, "score": score, "reasons": reasons}


def detect_silent_accumulation(klines: dict) -> dict:
    """
    模式2：静默积累检测
    特征：前6h波动≤5%，成交量持续萎缩（量比<0.7），突破时量比≥1.2x，价格突破前高
    返回: {"detected": bool, "score": int, "reasons": list}
    """
    score = 0
    reasons = []
    
    closes = klines.get("closes", [])
    volumes = klines.get("volumes", [])
    highs = klines.get("highs", [])
    
    if len(closes) < 24 or len(volumes) < 24:
        return {"detected": False, "score": 0, "reasons": ["数据不足"]}
    
    # 1. 计算前6小时波动
    if len(highs) >= 12:
        pre_6h_highs = highs[-12:-6]
        pre_6h_lows = klines.get("lows", closes[-12:-6])[-12:-6]
        pre_6h_volatility = (max(pre_6h_highs) - min(pre_6h_lows)) / min(pre_6h_lows) * 100 if min(pre_6h_lows) > 0 else 0
    else:
        pre_6h_volatility = 0
    
    # 2. 成交量萎缩检测：前6小时成交量持续下降
    pre_6h_volumes = volumes[-12:-6]
    avg_pre_vol = sum(pre_6h_volumes) / len(pre_6h_volumes) if pre_6h_volumes else 1
    
    # 检查成交量是否连续下降
    vol_declining = 0
    for i in range(1, len(pre_6h_volumes)):
        if pre_6h_volumes[i] < pre_6h_volumes[i-1]:
            vol_declining += 1
    
    vol_declining_pct = vol_declining / (len(pre_6h_volumes) - 1) * 100 if len(pre_6h_volumes) > 1 else 0
    
    # 3. 突破时量比
    avg_vol = sum(volumes[-24:]) / 24
    current_vol = volumes[-1]
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0
    
    # 4. 价格突破前高检测
    recent_high = max(highs[-6:]) if len(highs) >= 6 else closes[-1]
    prev_high = max(highs[-12:-6]) if len(highs) >= 12 else closes[-1]
    breakout = recent_high > prev_high * 1.01  # 突破前高1%以上
    
    # 评分逻辑
    if pre_6h_volatility <= 3:
        score += 20
        reasons.append(f"极度窄幅{pre_6h_volatility:.1f}%")
    elif pre_6h_volatility <= 5:
        score += 15
        reasons.append(f"窄幅整理{pre_6h_volatility:.1f}%")
    elif pre_6h_volatility <= 8:
        score += 10
        reasons.append(f"小幅整理{pre_6h_volatility:.1f}%")
    
    if vol_declining_pct >= 80:
        score += 20
        reasons.append(f"成交量持续萎缩{vol_declining_pct:.0f}%")
    elif vol_declining_pct >= 60:
        score += 15
        reasons.append(f"成交量萎缩{vol_declining_pct:.0f}%")
    
    if vol_ratio >= 2.0:
        score += 20
        reasons.append(f"突破放量{vol_ratio:.1f}x")
    elif vol_ratio >= 1.5:
        score += 15
        reasons.append(f"放量突破{vol_ratio:.1f}x")
    elif vol_ratio >= 1.2:
        score += 10
        reasons.append(f"温和放量{vol_ratio:.1f}x")
    
    if breakout:
        score += 20
        reasons.append("价格突破前高")
    
    # 综合判断
    detected = score >= 50 and pre_6h_volatility <= 8 and vol_ratio >= 1.0
    
    return {"detected": detected, "score": score, "reasons": reasons}


def detect_silent_start(klines: dict, oi_data: list = None) -> dict:
    """
    模式3：无量启动检测
    特征：启动量比≥0.5x（降低门槛），OI变化≥0%（不要求增加），价格连续上涨2根K线
    返回: {"detected": bool, "score": int, "reasons": list}
    """
    score = 0
    reasons = []
    
    closes = klines.get("closes", [])
    volumes = klines.get("volumes", [])
    
    if len(closes) < 12 or len(volumes) < 12:
        return {"detected": False, "score": 0, "reasons": ["数据不足"]}
    
    # 1. 计算启动量比（降低门槛）
    avg_vol = sum(volumes[-12:]) / 12
    current_vol = volumes[-1]
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0
    
    # 2. 价格连续上涨检测
    consecutive_up = 0
    for i in range(-1, -4, -1):  # 检查最近3根K线
        if i-1 >= -len(closes) and closes[i] > closes[i-1]:
            consecutive_up += 1
        else:
            break
    
    # 3. OI变化检测（不要求增加，只要求不大幅下降）
    oi_stable = True
    oi_change = 0
    if oi_data and len(oi_data) >= 3:
        oi_values = [float(o.get("sumOpenInterestValue", 0)) for o in oi_data if "sumOpenInterestValue" in o]
        if len(oi_values) >= 3:
            oi_change = (oi_values[-1] - oi_values[-3]) / oi_values[-3] * 100 if oi_values[-3] > 0 else 0
            oi_stable = oi_change >= -5  # OI下降不超过5%
    
    # 4. 价格涨幅
    price_change_1h = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0
    price_change_4h = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0
    
    # 评分逻辑
    if vol_ratio >= 1.0:
        score += 15
        reasons.append(f"量比正常{vol_ratio:.1f}x")
    elif vol_ratio >= 0.7:
        score += 10
        reasons.append(f"量比偏低{vol_ratio:.1f}x")
    elif vol_ratio >= 0.5:
        score += 5
        reasons.append(f"量比低{vol_ratio:.1f}x")
    
    if consecutive_up >= 3:
        score += 25
        reasons.append(f"连续上涨{consecutive_up}根")
    elif consecutive_up >= 2:
        score += 15
        reasons.append(f"连续上涨{consecutive_up}根")
    
    if oi_stable:
        score += 10
        if oi_change >= 3:
            score += 10
            reasons.append(f"OI增加{oi_change:.1f}%")
        elif oi_change >= 0:
            reasons.append("OI稳定")
        else:
            reasons.append(f"OI小幅下降{oi_change:.1f}%")
    
    if price_change_1h >= 2:
        score += 15
        reasons.append(f"1h涨{price_change_1h:.1f}%")
    elif price_change_1h >= 1:
        score += 10
        reasons.append(f"1h涨{price_change_1h:.1f}%")
    
    if price_change_4h >= 5:
        score += 15
        reasons.append(f"4h涨{price_change_4h:.1f}%")
    elif price_change_4h >= 3:
        score += 10
        reasons.append(f"4h涨{price_change_4h:.1f}%")
    
    # 综合判断
    detected = score >= 40 and consecutive_up >= 2 and vol_ratio >= 0.5
    
    return {"detected": detected, "score": score, "reasons": reasons}


def detect_startup_patterns(klines: dict, oi_data: list = None) -> dict:
    """
    综合启动模式检测：同时运行三种模式检测，返回最匹配的
    返回: {"pattern": str, "score": int, "reasons": list, "details": dict}
    """
    patterns = []
    
    # 模式1：清洗后反转
    washout = detect_washout_recovery(klines, oi_data)
    if washout["detected"]:
        patterns.append(("washout_recovery", washout["score"], washout["reasons"]))
    
    # 模式2：静默积累
    silent_acc = detect_silent_accumulation(klines)
    if silent_acc["detected"]:
        patterns.append(("silent_accumulation", silent_acc["score"], silent_acc["reasons"]))
    
    # 模式3：无量启动
    silent_start = detect_silent_start(klines, oi_data)
    if silent_start["detected"]:
        patterns.append(("silent_start", silent_start["score"], silent_start["reasons"]))
    
    # 返回最高分的模式
    if patterns:
        best = max(patterns, key=lambda x: x[1])
        return {
            "pattern": best[0],
            "score": best[1],
            "reasons": best[2],
            "details": {
                "washout_recovery": washout,
                "silent_accumulation": silent_acc,
                "silent_start": silent_start
            }
        }
    
    return {
        "pattern": "none",
        "score": 0,
        "reasons": ["无启动信号"],
        "details": {
            "washout_recovery": washout,
            "silent_accumulation": silent_acc,
            "silent_start": silent_start
        }
    }


def analyze_pullback_vs_reversal(klines: dict, oi_data: list = None, 
                                  funding_rate: float = 0, 
                                  ls_ratio: dict = None, 
                                  taker_ratio: float = 1.0) -> dict:
    """
    多维度分析：回调 vs 反转
    用于判断当前下跌是正常回调还是趋势反转
    
    返回:
    {
        "verdict": "pullback" | "reversal" | "uncertain",
        "confidence": 0-100,
        "score": -50 to +50 (正=回调, 负=反转),
        "reasons": [...],
        "dimensions": {...}
    }
    """
    score = 0
    reasons = []
    dimensions = {}
    
    closes = klines.get("closes", [])
    volumes = klines.get("volumes", [])
    
    if len(closes) < 12:
        return {"verdict": "uncertain", "confidence": 0, "score": 0, 
                "reasons": ["数据不足"], "dimensions": {}}
    
    # ============================================
    # 维度1: OI+价格组合分析 (权重: 30分)
    # ============================================
    oi_score = 0
    if oi_data and len(oi_data) >= 5:
        oi_values = [float(o.get("sumOpenInterest", 0)) for o in oi_data]
        
        # OI 4h变化
        oi_4h_change = (oi_values[-1] - oi_values[-5]) / oi_values[-5] * 100 if oi_values[-5] > 0 else 0
        
        # 价格4h变化
        price_4h_change = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0
        
        # OI+价格组合判断
        if oi_4h_change > 5 and price_4h_change < -5:
            # OI增加+价格跌 = 新资金抄底 → 回调
            oi_score = 25
            reasons.append(f"新资金抄底: OI{oi_4h_change:+.1f}% 价格{price_4h_change:+.1f}%")
        elif oi_4h_change > 0 and price_4h_change < -3:
            # OI小幅增加+价格跌 = 观望中
            oi_score = 10
            reasons.append(f"资金观望: OI{oi_4h_change:+.1f}% 价格{price_4h_change:+.1f}%")
        elif oi_4h_change < -5 and price_4h_change < -5:
            # OI减少+价格跌 = 获利了结 → 可能反转
            oi_score = -25
            reasons.append(f"获利了结: OI{oi_4h_change:+.1f}% 价格{price_4h_change:+.1f}%")
        elif oi_4h_change < -3 and price_4h_change < -3:
            # OI小幅减少+价格跌 = 部分出逃
            oi_score = -15
            reasons.append(f"资金出逃: OI{oi_4h_change:+.1f}% 价格{price_4h_change:+.1f}%")
        else:
            oi_score = 0
            reasons.append(f"OI中性: OI{oi_4h_change:+.1f}% 价格{price_4h_change:+.1f}%")
        
        dimensions["oi_price"] = {
            "oi_4h_change": oi_4h_change,
            "price_4h_change": price_4h_change,
            "score": oi_score
        }
    
    score += oi_score
    
    # ============================================
    # 维度2: 资金费率成本 (权重: 20分)
    # ============================================
    funding_score = 0
    if funding_rate < -0.3:
        # 负费率很高，做空成本极高
        funding_score = -20
        reasons.append(f"负费率极高{funding_rate*100:.2f}%，做空成本高")
    elif funding_rate < -0.1:
        # 负费率，做空成本较高
        funding_score = -10
        reasons.append(f"负费率{funding_rate*100:.2f}%，做空成本较高")
    elif funding_rate > 0.3:
        # 正费率很高，做多成本高
        funding_score = 15
        reasons.append(f"正费率{funding_rate*100:.2f}%，做多成本高")
    elif funding_rate > 0.1:
        # 正费率，做多成本较高
        funding_score = 8
        reasons.append(f"正费率{funding_rate*100:.2f}%，做多成本较高")
    else:
        funding_score = 0
        reasons.append(f"费率中性{funding_rate*100:.2f}%")
    
    dimensions["funding"] = {
        "rate": funding_rate,
        "score": funding_score
    }
    
    score += funding_score
    
    # ============================================
    # 维度3: 大户多空比 (权重: 20分)
    # ============================================
    whale_score = 0
    if ls_ratio:
        whale_long = ls_ratio.get("long", 50)
        whale_short = ls_ratio.get("short", 50)
        whale_ratio = ls_ratio.get("ratio", 1.0)
        
        if whale_long > 60:
            # 大户偏多 → 回调
            whale_score = 15
            reasons.append(f"大户偏多{whale_long:.0f}%，聪明钱看多")
        elif whale_long > 55:
            whale_score = 8
            reasons.append(f"大户略偏多{whale_long:.0f}%")
        elif whale_short > 60:
            # 大户偏空 → 反转
            whale_score = -15
            reasons.append(f"大户偏空{whale_short:.0f}%，聪明钱看空")
        elif whale_short > 55:
            whale_score = -8
            reasons.append(f"大户略偏空{whale_short:.0f}%")
        else:
            whale_score = 0
            reasons.append(f"大户中性 多{whale_long:.0f}%/空{whale_short:.0f}%")
        
        dimensions["whale"] = {
            "long_pct": whale_long,
            "short_pct": whale_short,
            "ratio": whale_ratio,
            "score": whale_score
        }
    
    score += whale_score
    
    # ============================================
    # 维度4: 散户情绪 (权重: 15分)
    # ============================================
    retail_score = 0
    if ls_ratio:
        retail_ratio = ls_ratio.get("retail_ratio", 1.0)
        
        if retail_ratio > 2.0:
            # 散户极度看多 → 危险，可能反转
            retail_score = -15
            reasons.append(f"散户极度看多{retail_ratio:.2f}，过度乐观")
        elif retail_ratio > 1.5:
            # 散户偏多 → 可能回调
            retail_score = -8
            reasons.append(f"散户偏多{retail_ratio:.2f}")
        elif retail_ratio < 0.5:
            # 散户极度看空 → 可能反弹
            retail_score = 15
            reasons.append(f"散户极度看空{retail_ratio:.2f}，可能反弹")
        elif retail_ratio < 0.7:
            retail_score = 8
            reasons.append(f"散户偏空{retail_ratio:.2f}")
        else:
            retail_score = 0
            reasons.append(f"散户中性{retail_ratio:.2f}")
        
        dimensions["retail"] = {
            "ratio": retail_ratio,
            "score": retail_score
        }
    
    score += retail_score
    
    # ============================================
    # 维度5: 成交量恐慌度 (权重: 15分)
    # ============================================
    volume_score = 0
    if len(volumes) >= 12:
        # 计算成交量变化
        vol_now = volumes[-1]
        vol_4h_avg = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else vol_now
        
        vol_ratio_4h = vol_now / vol_4h_avg if vol_4h_avg > 0 else 1
        
        # 价格变化
        price_change_1h = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0
        
        if vol_ratio_4h > 3 and price_change_1h < -5:
            # 量价齐跌，恐慌抛售 → 可能反转
            volume_score = -15
            reasons.append(f"恐慌抛售: 量比{vol_ratio_4h:.1f}x 价跌{price_change_1h:.1f}%")
        elif vol_ratio_4h > 2 and price_change_1h < -3:
            # 放量下跌 → 可能反转
            volume_score = -10
            reasons.append(f"放量下跌: 量比{vol_ratio_4h:.1f}x 价跌{price_change_1h:.1f}%")
        elif vol_ratio_4h < 0.5:
            # 缩量下跌 → 正常回调
            volume_score = 15
            reasons.append(f"缩量下跌: 量比{vol_ratio_4h:.1f}x，正常回调")
        elif vol_ratio_4h < 0.7:
            volume_score = 8
            reasons.append(f"量比偏低{vol_ratio_4h:.1f}x")
        else:
            volume_score = 0
            reasons.append(f"量比正常{vol_ratio_4h:.1f}x")
        
        dimensions["volume"] = {
            "vol_ratio_4h": vol_ratio_4h,
            "price_change_1h": price_change_1h,
            "score": volume_score
        }
    
    score += volume_score
    
    # ============================================
    # 综合判断
    # ============================================
    # 计算置信度
    confidence = min(abs(score), 100)
    
    # 判断回调还是反转
    if score >= 30:
        verdict = "pullback"
        reasons.insert(0, "✅ 判断: 正常回调，可以做多")
    elif score <= -30:
        verdict = "reversal"
        reasons.insert(0, "❌ 判断: 趋势反转，谨慎做空")
    elif score >= 15:
        verdict = "pullback"
        reasons.insert(0, "🟡 判断: 可能回调，观望为主")
    elif score <= -15:
        verdict = "reversal"
        reasons.insert(0, "🟡 判断: 可能反转，谨慎观望")
    else:
        verdict = "uncertain"
        reasons.insert(0, "⚪ 判断: 信号不明，建议观望")
    
    return {
        "verdict": verdict,
        "confidence": confidence,
        "score": score,
        "reasons": reasons,
        "dimensions": dimensions
    }


def analyze_uptrend_quality(klines: dict, oi_data: list = None, 
                             funding_rate: float = 0, 
                             ls_ratio: dict = None, 
                             taker_ratio: float = 1.0) -> dict:
    """
    多维度分析：上涨质量
    用于判断当前上涨是健康上涨还是过度拉升
    
    返回:
    {
        "verdict": "healthy" | "overheated" | "weak" | "uncertain",
        "confidence": 0-100,
        "score": -50 to +50 (正=健康, 负=过热),
        "reasons": [...],
        "dimensions": {...}
    }
    """
    score = 0
    reasons = []
    dimensions = {}
    
    closes = klines.get("closes", [])
    volumes = klines.get("volumes", [])
    
    if len(closes) < 12:
        return {"verdict": "uncertain", "confidence": 0, "score": 0, 
                "reasons": ["数据不足"], "dimensions": {}}
    
    # ============================================
    # 维度1: OI+价格组合分析 (权重: 30分)
    # ============================================
    oi_score = 0
    if oi_data and len(oi_data) >= 5:
        oi_values = [float(o.get("sumOpenInterest", 0)) for o in oi_data]
        
        # OI 4h变化
        oi_4h_change = (oi_values[-1] - oi_values[-5]) / oi_values[-5] * 100 if oi_values[-5] > 0 else 0
        
        # 价格4h变化
        price_4h_change = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0
        
        # OI+价格组合判断
        if oi_4h_change > 5 and price_4h_change > 5:
            # OI增加+价格涨 = 新资金进场 → 健康上涨
            oi_score = 25
            reasons.append(f"新资金进场: OI{oi_4h_change:+.1f}% 价格{price_4h_change:+.1f}%")
        elif oi_4h_change > 0 and price_4h_change > 3:
            # OI小幅增加+价格涨 = 资金跟进
            oi_score = 15
            reasons.append(f"资金跟进: OI{oi_4h_change:+.1f}% 价格{price_4h_change:+.1f}%")
        elif oi_4h_change < -5 and price_4h_change > 5:
            # OI减少+价格涨 = 空头回补 → 弱势上涨
            oi_score = -20
            reasons.append(f"空头回补: OI{oi_4h_change:+.1f}% 价格{price_4h_change:+.1f}%")
        elif oi_4h_change < -3 and price_4h_change > 3:
            # OI小幅减少+价格涨 = 部分获利了结
            oi_score = -10
            reasons.append(f"获利了结: OI{oi_4h_change:+.1f}% 价格{price_4h_change:+.1f}%")
        elif oi_4h_change > 10:
            # OI大幅增加但价格没怎么涨 = 资金观望
            oi_score = 5
            reasons.append(f"资金观望: OI{oi_4h_change:+.1f}% 价格{price_4h_change:+.1f}%")
        else:
            oi_score = 0
            reasons.append(f"OI中性: OI{oi_4h_change:+.1f}% 价格{price_4h_change:+.1f}%")
        
        dimensions["oi_price"] = {
            "oi_4h_change": oi_4h_change,
            "price_4h_change": price_4h_change,
            "score": oi_score
        }
    
    score += oi_score
    
    # ============================================
    # 维度2: 资金费率成本 (权重: 15分)
    # ============================================
    funding_score = 0
    if funding_rate > 0.3:
        # 正费率很高，做多成本极高 → 过热
        funding_score = -15
        reasons.append(f"费率极高{funding_rate*100:.2f}%，做多成本高")
    elif funding_rate > 0.1:
        # 正费率，做多成本较高
        funding_score = -8
        reasons.append(f"正费率{funding_rate*100:.2f}%，做多成本较高")
    elif funding_rate < -0.3:
        # 负费率很高，做多有收益 → 健康
        funding_score = 15
        reasons.append(f"负费率{funding_rate*100:.2f}%，做多有收益")
    elif funding_rate < -0.1:
        # 负费率，做多有收益
        funding_score = 8
        reasons.append(f"负费率{funding_rate*100:.2f}%，做多有收益")
    else:
        funding_score = 0
        reasons.append(f"费率中性{funding_rate*100:.2f}%")
    
    dimensions["funding"] = {
        "rate": funding_rate,
        "score": funding_score
    }
    
    score += funding_score
    
    # ============================================
    # 维度3: 大户多空比 (权重: 20分)
    # ============================================
    whale_score = 0
    if ls_ratio:
        whale_long = ls_ratio.get("long", 50)
        whale_short = ls_ratio.get("short", 50)
        
        if whale_long > 65:
            # 大户极度偏多 → 可能过热
            whale_score = -15
            reasons.append(f"大户极度偏多{whale_long:.0f}%，可能过热")
        elif whale_long > 55:
            # 大户偏多 → 健康
            whale_score = 15
            reasons.append(f"大户偏多{whale_long:.0f}%，聪明钱看多")
        elif whale_short > 60:
            # 大户偏空 → 弱势
            whale_score = -15
            reasons.append(f"大户偏空{whale_short:.0f}%，聪明钱看空")
        elif whale_short > 55:
            whale_score = -8
            reasons.append(f"大户略偏空{whale_short:.0f}%")
        else:
            whale_score = 0
            reasons.append(f"大户中性 多{whale_long:.0f}%/空{whale_short:.0f}%")
        
        dimensions["whale"] = {
            "long_pct": whale_long,
            "short_pct": whale_short,
            "score": whale_score
        }
    
    score += whale_score
    
    # ============================================
    # 维度4: 散户情绪 (权重: 15分)
    # ============================================
    retail_score = 0
    if ls_ratio:
        retail_ratio = ls_ratio.get("retail_ratio", 1.0)
        
        if retail_ratio > 2.5:
            # 散户极度看多 → 危险，过热
            retail_score = -20
            reasons.append(f"散户极度看多{retail_ratio:.2f}，严重过热")
        elif retail_ratio > 1.8:
            # 散户偏多 → 可能过热
            retail_score = -10
            reasons.append(f"散户偏多{retail_ratio:.2f}，可能过热")
        elif retail_ratio < 0.5:
            # 散户极度看空 → 可能反弹
            retail_score = 15
            reasons.append(f"散户极度看空{retail_ratio:.2f}，可能反弹")
        elif retail_ratio < 0.7:
            retail_score = 8
            reasons.append(f"散户偏空{retail_ratio:.2f}")
        else:
            retail_score = 0
            reasons.append(f"散户中性{retail_ratio:.2f}")
        
        dimensions["retail"] = {
            "ratio": retail_ratio,
            "score": retail_score
        }
    
    score += retail_score
    
    # ============================================
    # 维度5: 成交量确认 (权重: 20分)
    # ============================================
    volume_score = 0
    if len(volumes) >= 12:
        # 计算成交量变化
        vol_now = volumes[-1]
        vol_4h_avg = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else vol_now
        vol_12h_avg = sum(volumes[-12:]) / 12
        
        vol_ratio_4h = vol_now / vol_4h_avg if vol_4h_avg > 0 else 1
        
        # 价格变化
        price_change_1h = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0
        
        if vol_ratio_4h > 2 and price_change_1h > 3:
            # 放量上涨 → 健康
            volume_score = 20
            reasons.append(f"放量上涨: 量比{vol_ratio_4h:.1f}x 价涨{price_change_1h:.1f}%")
        elif vol_ratio_4h > 1.5 and price_change_1h > 2:
            # 温和放量上涨
            volume_score = 12
            reasons.append(f"温和放量: 量比{vol_ratio_4h:.1f}x 价涨{price_change_1h:.1f}%")
        elif vol_ratio_4h < 0.5 and price_change_1h > 5:
            # 缩量大涨 → 弱势，可能假突破
            volume_score = -20
            reasons.append(f"缩量大涨: 量比{vol_ratio_4h:.1f}x 价涨{price_change_1h:.1f}%，弱势")
        elif vol_ratio_4h < 0.7 and price_change_1h > 3:
            # 缩量上涨 → 弱势
            volume_score = -10
            reasons.append(f"缩量上涨: 量比{vol_ratio_4h:.1f}x 价涨{price_change_1h:.1f}%")
        else:
            volume_score = 0
            reasons.append(f"量比正常{vol_ratio_4h:.1f}x")
        
        dimensions["volume"] = {
            "vol_ratio_4h": vol_ratio_4h,
            "price_change_1h": price_change_1h,
            "score": volume_score
        }
    
    score += volume_score
    
    # ============================================
    # 综合判断
    # ============================================
    # 计算置信度
    confidence = min(abs(score), 100)
    
    # 判断上涨质量
    if score >= 35:
        verdict = "healthy"
        reasons.insert(0, "✅ 判断: 健康上涨，可以做多")
    elif score <= -25:
        verdict = "overheated"
        reasons.insert(0, "❌ 判断: 严重过热，不宜追多")
    elif score >= 15:
        verdict = "healthy"
        reasons.insert(0, "🟡 判断: 上涨尚可，观望为主")
    elif score <= -10:
        verdict = "weak"
        reasons.insert(0, "🟡 判断: 弱势上涨，谨慎做多")
    else:
        verdict = "uncertain"
        reasons.insert(0, "⚪ 判断: 信号不明，建议观望")
    
    return {
        "verdict": verdict,
        "confidence": confidence,
        "score": score,
        "reasons": reasons,
        "dimensions": dimensions
    }


def check_risk_alerts(score: int, details: dict, mode: str) -> list:
    """
    风控预警: 极端费率 + 流动性 + OI背离 + 时间止损
    返回: 预警列表
    """
    alerts = []
    
    # 1. 极端费率预警
    funding_rate = details.get("funding_rate", 0)
    if mode == "long" and funding_rate > 0.002:
        alerts.append(f"⚠️ 费率偏高{funding_rate*100:.3f}% 多头成本高")
    if mode == "short" and funding_rate < -0.002:
        alerts.append(f"⚠️ 费率偏低{funding_rate*100:.3f}% 空头成本高")
    
    # 2. 流动性预警
    volume_ratio = details.get("volume_ratio", 1)
    if volume_ratio < 0.3:
        alerts.append(f"⚠️ 流动性差 量比{volume_ratio:.1f}x 止损可能滑点大")
    
    # 3. OI背离预警
    oi_change = details.get("oi_change", 0)
    price_change = details.get("price_change_24h", 0)
    if mode == "long" and price_change > 0 and oi_change < -5:
        alerts.append(f"⚠️ OI背离 价涨{price_change:.1f}%但OI降{oi_change:.1f}%")
    if mode == "short" and price_change < 0 and oi_change > 10:
        alerts.append(f"⚠️ OI背离 价跌{price_change:.1f}%但OI涨{oi_change:.1f}%")
    
    # 4. 评分崩塌预警
    if score < 30:
        alerts.append(f"⚠️ 评分偏低{score}分 风险较高")
    
    return alerts


# ============================================================
# 三级扫描系统
# ============================================================
class TieredScanner:
    """三级扫描器 + 爆发检测 + 关注列表"""
    
    def __init__(self):
        # 存储中间结果
        self.tier1_results = {"long": [], "short": []}  # Top100
        self.tier2_results = {"long": [], "short": []}  # Top50
        self.tier3_results = {"long": [], "short": []}  # Top5 (用于互斥检测)
        self.tier1_time = 0
        self.tier2_time = 0
        self.max_workers = 15  # 并发线程数 (quick模式轻量，可用更多线程)
        
        # 关注列表: {symbol: {"mode": str, "score": int, "added_at": float, "reason": str}}
        self.watchlist = {}
        self.watchlist_ttl = 1800  # 关注30分钟后过期
        self.last_burst_time = {"long": 0.0, "short": 0.0}
        self.last_watchlist_time = 0.0
    
    def get_top_symbols(self, n=300):
        """获取Top N币种（按涨幅+成交量排序）"""
        board = fetch_json(f"https://fapi.binance.com/fapi/v1/ticker/24hr")
        if not board:
            return []
        
        # 过滤USDT永续
        usdt_pairs = [b for b in board if b["symbol"].endswith("USDT")]
        
        # 按涨幅排序（做多取涨幅最高，做空取涨幅最低）
        usdt_pairs.sort(key=lambda x: float(x.get("priceChangePercent", 0)), reverse=True)
        
        # 取Top N
        return [p["symbol"] for p in usdt_pairs[:n]]
    

    def fetch_batch(self, symbols: list, quick=True) -> dict:
        """批量获取数据（并发）"""
        results = {}
        
        def fetch_one(sym):
            try:
                data = fetch_symbol_data(sym, quick=quick)
                return sym, data
            except Exception as e:
                log(f"  ⚠️ {sym} 获取失败: {e}")
                return sym, None
        
        # 使用线程池并发执行
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(fetch_one, sym): sym for sym in symbols}
            
            for future in concurrent.futures.as_completed(futures):
                sym, data = future.result()
                if data is not None:
                    results[sym] = data
        
        return results
    
    def pre_filter_by_ticker(self, tickers: list, mode: str, max_count: int = 100) -> list:
        """
        使用ticker数据预筛选 v2.0 (2026-06-12 增强)
        
        新增:
        - 成交量异动检测: volume > 2x 24h均量 → 高优先级
        - 价格范围扩大: 做多允许-5%~+5%横盘币
        - 启动前兆检测: 量升价平 = 吸筹信号
        """
        filtered = []  # [(symbol, priority_score)]
        
        for ticker in tickers:
            sym = ticker['symbol']
            if not sym.endswith('USDT'):
                continue
                
            try:
                price_change = float(ticker.get('priceChangePercent', 0))
                volume = float(ticker.get('quoteVolume', 0))
                last_price = float(ticker.get('lastPrice', 0))
                high_24h = float(ticker.get('highPrice', 0))
                low_24h = float(ticker.get('lowPrice', 0))
                open_price = float(ticker.get('openPrice', 0))
                count = int(ticker.get('count', 0))  # 交易笔数
            except (ValueError, TypeError):
                continue
            
            # 成交量底线: > 5万U
            if volume < 50000 or last_price <= 0:
                continue
            
            # 波动率
            volatility = (high_24h - low_24h) / low_24h * 100 if low_24h > 0 else 0
            
            # 量价比: 成交额/交易笔数 = 每笔平均金额
            avg_trade_size = volume / count if count > 0 else 0
            
            # 24h振幅占比: 当前价在日内区间的位置
            range_pos = (last_price - low_24h) / (high_24h - low_24h) * 100 if high_24h > low_24h else 50
            
            if mode == 'long':
                # === 做多模式 ===
                priority = 0
                
                # 类型1: 涨幅币 (price_change > 0)
                if price_change > 5:
                    priority = price_change + min(volume / 1000000, 10)
                    # 成交量异动加成: 量越大优先级越高
                    if volume > 2000000:  # >200万U
                        priority += 5
                elif price_change > 0:
                    priority = price_change + min(volume / 500000, 5)
                    
                # 类型2: 横盘BEAT型 (价格不动但量大)
                elif abs(price_change) <= 5 and volume > 300000 and volatility > 3:
                    # 量升价平 = 吸筹信号
                    priority = (volume / 100000) * (volatility / 5) * 0.8
                    # 量价比加成: 大单多 = 机构在吸筹
                    if avg_trade_size > 500:
                        priority += 3
                        
                # 类型3: 小跌但量大 (可能是最后一次洗盘)
                elif -5 < price_change < 0 and volume > 800000:
                    priority = (volume / 200000) * 0.5
                    
                if priority <= 0:
                    continue
                    
            elif mode == 'short':
                # === 做空模式 ===
                priority = 0
                
                # 类型1: 暴涨币 (price_change > 30%)
                if price_change > 30:
                    priority = price_change
                    # 费率越高 → 做空价值越大 (空头被挤压)
                    # (这里用振幅代替,费率需要额外数据)
                    if volatility > 50:
                        priority += 10  # 高波动=见顶风险大
                        
                # 类型2: 涨幅收窄 (可能是见顶信号)
                elif 10 < price_change <= 30 and volume > 1500000:
                    priority = price_change * 0.7
                    # 放量滞涨 = 顶部信号
                    if range_pos > 80:  # 价格在日内高位
                        priority += 5
                        
                # 类型3: 小涨+天量 (可能见顶)
                elif 0 < price_change <= 10 and volume > 2000000:
                    priority = price_change * 0.3
                    
                if priority <= 0:
                    continue
            else:
                continue
            
            filtered.append((sym, priority))
        
        # 按优先级降序排序
        filtered.sort(key=lambda x: x[1], reverse=True)
        
        return [sym for sym, _ in filtered[:max_count]]

    def detect_burst(self, tickers: list) -> dict:
        """
        爆发检测层 — 从ticker数据中发现爆发币
        不需要额外API调用，纯ticker数据分析
        返回: {"long": [...], "short": [...]} 候选列表
        """
        bursts = {"long": [], "short": []}
        now = time.time()
        
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            
            try:
                price_chg_1h = float(t.get("priceChangePercent", 0))
                volume = float(t.get("quoteVolume", 0))
                last_price = float(t.get("lastPrice", 0))
                high_24h = float(t.get("highPrice", 0))
                low_24h = float(t.get("lowPrice", 0))
                open_price = float(t.get("openPrice", 0))
            except (ValueError, TypeError):
                continue
            
            if volume < 50000 or last_price <= 0:
                continue
            
            # 24h涨幅
            chg_24h = (last_price - open_price) / open_price * 100 if open_price > 0 else 0
            
            # === 做多爆发检测 ===
            # 条件1: 1h涨幅>5% (BEAT型: 突然拉升)
            # 条件2: 24h涨幅>15% 且 1h还在涨 (持续爆发)
            # 条件3: 成交量>50万U 且 涨幅>3% (放量上涨)
            is_long_burst = False
            reason = ""
            
            if price_chg_1h > 8:
                is_long_burst = True
                reason = f"1h暴涨{price_chg_1h:.1f}%"
            elif price_chg_1h > 5:
                is_long_burst = True
                reason = f"1h大涨{price_chg_1h:.1f}%"
            elif chg_24h > 30 and price_chg_1h > 2:
                is_long_burst = True
                reason = f"24h涨{chg_24h:.0f}%+1h涨{price_chg_1h:.1f}%"
            elif chg_24h > 15 and price_chg_1h > 3 and volume > 500000:
                is_long_burst = True
                reason = f"24h涨{chg_24h:.0f}%+放量{volume/10000:.0f}万U"
            
            if is_long_burst:
                bursts["long"].append({
                    "symbol": sym,
                    "reason": reason,
                    "price_chg_1h": price_chg_1h,
                    "price_chg_24h": chg_24h,
                    "volume": volume,
                    "price": last_price,
                    "detected_at": now,
                })
            
            # === 做空爆发检测 ===
            # 条件1: 24h暴涨>50% (暴涨后见顶风险)
            # 条件2: 24h涨>30% 且 1h转跌 (冲高回落)
            is_short_burst = False
            reason_s = ""
            
            if chg_24h > 80:
                is_short_burst = True
                reason_s = f"24h暴涨{chg_24h:.0f}% 见顶风险"
            elif chg_24h > 50:
                is_short_burst = True
                reason_s = f"24h大涨{chg_24h:.0f}% 可能见顶"
            elif chg_24h > 30 and price_chg_1h < -3:
                is_short_burst = True
                reason_s = f"24h涨{chg_24h:.0f}%+1h跌{price_chg_1h:.1f}% 冲高回落"
            
            if is_short_burst:
                bursts["short"].append({
                    "symbol": sym,
                    "reason": reason_s,
                    "price_chg_1h": price_chg_1h,
                    "price_chg_24h": chg_24h,
                    "volume": volume,
                    "price": last_price,
                    "detected_at": now,
                })
        
        # 按1h涨幅排序，取Top20
        bursts["long"].sort(key=lambda x: x["price_chg_1h"], reverse=True)
        bursts["short"].sort(key=lambda x: x["price_chg_24h"], reverse=True)
        bursts["long"] = bursts["long"][:20]
        bursts["short"] = bursts["short"][:20]
        
        return bursts

    def update_watchlist(self, candidates: list, mode: str):
        """将中间信号(60-75分)加入关注列表"""
        now = time.time()
        for c in candidates:
            sym = c["symbol"]
            if sym not in self.watchlist:
                self.watchlist[sym] = {
                    "mode": mode,
                    "score": c["score"],
                    "added_at": now,
                    "last_scan": now,
                    "reason": " | ".join(c.get("reasons", [])[:3]),
                    "details": c.get("details", {}),
                }
                log(f"  👁️ 加入关注: {sym} {mode.upper()} 评分{c['score']}")

    def clean_watchlist(self):
        """清理过期的关注列表"""
        now = time.time()
        expired = [s for s, d in self.watchlist.items() if now - d["added_at"] > self.watchlist_ttl]
        for s in expired:
            log(f"  ⏰ 关注过期: {s}")
            del self.watchlist[s]

    def rescan_watchlist(self, config) -> list:
        """
        扫描关注列表中的币 — 用完整数据重新评分
        如果评分升到入场线以上，返回为开仓候选
        """
        if not self.watchlist:
            return []
        
        self.clean_watchlist()
        if not self.watchlist:
            return []
        
        now = time.time()
        results = []
        symbols = list(self.watchlist.keys())
        
        log(f"👁️ 扫描关注列表: {len(symbols)}个币")
        
        batch_data = self.fetch_batch(symbols, quick=False)
        
        for sym, data in batch_data.items():
            if data is None:
                continue
            
            kline_data, funding_data, oi_data, ls_data, taker_data = data
            watch_info = self.watchlist.get(sym, {})
            mode = watch_info.get("mode", "long")
            
            score, details, reasons, pattern, stop_loss_pct, skip_reason = calculate_score(
                sym, mode, kline_data, funding_data, oi_data, ls_data, taker_data
            )
            
            if skip_reason or score is None:
                continue
            
            # 更新关注列表
            if sym in self.watchlist:
                self.watchlist[sym]["score"] = score
                self.watchlist[sym]["last_scan"] = now
            
            # 检查是否升到入场线
            entry_score = config["modes"][mode].get("entry_score", 70)
            
            # v3.1 跌幅榜做空降低入场线: EMA空头+MACD零轴下 → 入场线从70降到60
            effective_entry = entry_score
            if mode == "short" and details.get("ema_trend") == "bearish" and details.get("macd_trend") == "bearish_zone":
                effective_entry = max(60, entry_score - 10)  # 降低10分，但最低60
                if effective_entry < entry_score:
                    log(f"📉 {sym} 趋势确认降低入场线: {entry_score}→{effective_entry}")
            
            if score >= effective_entry:
                results.append({
                    "symbol": sym,
                    "score": score,
                    "price": kline_data.get("close", 0),
                    "details": details,
                    "reasons": reasons,
                    "pattern": pattern,
                    "stop_loss_pct": stop_loss_pct,
                    "mode": mode,
                    "source": "watchlist",
                })
                log(f"  🎯 关注→入场: {sym} {mode.upper()} 评分{score}↑ (之前{watch_info.get('score',0)})")
                # 从关注列表移除(已升级为入场候选)
                del self.watchlist[sym]
            else:
                # 降级: 如果分数掉到50以下，从关注列表移除
                if score < 50:
                    log(f"  📉 关注→移除: {sym} 评分{score}↓ 太低")
                    del self.watchlist[sym]
        
        return results

    def scan_burst_candidates(self, burst_list: list, mode: str, config) -> list:
        """
        对爆发候选做完整数据扫描
        """
        if not burst_list:
            return []
        
        symbols = [b["symbol"] for b in burst_list]
        burst_map = {b["symbol"]: b for b in burst_list}
        
        log(f"🔥 爆发扫描: {len(symbols)}个候选 ({mode.upper()})")
        
        batch_data = self.fetch_batch(symbols, quick=False)
        
        results = []
        watchlist_candidates = []
        entry_score = config["modes"][mode].get("entry_score", 70)
        
        for sym, data in batch_data.items():
            if data is None:
                continue
            
            kline_data, funding_data, oi_data, ls_data, taker_data = data
            
            score, details, reasons, pattern, stop_loss_pct, skip_reason = calculate_score(
                sym, mode, kline_data, funding_data, oi_data, ls_data, taker_data
            )
            
            if skip_reason or score is None:
                continue
            
            burst_info = burst_map.get(sym, {})
            
            result_entry = {
                "symbol": sym,
                "score": score,
                "price": kline_data.get("close", 0),
                "details": details,
                "reasons": (reasons or []) + [f"🔥{burst_info.get('reason', '')}"],
                "pattern": pattern,
                "stop_loss_pct": stop_loss_pct,
                "mode": mode,
                "source": "burst",
            }
            
            if score >= entry_score:
                results.append(result_entry)
            elif score >= 50:
                # 中间信号加入关注列表
                watchlist_candidates.append(result_entry)
        
        if watchlist_candidates:
            self.update_watchlist(watchlist_candidates, mode)
        
        return results

    def scan_tier1_optimized(self, mode, config):
        """
        优化版Tier1扫描: 预筛选 + 并发
        
        2026-06-12 优化:
        - 不再纯按price_change取Top300,改用混合池:
          Top200(按涨幅) + Top200(按成交量) + 爆发潜力币
        - pre_filter_by_ticker内部做优先级排序
        - 扫描结果注入关注列表(60-69分不丢弃)
        """
        start_time = time.time()
        
        # 1. 获取ticker数据 (1个API调用获取所有)
        tickers = fetch_json(f"{BASE_URL}/fapi/v1/ticker/24hr")
        if not tickers:
            log("❌ 获取ticker失败")
            return []
        
        # 2. 构建混合候选池 (不再只取涨幅Top300)
        usdt_tickers = [t for t in tickers if t['symbol'].endswith('USDT')]
        
        # 传统池: 按涨幅排的Top200
        if mode == 'long':
            by_change = sorted(usdt_tickers, 
                             key=lambda x: float(x.get('priceChangePercent', 0)), 
                             reverse=True)[:200]
        else:
            # 做空: 只看正涨幅Top200 (暴涨→冲高回落候选)
            # 暴跌的币不做空追跌(胜率差),通过by_volume池间接覆盖
            by_change = sorted(usdt_tickers, 
                             key=lambda x: float(x.get('priceChangePercent', 0)), 
                             reverse=True)[:200]
        
        # 成交量池: 按成交量排的Top200 (抓BEAT型: 大量未涨的币)
        by_volume = sorted(usdt_tickers, 
                         key=lambda x: float(x.get('quoteVolume', 0)), 
                         reverse=True)[:200]
        
        # 合并去重
        seen = set()
        combined = []
        for t in by_change + by_volume:
            sym = t['symbol']
            if sym not in seen:
                seen.add(sym)
                combined.append(t)
        
        log(f"🔍 Tier1混合池: {len(combined)}个候选 (涨幅池200 + 量能池200, 去重后)")
        
        # 3. 预筛选 (内部做优先级排序,不再依赖外部排序)
        pre_filtered = self.pre_filter_by_ticker(combined, mode, max_count=150)
        
        log(f"🔍 Tier1预筛选: {len(pre_filtered)}个 ({mode.upper()})")
        
        if not pre_filtered:
            return []
        
        # 4. 并发获取数据 + 评分
        results = []
        watchlist_candidates = []
        entry_score = config["modes"][mode].get("entry_score", 70)
        watchlist_threshold = config.get("scan", {}).get("tiered", {}).get("watchlist_threshold", 55)
        
        batch_size = 30
        for i in range(0, len(pre_filtered), batch_size):
            batch = pre_filtered[i:i+batch_size]
            batch_data = self.fetch_batch(batch, quick=True)
            
            for sym, data in batch_data.items():
                if data is None:
                    continue
                    
                kline_data, funding_data, oi_data, ls_data, taker_data = data
                
                score, details, reasons, pattern, stop_loss_pct, skip_reason = calculate_score(
                    sym, mode, kline_data, funding_data, oi_data, ls_data, taker_data
                )
                
                if skip_reason:
                    continue
                
                if score is None:
                    continue
                
                entry = {
                    "symbol": sym,
                    "score": score,
                    "price": kline_data.get("close", 0),
                    "details": details,
                    "reasons": reasons,
                    "pattern": pattern,
                    "stop_loss_pct": stop_loss_pct,
                }
                
                if score >= entry_score:
                    results.append(entry)
                elif score >= watchlist_threshold:
                    # 中间信号加入关注列表,不再丢弃
                    watchlist_candidates.append({**entry, "mode": mode, "source": "tier1"})
            
            log(f"  📊 进度: {min(i+batch_size, len(pre_filtered))}/{len(pre_filtered)}")
        
        # 5. 更新关注列表
        if watchlist_candidates:
            self.update_watchlist(watchlist_candidates, mode)
            log(f"  👁️ Tier1加入关注列表: {len(watchlist_candidates)}个")
        
        # 按评分排序，取Top50
        results.sort(key=lambda x: x["score"], reverse=True)
        
        elapsed = time.time() - start_time
        log(f"✅ Tier1完成: {len(results)}个信号, 耗时{elapsed:.0f}秒")
        
        return results[:50]


    def scan_tier2(self, mode, config, tier1_results):
        """Tier2: 从Top100筛选Top50（并发快速模式）"""
        log(f"🔍 Tier2扫描: Top100 → Top50 ({mode.upper()})")
        
        results = []
        symbols = [item["symbol"] for item in tier1_results]
        
        # 使用并发获取完整数据（Tier2需要完整评分）
        batch_data = self.fetch_batch(symbols, quick=False)
        
        # 计算评分
        for sym, data in batch_data.items():
            if data is None:
                continue
            
            kline_data, funding_data, oi_data, ls_data, taker_data = data
            
            score, details, reasons, pattern, stop_loss_pct, skip_reason = calculate_score(
                sym, mode, kline_data, funding_data, oi_data, ls_data, taker_data
            )
            
            if skip_reason or score is None:
                continue
            
            results.append({
                "symbol": sym,
                "score": score,
                "price": kline_data.get("close", 0),
                "details": details,
                "reasons": reasons,
                "pattern": pattern,
                "stop_loss_pct": stop_loss_pct,
            })
        
        # 按评分排序，取Top50
        results.sort(key=lambda x: x["score"], reverse=True)
        top50 = results[:50]
        
        log(f"✅ Tier2完成: {len(results)}个有效 → Top50")
        return top50
    
    def scan_tier3(self, mode, config, tier2_results):
        """Tier3: 从Top50筛选Top5（并发完整模式）"""
        log(f"🔍 Tier3扫描: Top50 → Top5 ({mode.upper()})")
        
        results = []
        symbols = [item["symbol"] for item in tier2_results]
        
        # 使用并发获取完整数据
        batch_data = self.fetch_batch(symbols, quick=False)
        
        # 计算评分
        for sym, data in batch_data.items():
            if data is None:
                continue
            
            kline_data, funding_data, oi_data, ls_data, taker_data = data
            
            score, details, reasons, pattern, stop_loss_pct, skip_reason = calculate_score(
                sym, mode, kline_data, funding_data, oi_data, ls_data, taker_data
            )
            
            if skip_reason or score is None:
                continue
            
            results.append({
                "symbol": sym,
                "score": score,
                "price": kline_data.get("close", 0),
                "details": details,
                "reasons": reasons,
                "pattern": pattern,
                "stop_loss_pct": stop_loss_pct,
            })
        
        # 按评分排序，取Top5
        results.sort(key=lambda x: x["score"], reverse=True)
        top5 = results[:5]
        
        log(f"✅ Tier3完成: {len(results)}个有效 → Top5")
        return top5

# 全局扫描器实例
scanner = TieredScanner()


def verify_stop_losses(positions, config):
    """验证每个持仓都有物理止损单，缺失则补挂
    
    铁律: 不允许无止损持仓
    -4130 = 止损单已存在（视为成功）
    """
    log(f"  🔍 验证止损单: {len(positions)}个持仓")
    for pos in positions:
        sym = pos["symbol"]
        direction = pos["direction"]
        entry_price = pos["entry_price"]
        cfg = config["modes"][direction.lower()]
        sl_pct = cfg.get("stop_loss", 0.06)
        
        if direction == "LONG":
            sl_price = round(entry_price * (1 - sl_pct), 6)
        else:
            sl_price = round(entry_price * (1 + sl_pct), 6)
        
        sl_side = "SELL" if direction == "LONG" else "BUY"
        position_side = direction
        
        # 尝试挂止损单（-4130表示已存在，视为成功）
        result = api_post("/fapi/v1/algoOrder", {
            "symbol": sym,
            "side": sl_side,
            "type": "STOP_MARKET",
            "algoType": "CONDITIONAL",
            "triggerPrice": sl_price,
            "closePosition": "true",
            "positionSide": position_side,
            "workingType": "MARK_PRICE",
        })
        
        if result and "algoId" in result:
            log(f"  ✅ 止损单已挂: {sym} {direction} @ {sl_price:.6f}")
            # 存储algoId到state
            if _current_state is not None:
                if "algo_orders" not in _current_state:
                    _current_state["algo_orders"] = {}
                if sym not in _current_state["algo_orders"]:
                    _current_state["algo_orders"][sym] = []
                _current_state["algo_orders"][sym].clear()
                _current_state["algo_orders"][sym].append(str(result["algoId"]))
                save_state(_current_state)
        elif result and result.get("code") == -4130:
            pass  # 止损单已存在，正常
        else:
            err_code = result.get("code", "") if result else "no response"
            err_msg = result.get("msg", "") if result else ""
            log(f"  ⚠️ {sym} 止损单补挂失败: {err_code} {err_msg}")

def sync_binance_data(positions):
    """交叉验证: 同步Binance数据到本地DB
    
    每小时运行一次，确保本地DB与Binance一致:
    1. Binance已平仓但本地还开着 → 标记为closed
    2. Binance有但本地没有 → 添加到本地
    3. PnL数据不一致 → 以Binance为准更新
    """
    import sqlite3
    from datetime import datetime
    
    log("  🔄 交叉验证: 同步Binance数据...")
    
    # 获取Binance已实现盈亏
    income = api_get("/fapi/v1/income", {"incomeType": "REALIZED_PNL", "limit": "200"})
    if not income or not isinstance(income, list):
        log("  ⚠️ 获取Binance收入数据失败")
        return
    
    # 按(symbol, direction)汇总已实现盈亏
    bn_pnl_by_key = {}
    for d in income:
        sym = d["symbol"]
        # 尝试从income数据获取方向，如果没有则标记为unknown
        pos_side = d.get("positionSide", "BOTH")
        direction = "LONG" if pos_side == "LONG" else ("SHORT" if pos_side == "SHORT" else "BOTH")
        pnl = float(d["income"])
        key = f"{sym}_{direction}"
        if key not in bn_pnl_by_key:
            bn_pnl_by_key[key] = {"count": 0, "total_pnl": 0, "last_time": 0}
        bn_pnl_by_key[key]["count"] += 1
        bn_pnl_by_key[key]["total_pnl"] += pnl
        bn_pnl_by_key[key]["last_time"] = max(bn_pnl_by_key[key]["last_time"], int(d["time"]))
        # 也记录BOTH方向到symbol级别
        both_key = f"{sym}_BOTH"
        if both_key not in bn_pnl_by_key:
            bn_pnl_by_key[both_key] = {"count": 0, "total_pnl": 0, "last_time": 0}
        bn_pnl_by_key[both_key]["count"] += 1
        bn_pnl_by_key[both_key]["total_pnl"] += pnl
        bn_pnl_by_key[both_key]["last_time"] = max(bn_pnl_by_key[both_key]["last_time"], int(d["time"]))
    
    # 当前持仓keys (symbol_direction)
    bn_position_keys = set(f"{p['symbol']}_{p['direction']}" for p in positions)
    
    # 连接DB
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "review.db")
    if not os.path.exists(db_path):
        log("  ⚠️ 数据库不存在，跳过同步")
        return
    
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        sync_count = 0
        
        # 1. 标记已平仓
        local_open = conn.execute('SELECT * FROM trades WHERE status="open"').fetchall()
        for trade in local_open:
            sym = trade["symbol"]
            direction = trade["direction"]
            pos_key = f"{sym}_{direction}"
            if pos_key not in bn_position_keys:
                # Binance已平仓，本地还开着
                # 优先用精确方向匹配，回退到BOTH
                key_pnl = bn_pnl_by_key.get(pos_key, bn_pnl_by_key.get(f"{sym}_BOTH", {"total_pnl": 0}))
                total_pnl = key_pnl["total_pnl"]
                
                conn.execute(
                    "UPDATE trades SET status=?, exit_time=datetime('now'), pnl_usd=?, win=? WHERE id=?",
                    ("closed", total_pnl, 1 if total_pnl > 0 else 0, trade["id"])
                )
                sync_count += 1
                log(f"    📝 标记已平仓: {sym} {direction} PnL:{total_pnl:.2f}U")
        
        conn.commit()
        
        # 2. 添加缺失持仓
        local_open = conn.execute('SELECT * FROM trades WHERE status="open"').fetchall()
        local_keys = set(f"{r['symbol']}_{r['direction']}" for r in local_open)
        
        for p in positions:
            sym = p["symbol"]
            direction = p["direction"]
            pos_key = f"{sym}_{direction}"
            if pos_key not in local_keys:
                entry = p["entry_price"]
                leverage = p["leverage"]
                qty = p["amount"]
                margin = qty * entry / leverage
                
                conn.execute(
                    "INSERT INTO trades (symbol, direction, entry_price, entry_time, leverage, margin_usd, notional_usd, quantity, status, strategy_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (sym, direction, entry, datetime.now().isoformat(), leverage, margin, qty * entry, qty, "open", "v1.0")
                )
                sync_count += 1
                log(f"    📝 新增持仓: {sym} {direction} {leverage}x entry={entry:.6f}")
        
        conn.commit()
        
        # 3. 更新PnL (以Binance为准)
        local_closed = conn.execute('SELECT * FROM trades WHERE status="closed" AND (pnl_usd IS NULL OR pnl_usd = 0)').fetchall()
        for trade in local_closed:
            sym = trade["symbol"]
            direction = trade["direction"]
            pos_key = f"{sym}_{direction}"
            # 优先用精确方向匹配，回退到BOTH
            key_pnl = bn_pnl_by_key.get(pos_key, bn_pnl_by_key.get(f"{sym}_BOTH"))
            if key_pnl:
                total_pnl = key_pnl["total_pnl"]
                conn.execute("UPDATE trades SET pnl_usd=?, win=? WHERE id=?", 
                            (total_pnl, 1 if total_pnl > 0 else 0, trade["id"]))
                sync_count += 1
        
        conn.commit()
    finally:
        conn.close()
    
    if sync_count > 0:
        log(f"  ✅ 交叉验证完成: 同步{sync_count}条记录")
    else:
        log("  ✅ 交叉验证完成: 数据一致")

# ============================================================
# 持仓管理
# ============================================================
def manage_positions(positions, config):
    """管理持仓: 止盈止损"""
    if not hasattr(manage_positions, 'peak_pnl'):
        manage_positions.peak_pnl = dict(_current_state.get('peak_pnl', {}))
    if not hasattr(manage_positions, 'tp1_done'):
        manage_positions.tp1_done = set(_current_state.get('tp1_done', []))
    
    trade_mem_cache = load_trade_memory()
    current_symbols = set()
    
    for pos in positions:
        sym = pos["symbol"]
        direction = pos["direction"]
        pnl_pct = pos["pnl_pct"] / 100
        cfg = config["modes"][direction.lower()]
        pos_key = f"{sym}_{direction}"
        current_symbols.add(pos_key)
        
        # 计算实际持仓时间
        hold_hours = 0
        try:
            trade_mem = trade_mem_cache
            entry_record_for_hold = next((t for t in trade_mem if t.get('symbol') == sym and t.get('direction') == direction), {})
            entry_time_str = entry_record_for_hold.get("timestamp", "")
            if entry_time_str:
                from datetime import datetime as dt
                entry_dt = dt.fromisoformat(entry_time_str.replace("Z", "+00:00"))
                hold_hours = (dt.now() - entry_dt).total_seconds() / 3600
        except (ValueError, TypeError, AttributeError):
            hold_hours = 0
        
        emoji = "🟢" if pnl_pct > 0 else "🔴"
        log(f"  {emoji} {sym}({direction}): {pnl_pct*100:+.2f}% | 入:{pos['entry_price']:.6f} 现:{pos['mark_price']:.6f}")
        
        # 分批止盈 — 成功后continue跳过后续止损检查，避免TP1减仓后止损用原始数量
        if pnl_pct >= cfg.get("tp1_pct", 0.40) and pos_key not in manage_positions.tp1_done:
            log(f"  💰 {sym} 第一批止盈 {pnl_pct*100:.1f}%")
            close_qty = pos["amount"] * cfg.get("tp1_sell", 0.50)
            result = close_position_market(sym, direction, close_qty)
            if result and "orderId" in result:
                manage_positions.tp1_done.add(pos_key)
                notify_trade_exit(sym, direction, pnl_pct, pos["unrealized_pnl"], "分批止盈", 0)
                # 查找entry_record
                entry_records = trade_mem_cache
                entry_record = next((t for t in entry_records if t.get('symbol') == sym and t.get('direction') == direction), {})
                factors = entry_record.get("factors", {})
                market_ctx = {"funding_rate": factors.get("funding_rate", 0), "long_ratio": factors.get("long_ratio", 50), "oi_change": factors.get("oi_change", 0), "volume_ratio": factors.get("volume_ratio", 1), "rsi": factors.get("rsi", 50)}
                
                record_exit(
                    trade_id=pos_key,
                    symbol=sym,
                    direction=direction,
                    entry_price=pos["entry_price"],
                    exit_price=pos["mark_price"],
                    pnl_pct=pnl_pct,
                    pnl_usd=pos["unrealized_pnl"],
                    exit_type="partial_tp",
                    hold_hours=hold_hours,
                    peak_pnl=pnl_pct,
                    entry_record=entry_record,
                    market_context=market_ctx,
                )
                continue  # TP1成功，跳过止损检查，下一次循环再检查剩余仓位
        
        # 追踪止盈
        trail_activate = cfg.get("trail_activate", 0.10)
        trail_draw = cfg.get("trail_drawdown", 0.15)
        
        if pnl_pct >= trail_activate:
            if pos_key not in manage_positions.peak_pnl:
                manage_positions.peak_pnl[pos_key] = pnl_pct
            
            if pnl_pct > manage_positions.peak_pnl[pos_key]:
                manage_positions.peak_pnl[pos_key] = pnl_pct
                log(f"  📈 {sym} 追踪止盈: 新峰值 {pnl_pct*100:.1f}%")
                # ★ 峰值更新时立即持久化，防止重启丢失
                if _current_state is not None:
                    _current_state['peak_pnl'] = dict(manage_positions.peak_pnl)
                    save_state(_current_state)
            
            peak = manage_positions.peak_pnl[pos_key]
            drawdown = peak - pnl_pct
            
            if drawdown >= trail_draw:
                log(f"  💰 {sym} 追踪止盈触发: 峰值{peak*100:.1f}% → 当前{pnl_pct*100:.1f}%")
                result = close_position_market(sym, direction, pos["amount"])
                if result and "orderId" in result:
                    notify_trade_exit(sym, direction, pnl_pct, pos["unrealized_pnl"], "追踪止盈", 0)
                    record_cooldown(sym, direction, pos["unrealized_pnl"])
                    # 查找entry_record
                    entry_records = trade_mem_cache
                    entry_record = next((t for t in entry_records if t.get('symbol') == sym and t.get('direction') == direction), {})
                    factors = entry_record.get("factors", {})
                    market_ctx = {"funding_rate": factors.get("funding_rate", 0), "long_ratio": factors.get("long_ratio", 50), "oi_change": factors.get("oi_change", 0), "volume_ratio": factors.get("volume_ratio", 1), "rsi": factors.get("rsi", 50)}
                    
                    record_exit(
                        trade_id=pos_key,
                        symbol=sym,
                        direction=direction,
                        entry_price=pos["entry_price"],
                        exit_price=pos["mark_price"],
                        pnl_pct=pnl_pct,
                        pnl_usd=pos["unrealized_pnl"],
                        exit_type="trailing_tp",
                        hold_hours=hold_hours,
                        peak_pnl=peak,
                        entry_record=entry_record,
                        market_context=market_ctx,
                    )
                    if pos_key in manage_positions.peak_pnl:
                        del manage_positions.peak_pnl[pos_key]
                    # 平仓成功，从last_positions移除防止detect_closed_positions重复记录
                    global last_positions
                    last_positions = [p for p in last_positions if f"{p['symbol']}_{p['direction']}" != pos_key]
                    continue  # 已平仓，跳过止损检查
        
        # 止损 — 软件止损作为algo止损的备份，仅在algo止损未执行时触发
        # 使用比配置多1%的阈值，给algo止损单优先执行的机会
        sl_threshold = cfg.get("stop_loss", 0.06)
        if pnl_pct <= -(sl_threshold + 0.01):  # 配置6% → 软件止损在-7%触发
            log(f"  💰 {sym} 软件止损触发: {pnl_pct*100:.1f}% (algo止损可能已失效)")
            result = close_position_market(sym, direction, pos["amount"])
            if result and "orderId" in result:
                notify_trade_exit(sym, direction, pnl_pct, pos["unrealized_pnl"], "止损", 0)
                record_cooldown(sym, direction, pos["unrealized_pnl"])
                # 查找entry_record
                entry_records = trade_mem_cache
                entry_record = next((t for t in entry_records if t.get('symbol') == sym and t.get('direction') == direction), {})
                factors = entry_record.get("factors", {})
                market_ctx = {"funding_rate": factors.get("funding_rate", 0), "long_ratio": factors.get("long_ratio", 50), "oi_change": factors.get("oi_change", 0), "volume_ratio": factors.get("volume_ratio", 1), "rsi": factors.get("rsi", 50)}
                
                # 使用历史峰值而非当前pnl
                peak_for_tag = manage_positions.peak_pnl.get(pos_key, max(0, pnl_pct))
                
                record_exit(
                    trade_id=pos_key,
                    symbol=sym,
                    direction=direction,
                    entry_price=pos["entry_price"],
                    exit_price=pos["mark_price"],
                    pnl_pct=pnl_pct,
                    pnl_usd=pos["unrealized_pnl"],
                    exit_type="stop_loss",
                    hold_hours=hold_hours,
                    peak_pnl=peak_for_tag,
                    entry_record=entry_record,
                    market_context=market_ctx,
                )
                if pos_key in manage_positions.peak_pnl:
                    del manage_positions.peak_pnl[pos_key]
                # 平仓成功，从last_positions移除防止detect_closed_positions重复记录
                last_positions = [p for p in last_positions if f"{p['symbol']}_{p['direction']}" != pos_key]

# ============================================================
# 平仓检测
# ============================================================
last_positions = []

def detect_closed_positions(current_positions):
    """检测平仓"""
    global last_positions
    
    current_keys = {f"{p['symbol']}_{p['direction']}" for p in current_positions}
    last_keys = {f"{p['symbol']}_{p['direction']}" for p in last_positions}
    
    closed_keys = last_keys - current_keys
    
    for key in closed_keys:
        # 用rsplit从右侧分割，避免symbol含下划线时出错(如1000PEPE_USDT)
        parts = key.rsplit("_", 1)
        if len(parts) != 2:
            log(f"  ⚠️ 无法解析key: {key}")
            continue
        sym, direction = parts
        log(f"  ⚠️ 检测到平仓: {sym} {direction}")
        
        # 从Binance获取平仓数据
        try:
            # 获取盈亏记录
            income_data = api_get("/fapi/v1/income", {
                "symbol": sym,
                "incomeType": "REALIZED_PNL",
                "limit": 20,
            })
            
            pnl_usd = 0
            if income_data:
                # 过滤最近1小时的记录（只算本次平仓的盈亏）
                cutoff_ms = (time.time() - 3600) * 1000
                recent_income = [i for i in income_data
                                 if abs(float(i.get("income", 0))) > 0.01
                                 and int(i.get("time", 0)) > cutoff_ms]
                if recent_income:
                    pnl_usd = sum(float(i["income"]) for i in recent_income)
                    log(f"  📊 获取到 {len(recent_income)} 条盈亏记录")
                    log(f"  💰 计算盈亏: {pnl_usd:+.2f}U")
            
            # 获取成交记录
            trades_data = api_get("/fapi/v1/userTrades", {
                "symbol": sym,
                "limit": 50,
            })
            
            exit_price = 0
            entry_price = 0
            hold_hours = 0
            pnl_pct = 0
            peak_pnl = 0
            entry_record = {}
            
            # 从 trade_memory 获取入场信息
            try:
                trade_mem = load_trades()
                for t in trade_mem:
                    if t.get("symbol") == sym and t.get("direction") == direction:
                        entry_price = float(t.get("entry_price", 0))
                        entry_record = t
                        # 计算持仓时间
                        entry_time_str = t.get("timestamp", "")
                        if entry_time_str:
                            from datetime import datetime as dt
                            try:
                                entry_dt = dt.fromisoformat(entry_time_str.replace("Z", "+00:00"))
                                hold_hours = (dt.now() - entry_dt).total_seconds() / 3600
                            except (ValueError, TypeError):
                                hold_hours = 0
                        peak_pnl = t.get("peak_pnl", 0)
                        log(f"  📋 从trade_memory获取: entry={entry_price:.6f}, hold={hold_hours:.1f}h")
                        break
            except Exception as e:
                log(f"  ⚠️ 读取trade_memory失败: {e}")
            
            if trades_data:
                # 找到最近的成交
                recent_trades = [t for t in trades_data if int(t.get("time", 0)) > (time.time() - 7200) * 1000]
                
                if recent_trades:
                    # 计算平均出场价
                    total_qty = 0
                    total_cost = 0
                    for t in recent_trades:
                        qty = float(t.get("qty", 0))
                        price = float(t.get("price", 0))
                        total_qty += qty
                        total_cost += qty * price
                    
                    if total_qty > 0:
                        exit_price = total_cost / total_qty
                        log(f"  📈 最近2小时成交: {len(recent_trades)} 笔")
                        log(f"  💵 计算出场价: {exit_price:.6f}")
                else:
                    # 如果没有最近2小时的记录，使用最后一条成交记录
                    log(f"  ⚠️ 没有最近2小时的成交记录，使用最后一条")
                    if trades_data:
                        last_trade = trades_data[-1]
                        exit_price = float(last_trade.get("price", 0))
                        log(f"  💵 最后成交价: {exit_price:.6f}")
            
            # 计算盈亏百分比 (小数形式，如 -0.0648 表示 -6.48%)
            if entry_price > 0 and exit_price > 0:
                if direction == "LONG":
                    pnl_pct = (exit_price - entry_price) / entry_price
                else:  # SHORT
                    pnl_pct = (entry_price - exit_price) / entry_price
                log(f"  📊 计算盈亏: {pnl_pct*100:+.1f}%")
            elif pnl_usd != 0 and entry_price > 0:
                # 从 pnl_usd 反推 pnl_pct (pnl_usd = margin * leverage * pnl_pct)
                # 简单估算: 假设保证金约等于 notional/leverage
                notional = float(entry_record.get("notional_usd", 0))
                if notional > 0:
                    pnl_pct = pnl_usd / notional
                    log(f"  📊 从盈亏反推: {pnl_pct*100:+.1f}% (pnl_usd={pnl_usd:+.2f}, notional={notional:.0f})")
            
            # 从 entry_record 构建 market_context 给 auto_tag_loss
            factors = entry_record.get("factors", {})
            market_context = {
                "funding_rate": factors.get("funding_rate", 0),
                "long_ratio": factors.get("long_ratio", 50),
                "oi_change": factors.get("oi_change", 0),
                "volume_ratio": factors.get("volume_ratio", 1),
                "rsi": factors.get("rsi", 50),
                "price_change_24h": factors.get("price_change_24h", 0),
            }
            
            # 记录平仓
            record_exit(
                trade_id=key,
                symbol=sym,
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                pnl_usd=pnl_usd,
                exit_type="auto_close",
                hold_hours=hold_hours,
                peak_pnl=peak_pnl,
                entry_record=entry_record,
                market_context=market_context,
            )
            
            # 发送平仓通知
            emoji = "🟢" if pnl_usd > 0 else "🔴"
            log(f"  {emoji} 平仓完成: {sym} {direction} 盈亏:{pnl_usd:+.2f}U ({pnl_pct:+.1f}%)")
            notify_trade_exit(sym, direction, pnl_pct, pnl_usd, "止损/止盈", hold_hours)
            
            # 冷却机制：记录连续亏损
            record_cooldown(sym, direction, pnl_usd)
            
            # 清理tp1状态
            if hasattr(manage_positions, 'tp1_done'):
                manage_positions.tp1_done.discard(key)
            if hasattr(manage_positions, 'peak_pnl'):
                manage_positions.peak_pnl.pop(key, None)
            
            # 从 trade_memory 中移除已平仓的记录
            try:
                trade_mem = load_trades()
                before_count = len(trade_mem)
                trade_mem = [t for t in trade_mem if not (t.get("symbol") == sym and t.get("direction") == direction)]
                after_count = len(trade_mem)
                save_trades(trade_mem)
                log(f"  🗑️ 已从 trade_memory 移除: {sym} {direction} (清理{before_count - after_count}条)")
            except Exception as e:
                log(f"  ⚠️ 移除 trade_memory 失败: {e}")
            
            # 清理已平仓币种的条件委托
            if _current_state and "algo_orders" in _current_state and sym in _current_state["algo_orders"]:
                for algo_id in _current_state["algo_orders"][sym]:
                    result = cancel_algo_order(sym, algo_id)
                    if result and not result.get("code"):
                        log(f"  🗑️ 已取消条件委托: {sym} algoId={algo_id}")
                del _current_state["algo_orders"][sym]
                save_state(_current_state)
            
        except Exception as e:
            log(f"  ⚠️ 获取成交数据失败: {e}")
    
    last_positions = current_positions

# ============================================================
# 主循环
# ============================================================
def main_loop():
    """主循环"""
    global last_positions
    
    log("🚀 妖币猎手统一系统启动")
    log("=" * 50)
    
    # 初始化状态
    global _current_state
    state = load_state()
    _current_state = state
    
    # 启动时加载持仓到内存
    try:
        trade_mem = load_trades()
        for t in trade_mem:
            last_positions.append({
                "symbol": t["symbol"],
                "direction": t["direction"],
                "entry_price": t["entry_price"],
                "amount": t.get("quantity", 0),
                "pnl_pct": 0,
                "mark_price": t["entry_price"],
                "unrealized_pnl": 0,
            })
        log(f"📋 加载 {len(last_positions)} 个历史持仓")
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as e:
        log(f"⚠️ 加载历史持仓失败: {e}")
    
    # 扫描计时器初始化
    last_tier1_time = {"long": 0, "short": 0}
    last_tier2_time = {"long": 0, "short": 0}
    last_tier3_time = {"long": 0, "short": 0}
    
    while True:
        try:
            config = load_config()
            tiered_cfg = config.get("scan", {}).get("tiered", {})
            tier1_interval = tiered_cfg.get("tier1_interval", 900)
            tier2_interval = tiered_cfg.get("tier2_interval", 600)
            tier3_interval = tiered_cfg.get("tier3_interval", 300)
            now = time.time()
            
            # 获取当前状态
            bal = get_balance()
            positions = get_positions()
            
            if not bal:
                log("❌ 获取余额失败")
                time.sleep(60)
                continue
            
            log(f"💰 余额: {bal['balance']:.2f}U | 可用: {bal['available']:.2f}U | 持仓: {len(positions)}个")
            
            # 检测平仓
            detect_closed_positions(positions)
            
            # 持仓管理
            manage_positions(positions, config)
            
            # 验证止损单（每30分钟检查一次，铁律：不允许无止损持仓）
            if not hasattr(verify_stop_losses, 'last_check'):
                verify_stop_losses.last_check = 0
            if now - verify_stop_losses.last_check >= 1800:
                verify_stop_losses(positions, config)
                verify_stop_losses.last_check = now
            
            # 交叉验证（每小时同步Binance数据到本地DB）
            if not hasattr(sync_binance_data, 'last_sync'):
                sync_binance_data.last_sync = 0
            if now - sync_binance_data.last_sync >= 3600:
                sync_binance_data(positions)
                sync_binance_data.last_sync = now
            
            # 清理无持仓挂单（每5分钟检查一次）
            if not hasattr(cancel_stale_orders, 'last_check'):
                cancel_stale_orders.last_check = 0
            if now - cancel_stale_orders.last_check >= 300:
                cancel_stale_orders(positions, state)
                cancel_stale_orders.last_check = now
            
            # ★ 全局风控暂停检查（config.json 的 risk.daily_loss_limit_pct / max_consecutive_losses）
            global_risk_blocked, global_risk_reason = check_global_risk_pause(config, bal["balance"])
            if global_risk_blocked:
                log(f"🚫 全局风控触发: {global_risk_reason}，跳过本轮扫描")
                time.sleep(60)
                continue
            
            # 三级扫描 (按 mode 分开计时)
            for mode in ["long", "short"]:
                if not config["modes"][mode]["enabled"]:
                    continue
                
                # === 爆发检测层 (每5分钟) ===
                if not hasattr(scanner, 'last_burst_time'):
                    scanner.last_burst_time = {"long": 0, "short": 0}
                burst_interval = tiered_cfg.get("burst_interval", 300)
                
                if now - scanner.last_burst_time.get(mode, 0) >= burst_interval:
                    # 获取ticker做爆发检测 (1个API调用)
                    tickers = fetch_json(f"{BASE_URL}/fapi/v1/ticker/24hr")
                    if tickers:
                        bursts = scanner.detect_burst(tickers)
                        burst_list = bursts.get(mode, [])
                        if burst_list:
                            log(f"🔥 {mode.upper()}爆发检测: {len(burst_list)}个候选")
                            burst_results = scanner.scan_burst_candidates(burst_list, mode, config)
                            scanner.last_burst_time[mode] = now
                            
                            # 爆发信号直接开仓
                            held_symbols = {p["symbol"] for p in positions}
                            for r in burst_results:
                                if r["symbol"] not in held_symbols:
                                    max_pos = config["modes"][mode].get("max_positions", 4)
                                    mode_positions = [p for p in positions if p["direction"] == mode.upper()]
                                    if len(mode_positions) < max_pos:
                                        blocked, cd_reason = check_cooldown(r["symbol"], mode.upper())
                                        if not blocked:
                                            margin = bal["balance"] * config["modes"][mode].get("position_pct", 0.15)
                                            leverage = config["modes"][mode].get("leverage", 10)
                                            log(f"🔥 爆发开仓: {r['symbol']} {mode.upper()} 评分{r['score']}")
                                            trade = open_position(
                                                r["symbol"], mode.upper(), margin, leverage,
                                                r["price"], r["score"], r["reasons"], r["details"],
                                                r.get("stop_loss_pct"), r.get("pattern")
                                            )
                                            if trade:
                                                log(f"✅ 爆发开仓成功: {r['symbol']}")
                        else:
                            scanner.last_burst_time[mode] = now
                
                # === 关注列表扫描 (每5分钟) ===
                if not hasattr(scanner, 'last_watchlist_time'):
                    scanner.last_watchlist_time = 0
                watchlist_interval = tiered_cfg.get("watchlist_interval", 300)
                # 动态更新关注列表TTL
                scanner.watchlist_ttl = tiered_cfg.get("watchlist_ttl", 1800)
                
                if now - scanner.last_watchlist_time >= watchlist_interval and scanner.watchlist:
                    watchlist_results = scanner.rescan_watchlist(config)
                    scanner.last_watchlist_time = now
                    
                    # 关注列表升级为入场的信号
                    held_symbols = {p["symbol"] for p in positions}
                    for r in watchlist_results:
                        if r["symbol"] not in held_symbols:
                            max_pos = config["modes"][mode].get("max_positions", 4)
                            mode_positions = [p for p in positions if p["direction"] == mode.upper()]
                            if len(mode_positions) < max_pos:
                                blocked, cd_reason = check_cooldown(r["symbol"], mode.upper())
                                if not blocked:
                                    margin = bal["balance"] * config["modes"][mode].get("position_pct", 0.15)
                                    leverage = config["modes"][mode].get("leverage", 10)
                                    log(f"👁️ 关注→开仓: {r['symbol']} {mode.upper()} 评分{r['score']}")
                                    trade = open_position(
                                        r["symbol"], mode.upper(), margin, leverage,
                                        r["price"], r["score"], r["reasons"], r["details"],
                                        r.get("stop_loss_pct"), r.get("pattern")
                                    )
                                    if trade:
                                        log(f"✅ 关注开仓成功: {r['symbol']}")
                
                # Tier1: 每30分钟扫描Top300 → Top100
                if now - last_tier1_time[mode] >= tier1_interval:
                    scanner.tier1_results[mode] = scanner.scan_tier1_optimized(mode, config)
                    scanner.tier1_time = now
                    last_tier1_time[mode] = now
                    

                
                # Tier2: 每20分钟从Top100 → Top50 (依赖 Tier1 结果)
                if now - last_tier2_time[mode] >= tier2_interval and scanner.tier1_results[mode]:
                    scanner.tier2_results[mode] = scanner.scan_tier2(mode, config, scanner.tier1_results[mode])
                    scanner.tier2_time = now
                    last_tier2_time[mode] = now
                
                # Tier3: 每10分钟从Top50 → Top5 (依赖 Tier2 结果)
                if now - last_tier3_time[mode] >= tier3_interval and scanner.tier2_results[mode]:
                    top5 = scanner.scan_tier3(mode, config, scanner.tier2_results[mode])
                    scanner.tier3_results[mode] = top5  # 存储用于互斥检测
                    last_tier3_time[mode] = now
                    
                    # 构建报告
                    scan_results = []
                    held_symbols = {p["symbol"] for p in positions}
                    
                    for c in top5:
                        if c["symbol"] not in held_symbols:
                            scan_results.append({
                                "mode": mode,
                                "symbol": c["symbol"],
                                "score": c["score"],
                                "price": c["price"],
                                "reasons": c["reasons"],
                                "pattern": c.get("pattern"),
                            })
                    
                    # 开仓（检查max_positions）
                    if top5:
                        best = top5[0]
                        max_pos = config["modes"][mode].get("max_positions", 4)
                        mode_positions = [p for p in positions if p["direction"] == mode.upper()]
                        if best["symbol"] not in held_symbols and len(mode_positions) < max_pos:
                            # 冷却检查
                            blocked, reason = check_cooldown(best["symbol"], mode.upper())
                            if blocked:
                                log(f"🚫 {best['symbol']} {mode.upper()} {reason}，跳过")
                            else:
                                # 做空追跌保护：4h跌幅>20%不开空（防止底部做空）
                                if mode.upper() == "SHORT":
                                    price_change_4h = best.get("details", {}).get("price_change_4h", 0)
                                    if price_change_4h < -20:
                                        log(f"🚫 {best['symbol']} SHORT 4h已跌{price_change_4h:.1f}%，追跌风险太高，跳过")
                                        blocked = True
                                # 做多追涨保护：4h涨幅>30%不开多
                                if mode.upper() == "LONG" and not blocked:
                                    price_change_4h = best.get("details", {}).get("price_change_4h", 0)
                                    if price_change_4h > 30:
                                        log(f"🚫 {best['symbol']} LONG 4h已涨{price_change_4h:.1f}%，追涨风险太高，跳过")
                                        blocked = True
                                # === 2026-06-12 增强风控 ===
                                if not blocked:
                                    details = best.get("details", {})
                                    # 流动性止损: 量比<0.3 不开仓
                                    vol_ratio = details.get("volume_ratio", 1)
                                    if vol_ratio < 0.3:
                                        log(f"🚫 {best['symbol']} {mode.upper()} 流动性差(量比{vol_ratio:.1f}x)，跳过")
                                        blocked = True
                                    # 极端费率预警: 费率>0.5% 不开仓
                                    funding = details.get("funding_rate", 0)
                                    if mode.upper() == "LONG" and funding > 0.005:
                                        log(f"🚫 {best['symbol']} LONG 费率极端高({funding*100:.3f}%)，多头成本过高，跳过")
                                        blocked = True
                                    if mode.upper() == "SHORT" and funding < -0.005:
                                        log(f"🚫 {best['symbol']} SHORT 费率极端低({funding*100:.3f}%)，空头成本过高，跳过")
                                        blocked = True
                                    # 评分崩塌保护: 评分<50 不开仓(即使过了入口线)
                                    if best.get("score", 0) < 50:
                                        log(f"🚫 {best['symbol']} {mode.upper()} 评分偏低({best['score']}分)，跳过")
                                        blocked = True
                                
                                if not blocked:
                                    margin = bal["balance"] * config["modes"][mode].get("position_pct", 0.15)
                                    leverage = config["modes"][mode].get("leverage", 10)
                                    
                                    log(f"🎯 {mode.upper()}信号: {best['symbol']} 评分:{best['score']:.0f} (持仓{len(mode_positions)}/{max_pos})")
                                    trade = open_position(
                                        best["symbol"], mode.upper(), margin, leverage,
                                        best["price"], best["score"], best["reasons"], best["details"],
                                        best.get("stop_loss_pct"), best.get("pattern")
                                    )
                                    if trade:
                                        log(f"✅ 开仓成功: {best['symbol']} {mode.upper()}")
                        elif len(mode_positions) >= max_pos:
                            # 优胜劣汰: 尝试用高分信号替换最弱持仓
                            replace_enabled = config["modes"][mode].get("replace_enabled", False)
                            if replace_enabled and best["symbol"] not in held_symbols:
                                # 刷新持仓(防止manage_positions刚平掉的仓位被重复操作)
                                positions = get_positions()
                                mode_positions = [p for p in positions if p["direction"] == mode.upper()]
                                if len(mode_positions) >= max_pos:
                                    replaced = try_replace_position(best, mode, positions, bal, config, held_symbols)
                                    if not replaced:
                                        log(f"⚠️ {mode.upper()}已满仓 ({len(mode_positions)}/{max_pos})，跳过开仓")
                                else:
                                    # manage_positions刚平了一个仓位，直接开新仓
                                    blocked, reason = check_cooldown(best["symbol"], mode.upper())
                                    if blocked:
                                        log(f"🚫 {best['symbol']} {mode.upper()} {reason}，跳过")
                                    else:
                                        margin = bal["balance"] * config["modes"][mode].get("position_pct", 0.15)
                                        leverage = config["modes"][mode].get("leverage", 10)
                                        log(f"🎯 {mode.upper()}信号: {best['symbol']} 评分:{best['score']:.0f} (持仓{len(mode_positions)}/{max_pos}, 刚释放)")
                                        trade = open_position(
                                            best["symbol"], mode.upper(), margin, leverage,
                                            best["price"], best["score"], best["reasons"], best["details"],
                                            best.get("stop_loss_pct"), best.get("pattern")
                                        )
                                        if trade:
                                            log(f"✅ 开仓成功: {best['symbol']} {mode.upper()}")
                            else:
                                log(f"⚠️ {mode.upper()}已满仓 ({len(mode_positions)}/{max_pos})，跳过开仓")
                    
                    # 直接发送Tier3结果报告
                    if scan_results:
                        log(f"📊 发送{mode.upper()}报告: {len(scan_results)}个信号")
                        notify_scan_report(scan_results, bal)
            
            # === v3.1 多空互斥规则 ===
            # 同一币种同时出现多空信号时，分数差距<10则两边都不入场
            if scanner.tier3_results.get("long") and scanner.tier3_results.get("short"):
                long_syms = {r["symbol"]: r for r in scanner.tier3_results["long"]}
                short_syms = {r["symbol"]: r for r in scanner.tier3_results["short"]}
                conflicts = set(long_syms.keys()) & set(short_syms.keys())
                for sym in conflicts:
                    long_score = long_syms[sym].get("score", 0)
                    short_score = short_syms[sym].get("score", 0)
                    gap = abs(long_score - short_score)
                    if gap < 10:
                        log(f"⚖️ {sym} 多空冲突: LONG={long_score:.0f} SHORT={short_score:.0f} 差距{gap:.0f}<10，两边都不入场")
                        # 从两边结果中移除
                        scanner.tier3_results["long"] = [r for r in scanner.tier3_results["long"] if r["symbol"] != sym]
                        scanner.tier3_results["short"] = [r for r in scanner.tier3_results["short"] if r["symbol"] != sym]

            
            # 定期报告
            if now - state.get("last_report", 0) >= config["notifier"]["report_interval_seconds"]:
                mode_stats = {}
                for mode in ["LONG", "SHORT"]:
                    mp = [p for p in positions if p["direction"] == mode]
                    mode_stats[mode] = {
                        "count": len(mp),
                        "pnl": sum(p["unrealized_pnl"] for p in mp),
                    }
                notify_periodic_report(bal, positions, mode_stats)
                state["last_report"] = now
            
            # 定期复盘
            if now - state.get("last_review", 0) >= 3600:
                review = run_review()
                if review.get("status") == "reviewed":
                    notify_review(get_review_summary())
                    apply_review_feedback(review)
                    notify_feedback(get_feedback_summary())
                state["last_review"] = now
            
            # 定期交叉验证 (每小时)
            if now - state.get("last_validation", 0) >= 3600:
                try:
                    from backfill_exits import cross_validate
                    validation_result = cross_validate()
                    if validation_result.get("missing_in_system"):
                        log(f"  ⚠️ 发现系统缺失数据: {validation_result['missing_in_system']}")
                        from backfill_exits import backfill_exits
                        backfill_exits()
                    state["last_validation"] = now
                except Exception as e:
                    log(f"  ⚠️ 交叉验证失败: {e}")
            
            state['tp1_done'] = list(manage_positions.tp1_done) if hasattr(manage_positions, 'tp1_done') else []
            state['peak_pnl'] = manage_positions.peak_pnl if hasattr(manage_positions, 'peak_pnl') else {}
            save_state(state)
            time.sleep(60)  # 每分钟检查一次
            
        except KeyboardInterrupt:
            log("⏹️ 系统停止")
            save_state(state)
            break
        except Exception as e:
            log(f"❌ 错误: {e}\n{traceback.format_exc()}")
            time.sleep(60)

# ============================================================
# 状态管理
# ============================================================
def load_state():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
            # 确保algo_orders字段存在
            if "algo_orders" not in state:
                state["algo_orders"] = {}
            return state
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"last_report": 0, "last_review": 0, "scan_count": 0, "algo_orders": {}}

def save_state(state):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)

def print_status():
    """打印系统状态"""
    state = load_state()
    config = load_config()
    bal = get_balance()
    positions = get_positions()
    
    print("=" * 50)
    print("📊 妖币猎手统一系统状态")
    print("=" * 50)
    
    if bal:
        print(f"💰 余额: {bal['balance']:.2f}U")
        print(f"💰 可用: {bal['available']:.2f}U")
        print(f"💰 未实现盈亏: {bal['unrealized_pnl']:+.2f}U")
    
    print(f"\n📈 持仓: {len(positions)}个")
    for pos in positions:
        emoji = "🟢" if pos["pnl_pct"] > 0 else "🔴"
        print(f"  {emoji} {pos['symbol']}({pos['direction']}): {pos['pnl_pct']:+.2f}%")
    
    print(f"\n⚙️ 配置:")
    print(f"  做多入场: {config['modes']['long']['entry_score']}分")
    print(f"  做空入场: {config['modes']['short']['entry_score']}分")
    print(f"  扫描数量: {config['scan']['top_n']}个")
    print(f"  扫描间隔: {config['scan']['interval_seconds']}秒")
    
    print(f"\n⏰ 状态:")
    print(f"  上次报告: {datetime.fromtimestamp(state.get('last_report', 0)).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  上次复盘: {datetime.fromtimestamp(state.get('last_review', 0)).strftime('%Y-%m-%d %H:%M:%S')}")

# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--status":
            print_status()
        elif sys.argv[1] == "--loop":
            main_loop()
        else:
            print("用法: python unified_engine.py [--status|--loop]")
    else:
        print_status()
