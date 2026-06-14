"""
补录离场记录 - 从Binance历史成交中恢复
交叉验证: Binance成交 vs 系统记录
"""
import json, os, sys, time, hmac, hashlib
from datetime import datetime
from urllib.request import Request, ProxyHandler, build_opener
from urllib.parse import urlencode

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# 加载API
from unified_engine import api_get, log
from memory.exit_memory import record_exit, _load as load_exits, _save as save_exits
from memory.trade_memory import _load as load_trades


def fetch_all_trades():
    """获取所有USDT永续合约的成交记录"""
    # 先获取所有交易对
    exchange_info = api_get("/fapi/v1/exchangeInfo")
    if not exchange_info:
        return []
    
    usdt_symbols = [s["symbol"] for s in exchange_info.get("symbols", []) 
                    if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"]
    
    all_trades = []
    for sym in usdt_symbols[:50]:  # 限制数量避免太慢
        trades = api_get("/fapi/v1/userTrades", {"symbol": sym, "limit": 500})
        if trades:
            all_trades.extend(trades)
        time.sleep(0.1)  # 避免限流
    
    return all_trades


def match_trades_to_exits(trades):
    """将成交记录匹配为开仓-平仓对"""
    # 按symbol分组
    by_symbol = {}
    for t in trades:
        sym = t.get("symbol", "")
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(t)
    
    exits = []
    for sym, sym_trades in by_symbol.items():
        # 按时间排序
        sym_trades.sort(key=lambda x: x.get("time", 0))
        
        # 分离买入和卖出
        buys = [t for t in sym_trades if t.get("side") == "BUY"]
        sells = [t for t in sym_trades if t.get("side") == "SELL"]
        
        # 计算总买入和卖出
        total_buy_qty = sum(float(t["qty"]) for t in buys)
        total_sell_qty = sum(float(t["qty"]) for t in sells)
        
        # 计算加权平均价格
        if total_buy_qty > 0:
            avg_buy_price = sum(float(t["qty"]) * float(t["price"]) for t in buys) / total_buy_qty
        else:
            avg_buy_price = 0
        
        if total_sell_qty > 0:
            avg_sell_price = sum(float(t["qty"]) * float(t["price"]) for t in sells) / total_sell_qty
        else:
            avg_sell_price = 0
        
        # 计算手续费
        buy_commission = sum(float(t.get("commission", 0)) for t in buys)
        sell_commission = sum(float(t.get("commission", 0)) for t in sells)
        
        # 判断方向
        # 如果先买后卖 -> LONG
        # 如果先卖后买 -> SHORT
        if buys and sells:
            first_buy_time = min(t.get("time", 0) for t in buys)
            first_sell_time = min(t.get("time", 0) for t in sells)
            
            if first_buy_time < first_sell_time:
                direction = "LONG"
                entry_price = avg_buy_price
                exit_price = avg_sell_price
            else:
                direction = "SHORT"
                entry_price = avg_sell_price
                exit_price = avg_buy_price
        elif buys:
            direction = "LONG"
            entry_price = avg_buy_price
            exit_price = 0
        elif sells:
            direction = "SHORT"
            entry_price = avg_sell_price
            exit_price = 0
        else:
            continue
        
        # 计算已平仓数量
        closed_qty = min(total_buy_qty, total_sell_qty)
        if closed_qty <= 0:
            continue
        
        # 计算盈亏
        if direction == "LONG":
            pnl_usd = (exit_price - entry_price) * closed_qty - buy_commission - sell_commission
            pnl_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0
        else:  # SHORT
            pnl_usd = (entry_price - exit_price) * closed_qty - buy_commission - sell_commission
            pnl_pct = (entry_price - exit_price) / entry_price if entry_price > 0 else 0
        
        # 获取时间
        if direction == "LONG":
            entry_time = min(t.get("time", 0) for t in buys)
            exit_time = max(t.get("time", 0) for t in sells)
        else:
            entry_time = min(t.get("time", 0) for t in sells)
            exit_time = max(t.get("time", 0) for t in buys)
        
        hold_hours = (exit_time - entry_time) / (1000 * 3600) if entry_time and exit_time else 0
        
        exits.append({
            "symbol": sym,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": closed_qty,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "hold_hours": hold_hours,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "buy_count": len(buys),
            "sell_count": len(sells),
        })
    
    return exits


def backfill_exits():
    """补录离场记录"""
    log("📥 开始补录离场记录...")
    
    # 获取所有成交
    trades = fetch_all_trades()
    log(f"  获取到 {len(trades)} 笔成交记录")
    
    # 匹配为平仓对
    exits = match_trades_to_exits(trades)
    log(f"  匹配到 {len(exits)} 笔平仓记录")
    
    # 加载现有离场记录
    existing_exits = load_exits()
    existing_trade_ids = {e.get("trade_id") for e in existing_exits}
    
    # 补录新记录
    new_count = 0
    for exit_data in exits:
        # 生成唯一ID
        trade_id = f"BACKFILL_{exit_data['symbol']}_{exit_data['entry_time']}"
        
        if trade_id in existing_trade_ids:
            continue
        
        # 计算峰值盈利
        if exit_data["pnl_pct"] > 0:
            peak_pnl = exit_data["pnl_pct"]
        else:
            peak_pnl = 0  # 亏损交易不知道峰值
        
        # 记录离场
        record_exit(
            trade_id=trade_id,
            symbol=exit_data["symbol"],
            direction=exit_data["direction"],
            entry_price=exit_data["entry_price"],
            exit_price=exit_data["exit_price"],
            pnl_pct=exit_data["pnl_pct"],
            pnl_usd=exit_data["pnl_usd"],
            exit_type="backfill",  # 标记为补录
            hold_hours=exit_data["hold_hours"],
            peak_pnl=peak_pnl,
        )
        new_count += 1
        log(f"  ✅ 补录: {exit_data['symbol']} {exit_data['direction']} | 盈亏:{exit_data['pnl_pct']*100:+.2f}% ({exit_data['pnl_usd']:+.2f}U)")
    
    log(f"✅ 补录完成: {new_count} 笔新记录")
    return new_count


def cross_validate():
    """交叉验证: Binance成交 vs 系统记录"""
    log("🔍 开始交叉验证...")
    
    # 获取Binance成交
    trades = fetch_all_trades()
    binance_exits = match_trades_to_exits(trades)
    
    # 获取系统记录
    system_exits = load_exits()
    
    # 对比
    binance_symbols = {e["symbol"] for e in binance_exits}
    system_symbols = {e["symbol"] for e in system_exits}
    
    missing_in_system = binance_symbols - system_symbols
    missing_in_binance = system_symbols - binance_symbols
    
    log(f"  Binance平仓: {len(binance_exits)} 笔")
    log(f"  系统记录: {len(system_exits)} 笔")
    
    if missing_in_system:
        log(f"  ⚠️ 系统缺失: {missing_in_system}")
    
    if missing_in_binance:
        log(f"  ⚠️ Binance缺失: {missing_in_binance}")
    
    if not missing_in_system and not missing_in_binance:
        log("  ✅ 数据一致")
    
    return {
        "binance_count": len(binance_exits),
        "system_count": len(system_exits),
        "missing_in_system": list(missing_in_system),
        "missing_in_binance": list(missing_in_binance),
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "backfill":
            backfill_exits()
        elif sys.argv[1] == "validate":
            cross_validate()
        else:
            print("用法: python backfill_exits.py [backfill|validate]")
    else:
        print("用法: python backfill_exits.py [backfill|validate]")
