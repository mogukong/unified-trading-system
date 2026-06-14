#!/usr/bin/env python3
"""
妖币猎手统一系统 - 每日复盘脚本（完整版）
1. 涨幅榜Top10评分分析 + 为什么没交易
2. 跌幅榜Top10（做空机会复盘）
3. 策略优化建议（基于实际数据判断）
4. 系统健康检查（bug/报错/异常）
"""
import json, time, hmac, hashlib, os, glob
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener

# ========== 配置 ==========
BASE_DIR = os.path.dirname(os.path.realpath(__file__))
PROXY_URL = 'http://YOUR_PROXY:PORT'
proxy_handler = ProxyHandler({'http': PROXY_URL, 'https': PROXY_URL})
opener = build_opener(proxy_handler)

# 读取.env
api_key = api_secret = ''
env_path = os.path.join(BASE_DIR, '.env')
if not os.path.exists(env_path):
    env_path = os.path.join(os.path.dirname(BASE_DIR), '.env')
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line.startswith('BINANCE_API_KEY=') and not api_key:
            api_key = line.split('=', 1)[1].strip().strip('"').strip("'")
        elif line.startswith('BINANCE_API_SECRET=') and not api_secret:
            api_secret = line.split('=', 1)[1].strip().strip('"').strip("'")

# 读取配置
config_path = os.path.join(BASE_DIR, 'config.json')
with open(config_path) as f:
    CONFIG = json.load(f)


def api_get(endpoint, params=None):
    if params is None:
        params = {}
    params['timestamp'] = int(time.time() * 1000)
    params['recvWindow'] = 5000
    query = urlencode(params)
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f'https://fapi.binance.com{endpoint}?{query}&signature={sig}'
    req = Request(url, headers={'X-MBX-APIKEY': api_key})
    try:
        with opener.open(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except:
        return None


def fetch_json(url, timeout=10):
    try:
        req = Request(url)
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except:
        return None


def get_funding_rate(symbol):
    """获取资金费率"""
    data = fetch_json(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1')
    if data and len(data) > 0:
        return float(data[0]['fundingRate'])
    return 0


def get_oi_change(symbol):
    """获取OI变化（24h）"""
    data = fetch_json(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=24')
    if data and len(data) >= 2:
        old_oi = float(data[0]['sumOpenInterest'])
        new_oi = float(data[-1]['sumOpenInterest'])
        if old_oi > 0:
            return (new_oi - old_oi) / old_oi * 100
    return 0


def score_long(symbol, change_24h):
    """快速做多评分（简化版）"""
    score = 0
    # 涨幅
    if 3 <= change_24h <= 15:
        score += 20
    elif change_24h > 15:
        score += 5  # 追高扣分
    # 资金费率
    funding = get_funding_rate(symbol)
    if funding < 0:
        score += 15  # 负费率=做空付费=利于做多
    elif funding < 0.01:
        score += 10
    # OI变化
    oi_change = get_oi_change(symbol)
    if oi_change > 5:
        score += 15  # OI增加=新资金进场
    # 成交量（用24h成交额代替）
    score += 15  # 基础分
    # 动量
    score += 10
    # 风险
    score += 10
    return score, funding, oi_change


def score_short(symbol, change_24h):
    """快速做空评分（简化版）"""
    score = 0
    # 跌幅
    if change_24h < -10:
        score += 20
    elif change_24h < -5:
        score += 15
    elif change_24h < 0:
        score += 10
    # 资金费率
    funding = get_funding_rate(symbol)
    if funding > 0.01:
        score += 15  # 正费率=做多付费=利于做空
    elif funding > 0:
        score += 10
    # OI变化（OI下降=平仓=趋势延续）
    oi_change = get_oi_change(symbol)
    if oi_change < -5:
        score += 15
    # 基础分
    score += 15
    score += 10
    score += 10
    return score, funding, oi_change


def check_system_health():
    """系统健康检查"""
    issues = []
    
    # 1. 检查日志文件
    log_file = os.path.join(BASE_DIR, 'engine_log.txt')
    if os.path.exists(log_file):
        with open(log_file) as f:
            lines = f.readlines()
        
        # 检查最近的错误
        recent_errors = []
        for line in lines[-100:]:
            if 'ERROR' in line or 'Traceback' in line or 'Exception' in line:
                recent_errors.append(line.strip())
        
        if recent_errors:
            issues.append(f"⚠️ 日志中有{len(recent_errors)}条错误")
            for err in recent_errors[-3:]:  # 只显示最近3条
                issues.append(f"  → {err[:100]}")
        
        # 检查最后活动时间
        if lines:
            last_line = lines[-1].strip()
            issues.append(f"📝 最后日志: {last_line[:80]}")
    else:
        issues.append("❌ 日志文件不存在")
    
    # 2. 检查内存文件
    memory_files = [
        ('trade_memory.json', 'L1交易记忆'),
        ('exit_memory.json', 'L2离场记忆'),
        ('trade_history.json', 'L1交易历史'),
        ('exit_history.json', 'L2离场历史'),
    ]
    for fname, desc in memory_files:
        fpath = os.path.join(BASE_DIR, 'memory', fname)
        if os.path.exists(fpath):
            try:
                with open(fpath) as f:
                    data = json.load(f)
                count = len(data) if isinstance(data, list) else 1
                issues.append(f"✅ {desc}: {count}条记录")
            except json.JSONDecodeError:
                issues.append(f"❌ {desc}: JSON格式损坏!")
        else:
            issues.append(f"⚠️ {desc}: 文件不存在")
    
    # 3. 检查config.json完整性
    required_keys = ['modes', 'risk', 'memory']
    for key in required_keys:
        if key not in CONFIG:
            issues.append(f"❌ config.json缺少'{key}'配置")
    
    # 4. 检查止损单状态（如果有持仓）
    positions = api_get('/fapi/v3/positionRisk')
    if positions:
        active_symbols = []
        for p in positions:
            if float(p['positionAmt']) != 0:
                active_symbols.append(p['symbol'])
        
        # 检查algo orders
        algo_orders = api_get('/fapi/v1/algoOrders')
        if algo_orders is not None:
            protected = set()
            if isinstance(algo_orders, dict) and 'orders' in algo_orders:
                for o in algo_orders['orders']:
                    protected.add(o.get('symbol', ''))
            elif isinstance(algo_orders, list):
                for o in algo_orders:
                    protected.add(o.get('symbol', ''))
            
            for sym in active_symbols:
                if sym not in protected:
                    issues.append(f"🔴 {sym} 无物理止损单!")
                else:
                    issues.append(f"✅ {sym} 止损单正常")
    
    # 5. 检查进程是否存活
    import subprocess
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
        if 'unified_engine' in result.stdout:
            issues.append("✅ 引擎进程存活")
        else:
            issues.append("❌ 引擎进程未运行!")
    except:
        issues.append("⚠️ 无法检查进程状态")
    
    return issues


def daily_review():
    now = datetime.now()
    print('=' * 50)
    print('📋 妖币猎手统一系统 每日复盘')
    print(f'📅 {now.strftime("%Y-%m-%d %H:%M")}')
    print('=' * 50)

    # ==================== 1. 账户状态 ====================
    balance = api_get('/fapi/v2/balance')
    bal_usdt = available = pnl = 0
    if balance:
        for b in balance:
            if b['asset'] == 'USDT':
                bal_usdt = float(b['balance'])
                available = float(b['availableBalance'])
                pnl = float(b.get('crossUnPnl', 0))

    print('')
    print('━━━ 💰 账户状态 ━━━')
    print(f'余额: {bal_usdt:.2f}U | 可用: {available:.2f}U | 浮盈: {pnl:+.2f}U')

    # ==================== 2. 持仓 ====================
    positions = api_get('/fapi/v3/positionRisk')
    active_pos = []
    if positions:
        for p in positions:
            amt = float(p['positionAmt'])
            if amt != 0:
                entry = float(p['entryPrice'])
                mark = float(p['markPrice'])
                direction = 'LONG' if amt > 0 else 'SHORT'
                if direction == 'LONG':
                    pct = (mark - entry) / entry * 100
                else:
                    pct = (entry - mark) / entry * 100
                active_pos.append({
                    'symbol': p['symbol'],
                    'direction': direction,
                    'pnl_pct': pct,
                    'entry': entry,
                    'mark': mark,
                    'amt': abs(amt)
                })

    wins = sum(1 for p in active_pos if p['pnl_pct'] > 0)
    losses = sum(1 for p in active_pos if p['pnl_pct'] <= 0)
    win_rate = wins / len(active_pos) * 100 if active_pos else 0

    print('')
    print('━━━ 📈 当前持仓 ━━━')
    print(f'持仓: {len(active_pos)}笔 | 胜{wins} 负{losses} | 胜率 {win_rate:.0f}%')
    for pos in active_pos:
        emoji = '🟢' if pos['pnl_pct'] > 0 else '🔴'
        print(f'  {emoji} {pos["symbol"]} {pos["direction"]}: {pos["pnl_pct"]:+.2f}%')

    # ==================== 3. 涨幅榜Top10 + 评分分析 ====================
    board = fetch_json('https://fapi.binance.com/fapi/v1/ticker/24hr')
    our_symbols = {p['symbol'] for p in active_pos}
    gainers = []
    missed_opportunities = []
    short_opportunities = []
    long_entry_score = CONFIG.get('modes', {}).get('long', {}).get('entry_score', 70)
    short_entry_score = CONFIG.get('modes', {}).get('short', {}).get('entry_score', 60)

    if board:
        usdt_pairs = [b for b in board if b['symbol'].endswith('USDT')
                      and float(b.get('quoteVolume', 0)) > 1000000]  # 过滤低流动性
        
        # 涨幅榜
        gainers = sorted(usdt_pairs, key=lambda x: float(x.get('priceChangePercent', 0)), reverse=True)[:10]
        # 跌幅榜
        losers = sorted(usdt_pairs, key=lambda x: float(x.get('priceChangePercent', 0)))[:10]

        print('')
        print('━━━ 📊 涨幅榜 Top10 评分分析 ━━━')
        missed_opportunities = []
        
        for i, pair in enumerate(gainers, 1):
            sym = pair['symbol']
            change = float(pair.get('priceChangePercent', 0))
            vol = float(pair.get('quoteVolume', 0))
            
            if sym in our_symbols:
                status = '✅ 已持有'
                score_str = '-'
                funding_str = '-'
                oi_str = '-'
                reason = ''
            else:
                score, funding, oi = score_long(sym, change)
                score_str = f'{score}'
                funding_str = f'{funding*100:.3f}%'
                oi_str = f'{oi:+.1f}%'
                
                if score >= long_entry_score:
                    status = '✅ 达标'
                    reason = ''
                else:
                    status = '❌ 未达标'
                    reasons = []
                    if change > 15:
                        reasons.append('涨幅过高(追高风险)')
                    if funding > 0.01:
                        reasons.append(f'资金费率偏高({funding*100:.3f}%)')
                    if score < 50:
                        reasons.append('综合评分过低')
                    reason = f' 原因: {", ".join(reasons)}' if reasons else ''
                    missed_opportunities.append({
                        'symbol': sym,
                        'change': change,
                        'score': score,
                        'reasons': reasons
                    })
            
            print(f'{i:2}. {sym:<18} 涨幅:{change:+6.1f}%  评分:{score_str:>3}  费率:{funding_str}  OI:{oi_str}  {status}{reason}')
            time.sleep(0.1)  # 避免API限频

        # ==================== 4. 跌幅榜Top10 + 做空评分 ====================
        print('')
        print('━━━ 📉 跌幅榜 Top10 做空分析 ━━━')
        short_opportunities = []
        
        for i, pair in enumerate(losers, 1):
            sym = pair['symbol']
            change = float(pair.get('priceChangePercent', 0))
            vol = float(pair.get('quoteVolume', 0))
            
            if sym in our_symbols:
                pos = [p for p in active_pos if p['symbol'] == sym][0]
                status = f'✅ 已做空 {pos["pnl_pct"]:+.1f}%'
                score_str = '-'
                funding_str = '-'
                oi_str = '-'
            else:
                score, funding, oi = score_short(sym, change)
                score_str = f'{score}'
                funding_str = f'{funding*100:.3f}%'
                oi_str = f'{oi:+.1f}%'
                
                if score >= short_entry_score:
                    status = '✅ 可做空'
                    short_opportunities.append({
                        'symbol': sym,
                        'change': change,
                        'score': score
                    })
                else:
                    status = '❌ 评分不足'
            
            print(f'{i:2}. {sym:<18} 跌幅:{change:+6.1f}%  评分:{score_str:>3}  费率:{funding_str}  OI:{oi_str}  {status}')
            time.sleep(0.1)

    # ==================== 5. 系统健康检查 ====================
    print('')
    print('━━━ 🔧 系统健康检查 ━━━')
    health_issues = check_system_health()
    for issue in health_issues:
        print(f'  {issue}')

    # ==================== 6. 策略优化建议（智能判断） ====================
    print('')
    print('━━━ 💡 策略优化建议 ━━━')
    
    optimizations = []
    
    # 判断1: 涨幅榜Top10中有多少我们能抓住
    if board and gainers:
        top5_changes = [float(g.get('priceChangePercent', 0)) for g in gainers[:5]]
        avg_top5 = sum(top5_changes) / len(top5_changes)
        
        if avg_top5 > 30:
            optimizations.append(f"📈 涨幅榜Top5平均涨幅{avg_top5:.0f}%，市场极度活跃")
            if missed_opportunities:
                high_miss = [m for m in missed_opportunities if m['change'] > 30]
                if high_miss:
                    optimizations.append(f"⚠️ {len(high_miss)}个涨30%+的币错过，原因分析:")
                    for m in high_miss[:3]:
                        optimizations.append(f"   {m['symbol']} +{m['change']:.0f}%: {', '.join(m['reasons'])}")
                    
                    # 判断是否需要降低门槛
                    if all('涨幅过高' in str(m['reasons']) for m in high_miss):
                        optimizations.append("→ 涨幅过高是主要过滤原因，当前过滤合理（追高风险大）")
                    else:
                        optimizations.append(f"→ 考虑将entry_score从{long_entry_score}降到{long_entry_score-5}")
    
    # 判断2: 跌幅榜是否有做空机会
    if short_opportunities:
        optimizations.append(f"📉 跌幅榜发现{len(short_opportunities)}个可做空标的:")
        for opp in short_opportunities[:3]:
            optimizations.append(f"   {opp['symbol']} 跌{opp['change']:.0f}% 评分{opp['score']}")
        optimizations.append("→ 做空模式已激活，下次扫描会自动识别")
    
    # 判断3: 持仓分析
    if active_pos:
        longs = [p for p in active_pos if p['direction'] == 'LONG']
        shorts = [p for p in active_pos if p['direction'] == 'SHORT']
        
        long_avg = sum(p['pnl_pct'] for p in longs) / len(longs) if longs else 0
        short_avg = sum(p['pnl_pct'] for p in shorts) / len(shorts) if shorts else 0
        
        if long_avg < -2:
            optimizations.append(f"⚠️ 做多持仓平均亏损{long_avg:.1f}%，考虑提高做多门槛或缩小仓位")
        if short_avg > 3:
            optimizations.append(f"✅ 做空平均盈利{short_avg:.1f}%，做空策略表现良好")
        
        # 判断4: 是否有持仓该止损
        stuck_positions = [p for p in active_pos if p['pnl_pct'] < -5]
        if stuck_positions:
            optimizations.append(f"🔴 {len(stuck_positions)}个持仓亏损>5%，需检查止损是否生效:")
            for p in stuck_positions:
                optimizations.append(f"   {p['symbol']} {p['direction']} {p['pnl_pct']:+.1f}%")
    
    # 判断5: 账户风险
    if bal_usdt > 0:
        risk_pct = abs(pnl) / bal_usdt * 100
        if pnl < 0 and risk_pct > 10:
            optimizations.append(f"⚠️ 浮亏占余额{risk_pct:.1f}%，注意风险控制")
    
    if not optimizations:
        optimizations.append("✅ 当前策略运行正常，无需调整")
    
    for opt in optimizations:
        print(f'  {opt}')

    # ==================== 7. 总结 ====================
    print('')
    print('━━━ 📋 总结 ━━━')
    print(f'余额: {bal_usdt:.2f}U | 持仓: {len(active_pos)}笔 | 胜率: {win_rate:.0f}%')
    if pnl >= 0:
        print(f'状态: ✅ 盈利中')
    else:
        print(f'状态: ⚠️ 浮亏中')
    
    print('')
    print('=' * 50)


if __name__ == '__main__':
    daily_review()
