"""
做空模式评分逻辑 v3.0
基于 RIVER/RAVE/H/SAHARA/OPN 复盘优化
v3.0新增: MACD/布林带/EMA多周期/量价模式/支撑阻力/箱体 全面集成
四种做空模式: 暴涨见顶/趋势转弱/OI背离/恐慌抛售
"""

# ============================================================
# 四种做空模式定义
# ============================================================
SHORT_PATTERNS = {
    "surge_top": {  # 暴涨后见顶
        "name": "暴涨后见顶",
        "conditions": {
            "price_change_24h": {"min": 50, "max": None},  # 24h涨幅>50%
            "oi_change_24h": {"min": 20, "max": None},     # OI暴增>20%
            "rsi": {"min": 70, "max": None},               # RSI>70
        },
        "weight": 1.3,  # 权重加成
        "stop_loss_pct": 0.05,  # 止损5%
    },
    "trend_weak": {  # 趋势转弱
        "name": "趋势转弱",
        "conditions": {
            "ema20_below": True,           # 价格在EMA20下方
            "consecutive_red": {"min": 2}, # 连续阴线>=2
            "volume_ratio": {"min": 1.5},  # 成交量放大
        },
        "weight": 1.2,
        "stop_loss_pct": 0.06,
    },
    "oi_diverge": {  # OI背离
        "name": "OI背离",
        "conditions": {
            "price_change_24h": {"min": None, "max": 0},   # 价格下跌
            "oi_change_24h": {"min": 10, "max": None},     # OI增加>10%
        },
        "weight": 1.25,
        "stop_loss_pct": 0.06,
    },
    "panic_sell": {  # 恐慌性抛售
        "name": "恐慌性抛售",
        "conditions": {
            "price_change_4h": {"min": None, "max": -20},  # 4h跌幅>20%
            "volume_ratio": {"min": 3},                     # 成交量>3倍
            "funding_rate": {"min": None, "max": -0.001},   # 负费率
        },
        "weight": 1.1,  # 风险较高，权重较低
        "stop_loss_pct": 0.08,
    },
}


def calc_short_score_v2(symbol: str, klines: dict, funding: dict, oi_data: dict,
                        ls_ratio: dict, taker: dict, ema_data: dict = None) -> tuple:
    """
    计算做空评分 v3.0
    
    返回: (score, details, reasons, pattern, stop_loss_pct)
    """
    score = 0
    details = {}
    reasons = []
    
    # ============================================================
    # 维度1: 价格弱势 (20分)
    # ============================================================
    price_change_24h = klines.get("price_change_24h", 0)
    price_change_4h = klines.get("price_change_4h", 0)
    current_price = klines.get("close", 0)
    
    # 24h跌幅评分
    if price_change_24h < -10:
        score += 20
        reasons.append(f"24h暴跌{price_change_24h:.1f}%")
    elif price_change_24h < -5:
        score += 16
        reasons.append(f"24h跌{price_change_24h:.1f}%")
    elif price_change_24h < -2:
        score += 12
        reasons.append(f"24h跌{price_change_24h:.1f}%")
    elif price_change_24h < 0:
        score += 8
    
    # 暴涨评分（涨幅过大也是弱势信号）— v3.1: 需要确认信号才给高分
    # 没有确认信号的裸暴涨只给基础分，避免和做多同时触发
    surge_confirmed = False
    surge_confirm_reasons = []
    
    # 检查确认信号
    macd_cross = klines.get("macd_cross", "none")
    macd_trend = klines.get("macd_trend", "neutral")
    vol_pattern = klines.get("vol_pattern", "normal")
    vol_divergence = klines.get("vol_divergence", "none")
    oi_change = oi_data.get("oi_change_24h", 0) if oi_data else 0
    
    if macd_cross == "death_cross":
        surge_confirmed = True
        surge_confirm_reasons.append("MACD死叉")
    if vol_divergence == "bearish_top":
        surge_confirmed = True
        surge_confirm_reasons.append("量价顶背离")
    if price_change_4h < -5:
        surge_confirmed = True
        surge_confirm_reasons.append(f"1h跌{price_change_4h:.1f}%")
    if oi_change < -10:
        surge_confirmed = True
        surge_confirm_reasons.append(f"OI减{oi_change:.0f}%")
    
    if price_change_24h > 100:
        if surge_confirmed:
            score += 20
            reasons.append(f"24h暴涨{price_change_24h:.1f}% + {'|'.join(surge_confirm_reasons)}")
        else:
            score += 5  # 无确认信号，只给基础分
            reasons.append(f"24h暴涨{price_change_24h:.1f}% 但无见顶确认")
    elif price_change_24h > 50:
        if surge_confirmed:
            score += 16
            reasons.append(f"24h大涨{price_change_24h:.1f}% + {'|'.join(surge_confirm_reasons)}")
        else:
            score += 5
            reasons.append(f"24h大涨{price_change_24h:.1f}% 无确认")
    elif price_change_24h > 20:
        if surge_confirmed:
            score += 10
            reasons.append(f"24h涨{price_change_24h:.1f}% + 确认")
        else:
            score += 4
    elif price_change_24h > 10:
        score += 2
    
    # 4h跌幅评分
    if price_change_4h < -15:
        score += 4  # 额外加分
        reasons.append(f"4h暴跌{price_change_4h:.1f}%")
    elif price_change_4h < -10:
        score += 2
    # 4h暴涨评分
    elif price_change_4h > 30:
        score += 4
        reasons.append(f"4h暴涨{price_change_4h:.1f}%⚠️")
    elif price_change_4h > 15:
        score += 2
    
    details["price_change_24h"] = price_change_24h
    details["price_change_4h"] = price_change_4h
    
    # ============================================================
    # 维度2: EMA趋势 (12分) — v3.0升级为多周期
    # ============================================================
    ema_trend = klines.get("ema_trend", "neutral")
    ema_align = klines.get("ema_align", "")
    ema_spread = klines.get("ema_spread", 0)
    ema_slope = klines.get("ema_slope", 0)
    ema20 = klines.get("ema20", 0) or (ema_data.get("ema20", 0) if ema_data else 0)
    ema50 = klines.get("ema50", 0) or (ema_data.get("ema50", 0) if ema_data else 0)
    
    ema20_below = current_price < ema20 if ema20 > 0 else False
    ema50_below = current_price < ema50 if ema50 > 0 else False
    
    # 多周期EMA排列评分
    if ema_trend == "bearish":
        score += 12
        reasons.append(f"EMA空头排列 离散{ema_spread:+.1f}%")
    elif ema_trend == "weakening":
        score += 8
        reasons.append("EMA死叉形成中")
    elif ema_trend == "neutral":
        score += 3
    elif ema_trend == "recovering":
        score -= 3
        reasons.append("EMA金叉形成中⚠️")
    elif ema_trend == "bullish":
        score -= 5
        reasons.append("EMA多头排列⚠️")
    
    # EMA斜率
    if ema_slope < -0.15:
        score += 4
        reasons.append(f"EMA21强势下行 {ema_slope:+.2f}%")
    elif ema_slope > 0.15:
        score -= 3
        reasons.append(f"EMA21上行 {ema_slope:+.2f}%⚠️")
    
    # 传统EMA位置
    if ema20_below and ema50_below:
        score += 4
        reasons.append("价格<EMA20+50")
        details["ema_status"] = "双线下方"
    elif ema20_below:
        score += 2
        reasons.append("价格<EMA20")
        details["ema_status"] = "EMA20下方"
    else:
        details["ema_status"] = "上方"
    
    details["ema20_below"] = ema20_below
    details["ema50_below"] = ema50_below
    details["ema_trend"] = ema_trend
    details["ema_align"] = ema_align
    details["ema_spread"] = ema_spread
    details["ema_slope"] = ema_slope
    
    # v3.1 趋势确认组合加分
    trend_bonus = 0
    trend_signals = []
    if ema_trend == "bearish" and macd_trend == "bearish_zone":
        trend_bonus += 5
        trend_signals.append("EMA空头+MACD零轴下")
    if ema_trend == "bearish" and klines.get("bb_pct_b", 0.5) < 0.3:
        trend_bonus += 5
        trend_signals.append("EMA空头+布林下方")
    if ema_trend == "bearish" and klines.get("consecutive_red", 0) >= 3:
        trend_bonus += 5
        trend_signals.append("EMA空头+连续阴线")
    if ema_trend == "bearish" and macd_cross == "death_cross":
        trend_bonus += 8
        trend_signals.append("EMA空头+MACD死叉")
    if trend_bonus > 0:
        score += trend_bonus
        reasons.append(f"趋势共振+{trend_bonus} {'|'.join(trend_signals)}")
    
    # 连续阴线统计
    consecutive_red = klines.get("consecutive_red", 0)
    details["consecutive_red"] = consecutive_red
    
    # ============================================================
    # 维度3: OI背离 (18分)
    # ============================================================
    oi_change_24h = oi_data.get("oi_change_24h", 0)
    oi_change_4h = oi_data.get("oi_change_4h", 0)
    
    # 价格跌+OI升 = 新空头开仓（最强信号）
    if price_change_24h < 0 and oi_change_24h > 10:
        score += 18
        reasons.append("价跌OI升=新空开仓")
    elif price_change_24h < 0 and oi_change_24h > 5:
        score += 12
        reasons.append("价跌OI升")
    elif price_change_24h < 0 and oi_change_24h > 0:
        score += 8
    
    # 价格涨+OI暴增 = 见顶信号
    elif price_change_24h > 20 and oi_change_24h > 30:
        score += 16
        reasons.append("价涨OI暴增=见顶")
    elif price_change_24h > 10 and oi_change_24h > 20:
        score += 10
        reasons.append("价涨OI增=可能见顶")
    
    # OI暴增额外加分（无论价格涨跌）
    if oi_change_24h > 50:
        score += 12
        reasons.append(f"OI暴增{oi_change_24h:.0f}%🔥")
    elif oi_change_24h > 30:
        score += 8
        reasons.append(f"OI大增{oi_change_24h:.0f}%")
    elif oi_change_24h > 20:
        score += 4
        reasons.append(f"OI增{oi_change_24h:.0f}%")
    
    # OI下降 = 多头平仓
    elif price_change_24h < 0 and oi_change_24h < -5:
        score += 6
        reasons.append("价跌OI跌=多头平仓")
    
    details["oi_change_24h"] = oi_change_24h
    details["oi_change_4h"] = oi_change_4h
    
    # ============================================================
    # 维度4: 资金费率 (12分)
    # ============================================================
    funding_rate = funding.get("rate", 0)
    
    if funding_rate > 0.003:       # > 0.3% 极高费率
        score += 12
        reasons.append(f"高费率{funding_rate*100:.2f}%")
    elif funding_rate > 0.001:     # > 0.1% 高费率
        score += 10
        reasons.append(f"正费率{funding_rate*100:.2f}%")
    elif funding_rate > 0.0005:    # > 0.05% 中等费率
        score += 6
        reasons.append("正费率")
    elif funding_rate > 0:
        score += 3
    
    # 负费率 = 空头拥挤，可能反弹（风险提示）
    elif funding_rate < -0.001:    # < -0.1% 极端负费率
        score -= 4  # 扣分
        reasons.append("⚠️负费率过高")
    
    details["funding_rate"] = funding_rate
    
    # ============================================================
    # 维度5: 多头拥挤 (12分)
    # ============================================================
    long_ratio = ls_ratio.get("long_ratio", 50)
    rsi = klines.get("rsi", 50)
    
    # 多空比评分
    if long_ratio > 70:
        score += 12
        reasons.append("多头极度拥挤")
    elif long_ratio > 65:
        score += 10
        reasons.append("多头拥挤")
    elif long_ratio > 60:
        score += 6
        reasons.append("多头偏多")
    elif long_ratio > 55:
        score += 3
    
    # RSI评分
    if rsi > 80:
        score += 5  # 额外加分
        reasons.append(f"RSI{rsi:.0f}极度超买")
    elif rsi > 70:
        score += 3
        reasons.append(f"RSI{rsi:.0f}超买")
    elif rsi < 30:
        score -= 3  # 超卖可能反弹
        reasons.append("RSI超卖⚠️")
    
    details["long_ratio"] = long_ratio
    details["rsi"] = rsi
    
    # ============================================================
    # 维度6: 成交量确认 (12分)
    # ============================================================
    volume_ratio = taker.get("volume_ratio", 1)
    taker_sell_ratio = taker.get("taker_sell_ratio", 50)
    
    if volume_ratio > 5:
        score += 12
        reasons.append(f"量比{volume_ratio:.1f}极度恐慌🔥")
    elif volume_ratio > 3:
        score += 8
        reasons.append(f"量比{volume_ratio:.1f}恐慌")
    elif volume_ratio > 2:
        score += 6
        reasons.append(f"量比{volume_ratio:.1f}放量")
    elif volume_ratio > 1.5:
        score += 4
        reasons.append(f"量比{volume_ratio:.1f}")
    elif volume_ratio > 1:
        score += 2
    
    # Taker卖出占比
    if taker_sell_ratio > 60:
        score += 3
        reasons.append("卖盘主导")
    
    details["volume_ratio"] = volume_ratio
    details["taker_sell_ratio"] = taker_sell_ratio
    
    # ============================================================
    # v3.0 新增维度
    # ============================================================
    
    # === 维度7: MACD信号 (10分) ===
    macd_cross = klines.get("macd_cross", "none")
    macd_trend = klines.get("macd_trend", "neutral")
    histogram_action = klines.get("histogram_action", "continuing")
    
    if macd_cross == "death_cross":
        score += 10
        reasons.append("MACD死叉🔥")
    elif macd_cross == "golden_cross":
        score -= 5
        reasons.append("MACD金叉⚠️")
    
    if macd_trend == "bearish_zone":
        score += 4
        reasons.append("MACD零轴下方")
    elif macd_trend == "bullish_zone":
        score -= 2
    
    # 柱状图走平+趋势转换 = 即将死叉 (提前做空)
    if histogram_action == "flattening" and macd_trend in ("turning_bearish", "bullish_zone"):
        score += 3
        reasons.append("MACD柱状图走平 即将死叉")
    
    details["macd_cross"] = macd_cross
    details["macd_trend"] = macd_trend
    details["histogram_action"] = histogram_action
    
    # === 维度8: 布林带位置 (8分) ===
    bb_pct_b = klines.get("bb_pct_b", 0.5)
    bb_squeeze = klines.get("bb_squeeze", False)
    bb_bandwidth = klines.get("bb_bandwidth", 0)
    
    if bb_pct_b > 0.9:
        # 触及上轨 = 超买，做空机会
        score += 8
        reasons.append(f"触及布林上轨 %B={bb_pct_b:.2f} 超买")
    elif bb_pct_b > 0.75:
        score += 5
        reasons.append(f"布林带上方 %B={bb_pct_b:.2f}")
    elif bb_pct_b < 0.15:
        score -= 4
        reasons.append(f"触及布林下轨 %B={bb_pct_b:.2f} 超卖⚠️")
    
    if bb_squeeze:
        score += 3
        reasons.append(f"布林带挤压 bandwidth={bb_bandwidth:.1f}% 即将变盘")
    
    details["bb_pct_b"] = bb_pct_b
    details["bb_squeeze"] = bb_squeeze
    
    # === 维度9: 量价配合模式 (8分) ===
    vol_pattern = klines.get("vol_pattern", "normal")
    vol_health = klines.get("vol_health", "neutral")
    
    if vol_pattern == "vol_divergence_top":
        score += 8
        reasons.append("量价顶背离 做空信号🔥")
    elif vol_pattern == "vol_panic":
        score += 6
        reasons.append("恐慌放量")
    elif vol_pattern == "vol_breakout" and price_change_24h > 30:
        # 暴涨后放量突破 = 可能见顶
        score += 5
        reasons.append("暴涨后放量突破 可能见顶")
    elif vol_pattern == "vol_shrink_pullback":
        score -= 4
        reasons.append("缩量回调=健康调整⚠️")
    elif vol_pattern == "vol_expansion" and price_change_24h > 0:
        score -= 3
        reasons.append("放量上涨⚠️")
    
    details["vol_pattern"] = vol_pattern
    details["vol_health"] = vol_health
    
    # === 维度10: 支撑阻力位置 (8分) ===
    sr_position = klines.get("sr_position", 50)
    sr_support = klines.get("sr_support", 0)
    sr_resistance = klines.get("sr_resistance", 0)
    
    if sr_position > 85:
        score += 8
        reasons.append(f"接近阻力位 位置{sr_position:.0f}% 强阻力")
    elif sr_position > 70:
        score += 5
        reasons.append(f"偏阻力位 位置{sr_position:.0f}%")
    elif sr_position < 15:
        score -= 4
        reasons.append(f"接近支撑位 位置{sr_position:.0f}%⚠️")
    elif sr_position < 30:
        score -= 2
        reasons.append(f"偏支撑位 位置{sr_position:.0f}%")
    
    details["sr_position"] = sr_position
    details["sr_support"] = sr_support
    details["sr_resistance"] = sr_resistance
    
    # === 维度11: 箱体位置 (5分) ===
    box_status = klines.get("box_status", "not_detected")
    box_position = klines.get("box_position", 50)
    
    if box_status == "confirmed":
        if box_position > 80:
            score += 5
            reasons.append(f"箱体上沿附近 位置{box_position:.0f}%")
        elif box_position < 20:
            score -= 3
            reasons.append(f"箱体下沿附近 位置{box_position:.0f}%⚠️")
    elif box_status == "breaking_down":
        score += 5
        reasons.append("箱体向下破位🔥")
    elif box_status == "breaking_up":
        score -= 4
        reasons.append("箱体向上突破⚠️")
    
    details["box_status"] = box_status
    details["box_position"] = box_position
    
    # ============================================================
    # 模式识别
    # ============================================================
    pattern = None
    pattern_weight = 1.0
    stop_loss_pct = 0.06  # 默认止损6%
    
    for pattern_id, pattern_def in SHORT_PATTERNS.items():
        if _check_pattern_conditions(pattern_def["conditions"], details):
            pattern = pattern_id
            pattern_weight = pattern_def["weight"]
            stop_loss_pct = pattern_def["stop_loss_pct"]
            reasons.insert(0, f"🎯{pattern_def['name']}")
            break
    
    # 应用模式权重
    final_score = int(score * pattern_weight)
    final_score = min(100, max(0, final_score))  # 限制在0-100
    
    details["pattern"] = pattern
    details["raw_score"] = score
    details["pattern_weight"] = pattern_weight
    
    # === 复合扣分条件 ===
    # MACD金叉+量价底背离 = 双重看多，做空危险
    if macd_cross == "golden_cross" and vol_pattern == "vol_divergence_bottom":
        final_score = max(0, final_score - 15)
        reasons.append("MACD金叉+底背离 双重看多⚠️")
    
    # 布林带超卖+EMA多头排列 = 强势反弹
    if bb_pct_b < 0.1 and ema_trend == "bullish":
        final_score = max(0, final_score - 10)
        reasons.append("布林超卖+EMA多头 强势反弹⚠️")
    
    return final_score, details, reasons, pattern, stop_loss_pct


def _check_pattern_conditions(conditions: dict, details: dict) -> bool:
    """检查模式条件是否满足"""
    for key, condition in conditions.items():
        if isinstance(condition, bool):
            if details.get(key) != condition:
                return False
        elif isinstance(condition, dict):
            value = details.get(key)
            if value is None:
                return False
            if "min" in condition and condition["min"] is not None:
                if value < condition["min"]:
                    return False
            if "max" in condition and condition["max"] is not None:
                if value > condition["max"]:
                    return False
        elif isinstance(condition, (int, float)):
            if details.get(key) != condition:
                return False
    return True


def get_entry_reasons_v2(details: dict) -> list:
    """获取入场理由 v3.0"""
    reasons = []
    
    # 模式相关
    pattern = details.get("pattern")
    if pattern and pattern in SHORT_PATTERNS:
        reasons.append(SHORT_PATTERNS[pattern]["name"])
    
    # 价格弱势
    if details.get("price_change_24h", 0) < -5:
        reasons.append("弱势下跌")
    elif details.get("price_change_24h", 0) > 20:
        reasons.append("暴涨后")
    
    # EMA趋势
    ema_trend = details.get("ema_trend", "neutral")
    if ema_trend == "bearish":
        reasons.append("EMA空头排列")
    elif ema_trend == "weakening":
        reasons.append("EMA死叉形成中")
    elif details.get("ema20_below"):
        reasons.append("EMA20下方")
    
    # OI信号
    oi_change = details.get("oi_change_24h", 0)
    price_change = details.get("price_change_24h", 0)
    if price_change < 0 and oi_change > 5:
        reasons.append("新空进场")
    elif price_change > 10 and oi_change > 20:
        reasons.append("见顶信号")
    
    # 资金费率
    if details.get("funding_rate", 0) > 0.001:
        reasons.append("高费率")
    
    # 多头拥挤
    if details.get("long_ratio", 50) > 60:
        reasons.append("多头拥挤")
    
    # 成交量
    if details.get("volume_ratio", 1) > 2:
        reasons.append("放量下跌")
    
    # v3.0 新增理由
    macd_cross = details.get("macd_cross", "none")
    if macd_cross == "death_cross":
        reasons.append("MACD死叉")
    
    bb_pct_b = details.get("bb_pct_b", 0.5)
    if bb_pct_b > 0.85:
        reasons.append("布林上轨超买")
    
    vol_pattern = details.get("vol_pattern", "normal")
    if vol_pattern == "vol_divergence_top":
        reasons.append("量价顶背离")
    elif vol_pattern == "vol_panic":
        reasons.append("恐慌放量")
    
    sr_pos = details.get("sr_position", 50)
    if sr_pos > 80:
        reasons.append("接近阻力位")
    
    box_status = details.get("box_status", "")
    if box_status == "breaking_down":
        reasons.append("箱体破位")
    
    return reasons
