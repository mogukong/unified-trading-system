"""
OI资金流分析模块 - 操盘手视角
核心思想：站在庄家角度理解OI-价格关系，而不只是技术指标

阶段一（最安全）：OI增+价格涨 = 新杠杆资金进场做多，demand>supply
  → 这是早期拉升，仓位风险最低，最舒服

阶段二（复杂）：OI变化不直观，多角色博弈
  - 空头爆仓/平仓 → OI减+买单 → 不是真正的做多力量
  - 多头平仓 → OI减+卖单 → 获利了结
  - 多头开仓 → OI增+买单 → 真正的需求
  - 空头开仓 → OI增+卖单 → 对手盘进场

阶段三（盘整洗盘）：
  - OI减+价格跌 → 早期多头获利了结+震仓
  - 短时间跌破吸引做空 → 套住空头后收回

做空也一样：
  - 价格跌+OI增 = 新空头主动压盘（有质量的下跌）
  - 价格跌+OI减 = 多头爆仓出清（反抽风险高，降权）
"""

import time
import hmac
import hashlib
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener
from concurrent.futures import ThreadPoolExecutor, as_completed

PROXY_URL = 'http://YOUR_PROXY:PORT'
proxy_handler = ProxyHandler({'http': PROXY_URL, 'https': PROXY_URL})
opener = build_opener(proxy_handler)


def _fetch_json(url, timeout=8):
    try:
        req = Request(url)
        with opener.open(req, timeout=timeout) as r:
            import json
            return json.loads(r.read().decode())
    except Exception:
        return None


def _api_get(endpoint, params, api_key, api_secret):
    params['timestamp'] = int(time.time() * 1000)
    params['recvWindow'] = 5000
    query = urlencode(params)
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f'https://fapi.binance.com{endpoint}?{query}&signature={sig}'
    req = Request(url, headers={'X-MBX-APIKEY': api_key})
    try:
        with opener.open(req, timeout=10) as r:
            import json
            return json.loads(r.read().decode())
    except Exception:
        return None


def fetch_oi_history(symbol, period='5m', limit=48):
    """
    获取OI历史（最近48根5min K线 = 4小时）
    用来分析OI变化的动态，而不是只看24h单点
    """
    data = _fetch_json(
        f'https://fapi.binance.com/futures/data/openInterestHist'
        f'?symbol={symbol}&period={period}&limit={limit}'
    )
    return data or []


def fetch_klines(symbol, interval='5m', limit=48):
    """获取K线（价格+成交量）"""
    data = _fetch_json(
        f'https://fapi.binance.com/fapi/v1/klines'
        f'?symbol={symbol}&interval={interval}&limit={limit}'
    )
    if not data:
        return []
    # [open_time, open, high, low, close, volume, close_time, quote_vol, ...]
    return [
        {
            'time': k[0],
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[5]),
            'quote_volume': float(k[7]),
        }
        for k in data
    ]


def fetch_klines_15m(symbol, limit=64):
    """获取15min K线（64根 = 16小时，用于V型反转检测和中期趋势）"""
    return fetch_klines(symbol, '15m', limit)


def fetch_klines_1h(symbol, limit=24):
    """获取1h K线（24根 = 1天，用于4h趋势上下文）"""
    return fetch_klines(symbol, '1h', limit)


def fetch_funding_rate(symbol):
    """获取当前资金费率"""
    data = _fetch_json(
        f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}'
    )
    if data:
        return float(data.get('lastFundingRate', 0))
    return 0


def fetch_long_short_ratio(symbol, period='5m', limit=48):
    """获取多空比"""
    data = _fetch_json(
        f'https://fapi.binance.com/futures/data/topLongShortPositionRatio'
        f'?symbol={symbol}&period={period}&limit={limit}'
    )
    return data or []


def detect_v_reversal(klines_15m, klines_5m, vol_ratio_5m):
    """
    V型反转检测：暴跌放量 → 强力收回
    
    核心逻辑：
    1. 在15min数据中找到最近的"低谷"（跌幅 > 3%）
    2. 检查从低谷的收回力度（收回 > 50% 的跌幅 = 有效V型）
    3. 暴跌时放量 = 主力吸筹（不是散户恐慌）
    4. 收回速度越快越强（1-3根K线收回 = 强V，>6根 = 弱V）
    
    返回:
        detected: bool
        score: 0-25 (额外加分)
        details: str
    """
    if not klines_15m or len(klines_15m) < 8:
        return False, 0, ''
    
    # 取最近16根15min K线（4小时）
    bars = klines_15m[-16:]
    closes = [b['close'] for b in bars]
    volumes = [b['quote_volume'] for b in bars]
    lows = [b['low'] for b in bars]
    
    # 1. 找到最低点位置
    min_low_idx = lows.index(min(lows))
    min_low = lows[min_low_idx]
    
    # 低谷必须在最近8根K线内（太远的不算当前信号）
    if min_low_idx < len(bars) - 8:
        return False, 0, ''
    
    # 2. 计算低谷前的高点（下跌起点）
    pre_high = max(closes[:min_low_idx + 1]) if min_low_idx > 0 else closes[0]
    if pre_high == 0:
        return False, 0, ''
    
    drop_pct = (pre_high - min_low) / pre_high * 100
    
    # 跌幅至少3%才算有意义的暴跌
    if drop_pct < 3:
        return False, 0, ''
    
    # 3. 计算从低谷的收回
    current_price = closes[-1]
    recovery_pct = (current_price - min_low) / (pre_high - min_low) * 100 if (pre_high - min_low) > 0 else 0
    
    # 收回 > 50% 才算有效V型
    if recovery_pct < 50:
        return False, 0, ''
    
    # 4. 暴跌时成交量分析（低谷附近3根 vs 之前3根）
    crash_zone_start = max(0, min_low_idx - 1)
    crash_zone_end = min(len(volumes), min_low_idx + 2)
    crash_vol = sum(volumes[crash_zone_start:crash_zone_end]) / max(1, crash_zone_end - crash_zone_start)
    normal_vol = sum(volumes[:max(1, min_low_idx - 2)]) / max(1, min(3, min_low_idx))
    crash_vol_ratio = crash_vol / normal_vol if normal_vol > 0 else 1
    
    # 5. 收回速度（从低谷到当前位置用了几根K线）
    bars_since_low = len(bars) - 1 - min_low_idx
    speed_score = max(0, 10 - bars_since_low)  # 越快越强，1根=10分，6根=4分
    
    # 6. 综合评分
    score = 0
    
    # 跌幅越大越有意义
    if drop_pct > 8:
        score += 8
    elif drop_pct > 5:
        score += 6
    else:
        score += 4
    
    # 收回力度
    if recovery_pct > 80:
        score += 8
    elif recovery_pct > 60:
        score += 5
    else:
        score += 3
    
    # 暴跌放量 = 主力吸筹信号
    if crash_vol_ratio > 2:
        score += 5
    elif crash_vol_ratio > 1.5:
        score += 3
    
    # 收回速度
    score += min(4, speed_score // 2)
    
    score = min(25, score)
    
    # 构建描述
    details_parts = []
    details_parts.append(f'V型反转: 跌{drop_pct:.1f}%→收回{recovery_pct:.0f}%')
    if crash_vol_ratio > 1.5:
        details_parts.append(f'暴跌放量{crash_vol_ratio:.1f}x(主力吸筹)')
    if bars_since_low <= 3:
        details_parts.append(f'{bars_since_low}根K线快速收回')
    
    return True, score, ' | '.join(details_parts)


def assess_short_squeeze_quality(klines_5m, oi_hist_5m, ls_ratio):
    """
    空头回补质量评估（升级版）
    
    原版问题：空头回补固定给8分，没有区分质量
    新版：根据收回力度、速度、量能动态评分
    
    返回:
        score: 0-20
        quality_level: 'weak' | 'moderate' | 'strong'
        details: str
    """
    if not klines_5m or len(klines_5m) < 12:
        return 0, 'weak', ''
    
    # 最近12根5min K线（1小时）
    recent = klines_5m[-12:]
    closes = [k['close'] for k in recent]
    volumes = [k['quote_volume'] for k in recent]
    
    # 找到最近1小时的最低点和当前价格
    min_price = min(closes)
    current = closes[-1]
    max_price = max(closes)
    
    if min_price == 0:
        return 0, 'weak', ''
    
    # 收回幅度
    range_size = max_price - min_price
    recovery = (current - min_price) / range_size * 100 if range_size > 0 else 0
    
    # OI下降速率（空头平仓速度）
    oi_drop_rate = 0
    if oi_hist_5m and len(oi_hist_5m) >= 12:
        oi_recent = [float(o['sumOpenInterest']) for o in oi_hist_5m[-12:]]
        oi_start = oi_recent[0]
        oi_end = oi_recent[-1]
        if oi_start > 0:
            oi_drop_rate = (oi_start - oi_end) / oi_start * 100
    
    # 量能变化（收回时 vs 下跌时）
    mid = len(volumes) // 2
    vol_down = sum(volumes[:mid]) / max(1, mid)
    vol_up = sum(volumes[mid:]) / max(1, len(volumes) - mid)
    vol_recovery_ratio = vol_up / vol_down if vol_down > 0 else 1
    
    # 综合评分
    score = 0
    details = []
    
    # 收回力度
    if recovery > 80:
        score += 8
        details.append(f'强力收回{recovery:.0f}%')
    elif recovery > 50:
        score += 5
        details.append(f'中等收回{recovery:.0f}%')
    else:
        score += 2
        details.append(f'弱收回{recovery:.0f}%')
    
    # OI下降速度（快速下降 = 空头被逼出局）
    if oi_drop_rate > 5:
        score += 6
        details.append(f'OI急降{oi_drop_rate:.1f}%(空头溃败)')
    elif oi_drop_rate > 2:
        score += 4
        details.append(f'OI缓降{oi_drop_rate:.1f}%')
    
    # 收回时量能
    if vol_recovery_ratio > 1.5:
        score += 4
        details.append(f'收回放量{vol_recovery_ratio:.1f}x')
    elif vol_recovery_ratio > 1:
        score += 2
    
    # 多空比确认
    if ls_ratio and len(ls_ratio) >= 6:
        recent_ls = float(ls_ratio[-1].get('longShortRatio', 1))
        if recent_ls < 0.6:
            score += 2
            details.append(f'多空比{recent_ls:.2f}空头拥挤')
    
    score = min(20, score)
    
    if score >= 15:
        quality = 'strong'
    elif score >= 8:
        quality = 'moderate'
    else:
        quality = 'weak'
    
    return score, quality, ' | '.join(details)


def get_4h_trend_context(klines_1h):
    """
    4小时趋势上下文判断
    
    解决核心问题：30min窗口不考虑4h趋势，高位反弹误判为early_rally
    
    返回:
        trend: 'strong_up' | 'up' | 'sideways' | 'down' | 'strong_down'
        price_change_4h: float (%)
        is_downtrend: bool (是否处于下行趋势)
    """
    if not klines_1h or len(klines_1h) < 4:
        return 'sideways', 0, False
    
    # 最近4根1h K线 = 4小时
    recent_4h = klines_1h[-4:]
    closes = [k['close'] for k in recent_4h]
    
    if not closes or closes[0] == 0:
        return 'sideways', 0, False
    
    change = (closes[-1] - closes[0]) / closes[0] * 100
    
    # 用EMA判断趋势方向（简单版：比较近期均值和远期均值）
    early_avg = sum(closes[:2]) / 2
    late_avg = sum(closes[2:]) / 2
    ema_direction = (late_avg - early_avg) / early_avg * 100
    
    if change > 5 and ema_direction > 2:
        return 'strong_up', change, False
    elif change > 2:
        return 'up', change, False
    elif change < -5 and ema_direction < -2:
        return 'strong_down', change, True
    elif change < -2:
        return 'down', change, True
    else:
        return 'sideways', change, False


# ==================== 技术指标函数 v2.1 ====================

def calc_bollinger(closes, period=20, std_dev=2):
    """
    布林带计算
    
    返回: (upper, middle, lower, bandwidth, price_position)
        bandwidth: (upper-lower)/middle 带宽（衡量波动率）
        price_position: 价格在布林带中的位置 0=下轨 0.5=中轨 1=上轨
    """
    if len(closes) < period:
        return None, None, None, None, None
    
    recent = closes[-period:]
    middle = sum(recent) / period
    variance = sum((x - middle) ** 2 for x in recent) / period
    std = variance ** 0.5
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    
    bandwidth = (upper - lower) / middle if middle > 0 else 0
    price = closes[-1]
    price_pos = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    
    return upper, middle, lower, bandwidth, price_pos


def calc_ema(closes, period=20):
    """EMA计算"""
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def analyze_volume_quality(klines, lookback=12):
    """
    成交量质量分析（参考AI500的放量突破/缩量回调判断）
    
    返回:
        quality: 'healthy_breakout' | 'healthy_pullback' | 'unhealthy_divergence' | 'normal'
        vol_trend: 'expanding' | 'contracting' | 'stable'
        details: str
    """
    if not klines or len(klines) < lookback:
        return 'normal', 'stable', ''
    
    bars = klines[-lookback:]
    volumes = [b['quote_volume'] for b in bars]
    closes = [b['close'] for b in bars]
    
    mid = len(volumes) // 2
    vol_first_half = sum(volumes[:mid]) / mid
    vol_second_half = sum(volumes[mid:]) / (len(volumes) - mid)
    vol_change = vol_second_half / vol_first_half if vol_first_half > 0 else 1
    
    price_first = closes[mid - 1] if mid > 0 else closes[0]
    price_last = closes[-1]
    price_up = price_last > price_first
    
    # 判断成交量趋势
    if vol_change > 1.5:
        vol_trend = 'expanding'
    elif vol_change < 0.7:
        vol_trend = 'contracting'
    else:
        vol_trend = 'stable'
    
    # 量价关系判断
    if price_up and vol_change > 1.3:
        return 'healthy_breakout', vol_trend, f'放量上涨(量能{vol_change:.1f}x)多头强势'
    elif price_up and vol_change < 0.8:
        return 'healthy_pullback', vol_trend, f'缩量回调(量能{vol_change:.1f}x)抛压轻'
    elif not price_up and vol_change > 1.3:
        return 'unhealthy_divergence', vol_trend, f'放量下跌(量能{vol_change:.1f}x)空头强势'
    elif not price_up and vol_change < 0.8:
        return 'normal', vol_trend, f'缩量下跌(量能{vol_change:.1f}x)动能衰减'
    
    return 'normal', vol_trend, f'量价平稳(量能{vol_change:.1f}x)'


def find_support_resistance(klines, lookback=48):
    """
    支撑阻力位识别（从价格结构中提取）
    
    返回:
        resistance: 最近的阻力位
        support: 最近的支撑位
        box_high: 箱体上沿（如果在震荡区间内）
        box_low: 箱体下沿
        details: str
    """
    if not klines or len(klines) < 8:
        return None, None, None, None, ''
    
    bars = klines[-lookback:]
    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    closes = [b['close'] for b in bars]
    current = closes[-1]
    
    # 找局部高点和低点（前后各3根K线比较）
    resistance_levels = []
    support_levels = []
    for i in range(3, len(bars) - 3):
        if highs[i] == max(highs[i-3:i+4]):
            resistance_levels.append(highs[i])
        if lows[i] == min(lows[i-3:i+4]):
            support_levels.append(lows[i])
    
    # 取最近的高于当前价的阻力位
    resistance = min([r for r in resistance_levels if r > current], default=None)
    # 取最近的低于当前价的支撑位
    support = max([s for s in support_levels if s < current], default=None)
    
    # 箱体识别：最近24根K线的高低点
    recent = bars[-24:]
    box_high = max(b['high'] for b in recent)
    box_low = min(b['low'] for b in recent)
    box_range = (box_high - box_low) / box_low * 100 if box_low > 0 else 0
    
    # 只有在区间<10%时才算箱体震荡
    if box_range > 10:
        box_high, box_low = None, None
    
    details_parts = []
    if resistance:
        dist = (resistance - current) / current * 100
        details_parts.append(f'阻力{resistance:.4f}(+{dist:.1f}%)')
    if support:
        dist = (current - support) / current * 100
        details_parts.append(f'支撑{support:.4f}(-{dist:.1f}%)')
    if box_high and box_low:
        details_parts.append(f'箱体{box_low:.4f}-{box_high:.4f}')
    
    return resistance, support, box_high, box_low, ' | '.join(details_parts)


def compute_technical_context(klines_15m, klines_1h):
    """
    综合技术指标上下文（布林带+EMA+成交量质量+支撑阻力）
    
    返回字典包含所有技术指标数据
    """
    ctx = {}
    
    # --- 布林带 (15min, 20周期) ---
    if klines_15m and len(klines_15m) >= 20:
        closes_15m = [k['close'] for k in klines_15m]
        bb_upper, bb_middle, bb_lower, bb_bw, bb_pos = calc_bollinger(closes_15m)
        if bb_upper:
            ctx['bb_upper'] = bb_upper
            ctx['bb_middle'] = bb_middle
            ctx['bb_lower'] = bb_lower
            ctx['bb_bandwidth'] = bb_bw
            ctx['bb_position'] = bb_pos
            # 布林带信号
            if bb_pos and bb_pos < 0.1:
                ctx['bb_signal'] = 'oversold'  # 接近下轨
            elif bb_pos and bb_pos > 0.9:
                ctx['bb_signal'] = 'overbought'  # 接近上轨
            else:
                ctx['bb_signal'] = 'neutral'
    
    # --- EMA (15min, 20周期) ---
    if klines_15m and len(klines_15m) >= 20:
        closes_15m = [k['close'] for k in klines_15m]
        ema20 = calc_ema(closes_15m, 20)
        if ema20:
            ctx['ema20_15m'] = ema20
            current = closes_15m[-1]
            ctx['price_vs_ema20'] = 'above' if current > ema20 else 'below'
            ctx['ema20_dist_pct'] = (current - ema20) / ema20 * 100
    
    # --- 成交量质量 (15min, 12根) ---
    vol_quality, vol_trend, vol_details = analyze_volume_quality(klines_15m, 12)
    ctx['volume_quality'] = vol_quality
    ctx['volume_trend'] = vol_trend
    ctx['volume_details'] = vol_details
    
    # --- 支撑阻力 ---
    res, sup, box_h, box_l, sr_details = find_support_resistance(klines_15m)
    ctx['resistance'] = res
    ctx['support'] = sup
    ctx['box_high'] = box_h
    ctx['box_low'] = box_l
    ctx['sr_details'] = sr_details
    
    return ctx


def analyze_oi_price_phase(symbol, api_key=None, api_secret=None):
    """
    核心分析：OI-价格相位分析（v2.0 - 含V型反转+趋势上下文）
    
    返回:
    {
        'phase': 'early_rally' | 'late_rally' | 'distribution' | 'early_decline' | 'late_decline' | 'accumulation' | 'v_reversal' | 'bear_market_rally' | 'short_squeeze',
        'quality_score': 0-50,  # 资金流质量评分（含V型反转加分）
        'signal_direction': 'long' | 'short' | 'neutral',
        'oi_trend': 'rising' | 'falling' | 'flat',
        'price_trend': 'up' | 'down' | 'flat',
        'trend_context': 'strong_up' | 'up' | 'sideways' | 'down' | 'strong_down',
        'v_reversal': bool,  # 是否检测到V型反转
        'details': str,  # 人类可读分析
        'raw': {...}  # 原始数据
    }
    """
    result = {
        'phase': 'unknown',
        'quality_score': 0,
        'signal_direction': 'neutral',
        'oi_trend': 'flat',
        'price_trend': 'flat',
        'trend_context': 'sideways',
        'v_reversal': False,
        'details': '',
        'raw': {}
    }
    
    # 1. 获取多周期数据（并发）- v2.0: 新增15min和1h数据
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(fetch_oi_history, symbol, '5m', 48): 'oi_hist',
            executor.submit(fetch_klines, symbol, '5m', 48): 'klines',
            executor.submit(fetch_klines_15m, symbol, 64): 'klines_15m',
            executor.submit(fetch_klines_1h, symbol, 24): 'klines_1h',
            executor.submit(fetch_long_short_ratio, symbol, '5m', 48): 'ls_ratio',
            executor.submit(fetch_funding_rate, symbol): 'funding',
        }
        results = {}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception:
                results[key] = None
        oi_hist = results.get('oi_hist') or []
        klines = results.get('klines') or []
        klines_15m = results.get('klines_15m') or []
        klines_1h = results.get('klines_1h') or []
        ls_ratio = results.get('ls_ratio') or []
        funding = results.get('funding') or 0
    
    if len(oi_hist) < 6 or len(klines) < 6:
        result['details'] = '数据不足'
        return result
    
    # 2. 4小时趋势上下文（v2.0核心改进）
    trend_context, price_change_4h, is_downtrend = get_4h_trend_context(klines_1h)
    result['trend_context'] = trend_context
    result['raw']['price_change_4h'] = price_change_4h
    
    # 3. 计算多周期变化
    # 最近30分钟（6根5min）
    recent_oi = [float(o['sumOpenInterest']) for o in oi_hist[-6:]]
    recent_prices = [k['close'] for k in klines[-6:]]
    recent_volumes = [k['quote_volume'] for k in klines[-6:]]
    
    # 最近2小时（24根5min）
    medium_oi = [float(o['sumOpenInterest']) for o in oi_hist[-24:]]
    medium_prices = [k['close'] for k in klines[-24:]]
    
    # 最近4小时（全部）
    full_oi = [float(o['sumOpenInterest']) for o in oi_hist]
    full_prices = [k['close'] for k in klines]
    
    # 3. 计算变化率
    def pct_change(arr):
        if not arr or arr[0] == 0:
            return 0
        return (arr[-1] - arr[0]) / arr[0] * 100
    
    oi_30m = pct_change(recent_oi)
    price_30m = pct_change(recent_prices)
    oi_2h = pct_change(medium_oi)
    price_2h = pct_change(medium_prices)
    oi_4h = pct_change(full_oi)
    price_4h = pct_change(full_prices)
    
    # 成交量趋势（最近30min vs 之前）
    vol_recent = sum(recent_volumes[-3:]) / 3 if len(recent_volumes) >= 3 else 0
    vol_earlier = sum(recent_volumes[:3]) / 3 if len(recent_volumes) >= 3 else 1
    vol_ratio = vol_recent / vol_earlier if vol_earlier > 0 else 1
    
    result['raw'] = {
        'oi_30m': oi_30m, 'price_30m': price_30m,
        'oi_2h': oi_2h, 'price_2h': price_2h,
        'oi_4h': oi_4h, 'price_4h': price_4h,
        'vol_ratio': vol_ratio,
        'funding': funding,
        'trend_context': trend_context,
        'price_change_4h': price_change_4h,
    }
    
    # 4. 判断OI和价格趋势
    if oi_30m > 1.5:
        result['oi_trend'] = 'rising'
    elif oi_30m < -1.5:
        result['oi_trend'] = 'falling'
    
    if price_30m > 0.5:
        result['price_trend'] = 'up'
    elif price_30m < -0.5:
        result['price_trend'] = 'down'
    
    # 5. 核心：OI-价格相位判断（操盘手视角 v2.0）
    oi_up = oi_30m > 1.0
    oi_down = oi_30m < -1.0
    price_up = price_30m > 0.3
    price_down = price_30m < -0.3
    
    quality = 0
    phase = 'unknown'
    direction = 'neutral'
    details = []
    
    # 5.0 先检测V型反转（独立于OI-价格矩阵，可叠加加分）
    v_detected, v_score, v_details = detect_v_reversal(klines_15m, klines, vol_ratio)
    result['v_reversal'] = v_detected
    result['raw']['v_reversal_score'] = v_score
    if v_detected:
        details.append(f'🔥 {v_details}')
    
    # ===== 做多信号分析 =====
    if price_up and oi_up:
        # v2.0关键改进：检查4h趋势上下文
        if is_downtrend:
            # 4h下行趋势中的短期反弹 = 熊市反弹（死猫跳），不是early_rally
            phase = 'bear_market_rally'
            direction = 'short'  # 反而是做空机会
            quality = 10
            details.append(f'⚠️ 熊市反弹: 30m涨{price_30m:.1f}%但4h跌{price_change_4h:.1f}%')
            details.append('下行趋势中的反弹，大概率是死猫跳')
            # 如果有V型反转信号，稍微给多头一点机会
            if v_detected and v_score > 15:
                quality = 15
                direction = 'neutral'
                details.append('但V型反转信号较强，观望为主')
        else:
            # 正常的早期拉升
            phase = 'early_rally'
            direction = 'long'
            
            if oi_30m > 3 and price_30m > 1:
                quality = 30
                details.append(f'🚀 早期强势拉升: OI+{oi_30m:.1f}% 价格+{price_30m:.1f}%')
            elif oi_30m > 1.5:
                quality = 25
                details.append(f'📈 健康拉升: OI+{oi_30m:.1f}% 价格+{price_30m:.1f}%')
            else:
                quality = 20
                details.append(f'📊 温和上涨: OI+{oi_30m:.1f}% 价格+{price_30m:.1f}%')
            
            if vol_ratio > 2:
                quality = min(30, quality + 5)
                details.append(f'量比{vol_ratio:.1f}放量确认')
            
            if price_2h > 2 and oi_2h > 3:
                quality = min(30, quality + 3)
                details.append('2h持续性良好')
            
            if funding > 0.003:
                quality = max(0, quality - 5)
                details.append(f'⚠️ 费率{funding*100:.2f}%偏高')
            
            # V型反转额外加分（上不封50）
            if v_detected:
                quality = min(50, quality + v_score)
    
    elif price_up and oi_down:
        # v2.0：空头回补质量升级
        squeeze_score, squeeze_quality, squeeze_details = assess_short_squeeze_quality(
            klines, oi_hist, ls_ratio
        )
        
        if squeeze_quality == 'strong':
            # 强力空头回补 = 有质量的反弹
            phase = 'short_squeeze'
            direction = 'long'
            quality = squeeze_score + 5  # 15-25分
            details.append(f'💪 强力空头回补: {squeeze_details}')
            details.append('OI急降+强力收回，不是弱反弹')
        elif squeeze_quality == 'moderate':
            phase = 'short_covering'
            direction = 'long'
            quality = squeeze_score  # 8-14分
            details.append(f'📊 中等空头回补: {squeeze_details}')
        else:
            phase = 'short_covering'
            direction = 'long'
            quality = squeeze_score  # 2-7分
            details.append(f'⚠️ 弱空头回补: OI{oi_30m:.1f}% 价格+{price_30m:.1f}%')
            details.append('非新资金进场，持续性存疑')
        
        # V型反转叠加（空头回补+V型=很强的信号）
        if v_detected and squeeze_quality != 'weak':
            quality = min(50, quality + v_score)
            direction = 'long'
    
    # ===== 做空信号分析 =====
    elif price_down and oi_up:
        # v3.2: 先检查4h趋势，如果4h强势上涨，30分钟回调中的OI增加应视为回调中的新资金进场
        if price_4h > 10 and trend_context == 'strong_up':
            # 4h大涨+30分钟回调+OI增 = 新资金在回调中抄底，不是做空
            phase = 'pullback_buying'
            direction = 'long'
            quality = 20
            details.append(f'📈 回调中的新资金进场: OI{oi_30m:+.1f}% 价格{price_30m:+.1f}% 但4h涨{price_4h:+.1f}%')
            details.append('4h强势上涨中，30分钟回调的OI增加是新资金抄底')
            if oi_2h > 10:
                quality = min(30, quality + 5)
                details.append(f'2h OI+{oi_2h:.1f}% 资金持续流入')
        elif price_4h > 5 and oi_2h > 15:
            # 4h上涨+2h OI大幅增加 = 趋势中的回调
            phase = 'pullback_buying'
            direction = 'long'
            quality = 15
            details.append(f'📊 趋势回调: OI{oi_30m:+.1f}% 价格{price_30m:+.1f}% 2h OI+{oi_2h:.1f}%')
        else:
            # 新空头主动压盘，有质量的下跌
            phase = 'early_decline'
            direction = 'short'
            
            if oi_30m > 3 and price_30m < -1:
                quality = 30  # 强势做空
                details.append(f'🔴 新空头强势压盘: OI+{oi_30m:.1f}% 价格{price_30m:.1f}%')
            elif oi_30m > 1.5:
                quality = 25
                details.append(f'📉 新空头进场: OI+{oi_30m:.1f}% 价格{price_30m:.1f}%')
            else:
                quality = 20
                details.append(f'📊 空头试探: OI+{oi_30m:.1f}% 价格{price_30m:.1f}%')
        
        # 加分：费率正（做多拥挤，利于做空）
        if funding > 0.001:
            quality = min(30, quality + 5)
            details.append(f'正费率{funding*100:.3f}%做多拥挤')
        
        # 加分：成交量放大
        if vol_ratio > 2:
            quality = min(30, quality + 3)
            details.append('放量下杀')
        
        # 减分：如果已经跌很多了（追空风险）
        if price_4h < -15:
            quality = max(0, quality - 8)
            details.append(f'⚠️ 4h已跌{price_4h:.0f}%，追空风险')
    
    elif price_down and oi_down:
        # 多头爆仓/出清，反抽风险高
        # 不是真正的做空力量，降权
        phase = 'liquidation'
        direction = 'short'
        quality = 5
        details.append(f'⚠️ 多头出清: OI{oi_30m:.1f}% 价格{price_30m:.1f}%')
        details.append('爆仓驱动非新空头，反抽风险高')
        
        # 如果出清接近尾声，可能是底部
        if oi_4h < -10 and oi_30m > -2:
            quality = 3
            direction = 'neutral'
            details.append('出清接近尾声，可能反弹')
    
    # ===== 盘整/震荡 =====
    elif abs(price_30m) < 0.5 and abs(oi_30m) < 2:
        phase = 'consolidation'
        direction = 'neutral'
        quality = 0
        details.append(f'盘整中: OI{oi_30m:+.1f}% 价格{price_30m:+.1f}%')
        
        # v2.0: 盘整中看4h趋势方向（不只是2h）
        if trend_context in ('up', 'strong_up') and oi_2h > 0:
            quality = 12
            direction = 'long'
            details.append(f'4h趋势{trend_context}偏多，盘整蓄力后可能继续')
        elif trend_context in ('down', 'strong_down') and oi_2h > 0:
            quality = 12
            direction = 'short'
            details.append(f'4h趋势{trend_context}偏空，盘整后可能继续下破')
        elif price_2h > 3 and oi_2h > 0:
            quality = 10
            direction = 'long'
            details.append('2h趋势偏多，盘整后可能继续')
        elif price_2h < -3 and oi_2h > 0:
            quality = 10
            direction = 'short'
            details.append('2h趋势偏空，盘整后可能继续')
    
    else:
        # 其他情况
        phase = 'mixed'
        quality = 5
        details.append(f'信号混合: OI{oi_30m:+.1f}% 价格{price_30m:+.1f}%')
    
    # 6. 多空比辅助判断
    if ls_ratio and len(ls_ratio) >= 6:
        recent_ls = float(ls_ratio[-1].get('longShortRatio', 1))
        result['raw']['ls_ratio'] = recent_ls
        
        # 多头极度拥挤 → 做空加分
        if recent_ls > 2 and direction == 'short':
            quality = min(50, quality + 5)
            details.append(f'多空比{recent_ls:.2f}多头拥挤')
        # 空头极度拥挤 → 做多加分
        elif recent_ls < 0.5 and direction == 'long':
            quality = min(50, quality + 5)
            details.append(f'多空比{recent_ls:.2f}空头拥挤')
    
    result['phase'] = phase
    result['quality_score'] = quality
    result['signal_direction'] = direction
    result['details'] = ' | '.join(details) if details else '无明显信号'
    
    return result


def get_oi_quality_score(symbol, expected_direction, api_key=None, api_secret=None):
    """
    给评分系统调用的接口（v2.0）
    
    参数:
        symbol: 交易对
        expected_direction: 'long' 或 'short'（扫描器预判的方向）
    
    返回:
        (score_bonus, reason) - 额外加分和原因
    """
    analysis = analyze_oi_price_phase(symbol, api_key, api_secret)
    
    score = 0
    reasons = []
    
    # v2.0: bear_market_rally 视为做空信号
    effective_direction = analysis['signal_direction']
    if analysis['phase'] == 'bear_market_rally':
        effective_direction = 'short'
    
    # 方向一致才加分
    if effective_direction == expected_direction:
        score = analysis['quality_score']
        reasons.append(analysis['details'])
    elif effective_direction == 'neutral':
        # 中性不加分也不扣分
        pass
    else:
        # 方向相反，扣分（可能是假信号）
        score = -5
        reasons.append(f'⚠️ OI信号方向相反: {analysis["details"]}')
    
    return score, reasons, analysis


def format_oi_analysis(analysis):
    """格式化OI分析为人类可读报告（v2.0）"""
    lines = []
    lines.append(f'阶段: {analysis["phase"]}')
    lines.append(f'方向: {analysis["signal_direction"]}')
    lines.append(f'质量: {analysis["quality_score"]}/50')
    lines.append(f'4h趋势: {analysis.get("trend_context", "unknown")}')
    if analysis.get('v_reversal'):
        lines.append('🔥 检测到V型反转')
    lines.append(f'详情: {analysis["details"]}')
    
    raw = analysis.get('raw', {})
    if raw:
        lines.append(f'数据: OI_30m={raw.get("oi_30m", 0):+.2f}% '
                     f'价格_30m={raw.get("price_30m", 0):+.2f}% '
                     f'量比={raw.get("vol_ratio", 1):.1f} '
                     f'4h涨跌={raw.get("price_change_4h", 0):+.1f}%')
    
    return '\n'.join(lines)


if __name__ == '__main__':
    # 测试
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    print(f'分析 {symbol}...')
    result = analyze_oi_price_phase(symbol)
    print(format_oi_analysis(result))
