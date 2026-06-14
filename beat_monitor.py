#!/usr/bin/env python3
"""
BEATUSDT 做空时机监控
持续追踪OI-价格关系，找到最佳做空入场点
"""
import json, time, sys, os
from datetime import datetime
from urllib.request import Request, ProxyHandler, build_opener

# 加载配置
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notifier import send as tg_send

PROXY_URL = 'http://YOUR_PROXY:PORT'
proxy_handler = ProxyHandler({'http': PROXY_URL, 'https': PROXY_URL})
opener = build_opener(proxy_handler)

SYMBOL = 'BEATUSDT'
CHECK_INTERVAL = 60  # 每60秒检查一次

def fetch_json(url, timeout=8):
    try:
        req = Request(url)
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except:
        return None

def get_market_data():
    """获取完整市场数据"""
    data = {}
    
    # 行情
    board = fetch_json('https://fapi.binance.com/fapi/v1/ticker/24hr')
    if board:
        for b in board:
            if b['symbol'] == SYMBOL:
                data['price'] = float(b['lastPrice'])
                data['change_24h'] = float(b['priceChangePercent'])
                data['volume'] = float(b['quoteVolume'])
                data['high'] = float(b['highPrice'])
                data['low'] = float(b['lowPrice'])
                break
    
    # 5min K线（最近6根 = 30分钟）
    klines = fetch_json(f'https://fapi.binance.com/fapi/v1/klines?symbol={SYMBOL}&interval=5m&limit=12')
    if klines:
        data['klines_5m'] = [{
            'open': float(k[1]), 'high': float(k[2]), 'low': float(k[3]),
            'close': float(k[4]), 'volume': float(k[7])
        } for k in klines]
    
    # OI 5min
    oi = fetch_json(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={SYMBOL}&period=5m&limit=12')
    if oi:
        data['oi_5m'] = [float(o['sumOpenInterest']) for o in oi]
    
    # 大户多空比
    ls = fetch_json(f'https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={SYMBOL}&period=5m&limit=6')
    if ls:
        data['ls_ratio'] = [{
            'long': float(r['longAccount']),
            'short': float(r['shortAccount']),
            'ratio': float(r['longShortRatio'])
        } for r in ls]
    
    # 散户多空比
    gls = fetch_json(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={SYMBOL}&period=5m&limit=6')
    if gls:
        data['global_ls'] = [float(r['longShortRatio']) for r in gls]
    
    # 资金费率
    funding = fetch_json(f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={SYMBOL}')
    if funding:
        data['funding'] = float(funding.get('lastFundingRate', 0))
    
    # 主动买卖
    taker = fetch_json(f'https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={SYMBOL}&period=5m&limit=6')
    if taker:
        buy_vol = sum(float(r['buyVol']) for r in taker)
        sell_vol = sum(float(r['sellVol']) for r in taker)
        data['taker_ratio'] = sell_vol / buy_vol if buy_vol > 0 else 1
    
    return data

def analyze_short_signal(data):
    """
    分析做空信号强度
    返回 (score, reasons, action)
    score: 0-100
    action: 'SHORT_NOW' | 'WAIT' | 'NOT_READY'
    """
    score = 0
    reasons = []
    
    if not data.get('oi_5m') or not data.get('klines_5m') or not data.get('ls_ratio'):
        return 0, ['数据不足'], 'NOT_READY'
    
    oi = data['oi_5m']
    klines = data['klines_5m']
    ls = data['ls_ratio']
    
    # ===== 1. OI-价格相位分析（30分）=====
    # 最近3根5min K线的OI-价格关系
    oi_changes = []
    price_changes = []
    for i in range(-3, 0):
        if abs(i) < len(oi):
            oi_chg = (oi[i] - oi[i-1]) / oi[i-1] * 100 if oi[i-1] > 0 else 0
            price_chg = (klines[i]['close'] - klines[i-1]['close']) / klines[i-1]['close'] * 100
            oi_changes.append(oi_chg)
            price_changes.append(price_chg)
    
    # 核心判断：新空头进场 = OI增 + 价格跌
    new_shorts = sum(1 for o, p in zip(oi_changes, price_changes) if o > 0.05 and p < -0.3)
    long_exit = sum(1 for o, p in zip(oi_changes, price_changes) if o < -0.05 and p < -0.3)
    
    if new_shorts >= 2:
        score += 30
        reasons.append(f'🔴 新空头压盘! {new_shorts}/3根OI增+价跌')
    elif new_shorts >= 1:
        score += 15
        reasons.append(f'🟡 有新空头进场 {new_shorts}/3')
    
    if long_exit >= 2:
        score -= 10
        reasons.append(f'⚠️ 多头出清中，反抽风险')
    
    # ===== 2. 多空比（25分）=====
    if ls:
        latest_ratio = ls[-1]['ratio']
        latest_long = ls[-1]['long']
        
        if latest_ratio > 2.0:
            score += 25
            reasons.append(f'🔴 多头极度拥挤! 比值{latest_ratio:.2f}')
        elif latest_ratio > 1.5:
            score += 20
            reasons.append(f'🟡 多头偏拥挤 {latest_ratio:.2f}')
        elif latest_ratio > 1.2:
            score += 10
            reasons.append(f'多头略多 {latest_ratio:.2f}')
        elif latest_ratio < 0.5:
            score -= 15
            reasons.append(f'⚠️ 空头拥挤，可能反弹')
    
    # 散户多空比
    if data.get('global_ls'):
        retail_ratio = data['global_ls'][-1]
        if retail_ratio < 0.3:
            score += 10
            reasons.append(f'散户偏空{retail_ratio:.2f}，可能被轧')
        elif retail_ratio < 0.5:
            score += 5
            reasons.append(f'散户偏空{retail_ratio:.2f}')
    
    # ===== 3. 资金费率（10分）=====
    funding = data.get('funding', 0)
    if funding > 0.05:
        score += 10
        reasons.append(f'高费率{funding*100:.2f}%')
    elif funding > 0.01:
        score += 5
        reasons.append(f'正费率{funding*100:.3f}%')
    elif funding < -0.01:
        score -= 5
        reasons.append(f'负费率，做多有收益')
    
    # ===== 4. 成交量确认（15分）=====
    if data.get('taker_ratio'):
        taker = data['taker_ratio']
        if taker > 1.3:
            score += 15
            reasons.append(f'卖盘主导 卖/买={taker:.2f}')
        elif taker > 1.1:
            score += 8
            reasons.append(f'卖盘偏强 {taker:.2f}')
        elif taker < 0.7:
            score -= 10
            reasons.append(f'⚠️ 买盘主导，反弹中')
    
    # ===== 5. K线形态（10分）=====
    if len(klines) >= 3:
        # 最近3根是否有上影线（抛压）
        upper_wicks = 0
        for k in klines[-3:]:
            body_top = max(k['open'], k['close'])
            upper = k['high'] - body_top
            body = abs(k['close'] - k['open'])
            if body > 0 and upper / body > 0.5:
                upper_wicks += 1
        
        if upper_wicks >= 2:
            score += 10
            reasons.append(f'上影线密集({upper_wicks}根)，抛压重')
    
    # ===== 6. 跌幅确认（10分）=====
    change = data.get('change_24h', 0)
    if change < -15:
        score += 5
        reasons.append(f'24h已跌{change:.0f}%，但趋势延续')
    elif change < -5:
        score += 10
        reasons.append(f'24h跌{change:.0f}%')
    
    # ===== 行动判断 =====
    if score >= 75:
        action = 'SHORT_NOW'
    elif score >= 55:
        action = 'WAIT_READY'
    elif score >= 35:
        action = 'WAIT'
    else:
        action = 'NOT_READY'
    
    return score, reasons, action

def format_report(data, score, reasons, action):
    """格式化报告"""
    action_emoji = {
        'SHORT_NOW': '🔴 立即做空!',
        'WAIT_READY': '🟡 接近就绪',
        'WAIT': '⏳ 继续等待',
        'NOT_READY': '❌ 时机未到'
    }
    
    lines = [
        f"{'='*40}",
        f"📡 BEATUSDT 做空监控",
        f"{'='*40}",
        f"价格: {data.get('price', 0):.4f} | 24h: {data.get('change_24h', 0):+.1f}%",
        f"评分: {score}/100 | {action_emoji.get(action, action)}",
        f"",
    ]
    
    # OI状态
    oi = data.get('oi_5m', [])
    if len(oi) >= 2:
        oi_chg = (oi[-1] - oi[0]) / oi[0] * 100
        lines.append(f"OI(1h): {oi_chg:+.2f}%")
    
    # 多空比
    ls = data.get('ls_ratio', [])
    if ls:
        lines.append(f"大户多空比: {ls[-1]['ratio']:.2f} (多{ls[-1]['long']*100:.0f}%/空{ls[-1]['short']*100:.0f}%)")
    
    # 散户
    if data.get('global_ls'):
        lines.append(f"散户多空比: {data['global_ls'][-1]:.2f}")
    
    lines.append(f"")
    lines.append(f"信号:")
    for r in reasons:
        lines.append(f"  {r}")
    
    if action == 'SHORT_NOW':
        lines.append(f"")
        lines.append(f"🎯 建议: 此刻做空BEAT")
        lines.append(f"   止损: {data.get('price', 0) * 1.06:.4f} (+6%)")
        lines.append(f"   止盈: {data.get('price', 0) * 0.60:.4f} (-40%)")
    
    return '\n'.join(lines)

def main():
    """主循环"""
    print(f"🚀 BEATUSDT 做空监控启动")
    print(f"   检查间隔: {CHECK_INTERVAL}秒")
    print()
    
    last_alert_score = 0
    alert_cooldown = 0
    
    while True:
        try:
            data = get_market_data()
            score, reasons, action = analyze_short_signal(data)
            report = format_report(data, score, reasons, action)
            
            print(report)
            print()
            
            # 发送TG提醒
            if action == 'SHORT_NOW' and score > last_alert_score:
                tg_send(report)
                last_alert_score = score
                alert_cooldown = 300  # 5分钟冷却
            elif action == 'WAIT_READY' and score >= 60 and alert_cooldown <= 0:
                tg_send(report)
                alert_cooldown = 300
            
            alert_cooldown -= CHECK_INTERVAL
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n⏹ 监控停止")
            break
        except Exception as e:
            print(f"❌ 错误: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
