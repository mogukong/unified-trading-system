"""
做多模式评分逻辑 v3.0
基于7个妖币(RAVE/BEAT/H/ALLO/SAHARA/RIVER/OPN)回测优化
v3.0新增: MACD/布林带/EMA多周期/量价模式/支撑阻力/箱体 全面集成
核心发现:
- 拉升幅度>500%才值得做多
- OI/价格四象限: short_squeeze 70%胜率, new_capital 75%胜率
- 盈亏比 3:1 是做多护城河
- 评分崩塌(<30)出场100%胜率
"""


# OI/价格四象限模式
LONG_PATTERNS = {
    "new_capital":   {"weight": 1.3, "stop_loss": 0.05, "desc": "新资金进场"},     # OI↑ + 价格↑
    "short_squeeze": {"weight": 1.25, "stop_loss": 0.05, "desc": "空头清算"},     # OI↓ + 价格↑
    "dip_buying":    {"weight": 1.1, "stop_loss": 0.06, "desc": "多头抄底"},      # OI↑ + 价格↓
    "capital_out":   {"weight": 1.0, "stop_loss": 0.07, "desc": "资金撤离上涨"},  # OI↓ + 价格↓(但仍涨)
}


def calc_long_score_v2(symbol: str, klines: dict, funding: dict, oi_data: dict,
                       ls_ratio: dict, taker: dict) -> tuple:
    """
    计算做多评分 v3.0
    返回: (score, details, reasons, pattern, stop_loss_pct)
    """
    score = 0
    details = {}
    reasons = []
    pattern = "none"
    
    # === 1. 价格动量 (20分) ===
    price_change = klines.get("price_change_24h", 0)
    price_change_4h = klines.get("price_change_4h", 0)
    
    if price_change > 10:
        score += 20
        reasons.append(f"24h涨{price_change:.1f}% 强势")
    elif price_change > 5:
        score += 16
        reasons.append(f"24h涨{price_change:.1f}%")
    elif price_change > 2:
        score += 12
        reasons.append(f"24h涨{price_change:.1f}%")
    elif price_change > 0:
        score += 8
    elif price_change > -3:
        score += 4  # 小回调可能是入场机会
    
    details["price_change_24h"] = price_change
    details["price_change_4h"] = price_change_4h
    
    # === 2. OI资金流 (20分) — 核心指标 ===
    oi_change = oi_data.get("oi_change_pct", 0)
    oi_change_4h = oi_data.get("oi_change_4h_pct", 0)
    
    # OI/价格四象限 — 判断资金流类型
    if oi_change > 0 and price_change > 0:
        # 新资金进场 — 最强信号
        if oi_change > 10:
            score += 20
            reasons.append(f"OI+{oi_change:.1f}% 新资金涌入")
        elif oi_change > 5:
            score += 16
            reasons.append(f"OI+{oi_change:.1f}% 新资金进场")
        else:
            score += 12
            reasons.append(f"OI+{oi_change:.1f}% 资金流入")
        pattern = "new_capital"
    elif oi_change < 0 and price_change > 0:
        # 空头清算 — 强势信号
        score += 18
        reasons.append(f"OI{oi_change:+.1f}% 空头清算")
        pattern = "short_squeeze"
    elif oi_change > 0 and price_change < 0:
        # 多头抄底 — 中等信号
        score += 10
        reasons.append(f"OI+{oi_change:.1f}% 多头抄底")
        pattern = "dip_buying"
    elif oi_change < 0 and price_change < 0:
        pattern = 'capital_out'
        score += 4
        reasons.append('资金撤离')
    else:
        pattern = 'neutral'
        score += 2
    
    details["oi_change"] = oi_change
    details["oi_change_4h"] = oi_change_4h
    details["oi_pattern"] = pattern
    
    # === 3. 成交量 (10分) ===
    volume_ratio = taker.get("volume_ratio", 1)
    if volume_ratio > 3:
        score += 10
        reasons.append(f"量比{volume_ratio:.1f}x 放量")
    elif volume_ratio > 2:
        score += 8
        reasons.append(f"量比{volume_ratio:.1f}x")
    elif volume_ratio > 1.2:
        score += 5
    elif volume_ratio > 0.8:
        score += 3
    
    details["volume_ratio"] = volume_ratio
    
    # === 4. 资金费率 (8分) ===
    funding_rate = funding.get("rate", 0)
    if -0.005 < funding_rate < 0.005:
        score += 8
        reasons.append("费率中性")
    elif funding_rate < 0:
        score += 6
        reasons.append(f"负费率{funding_rate*100:.3f}%")
    elif funding_rate < 0.01:
        score += 4
    else:
        score += 1  # 高费率=多头过热
    
    details["funding_rate"] = funding_rate
    
    # === 5. 多空比 (8分) ===
    long_ratio = ls_ratio.get("long_ratio", 50)
    if long_ratio < 40:
        score += 8
        reasons.append(f"多头{long_ratio:.0f}% 极度空头拥挤")
    elif long_ratio < 45:
        score += 6
        reasons.append(f"多头{long_ratio:.0f}% 空头拥挤")
    elif long_ratio < 50:
        score += 4
    elif long_ratio > 65:
        score -= 4  # 多头过热扣分
        reasons.append(f"多头{long_ratio:.0f}% 过热⚠️")
    
    details["long_ratio"] = long_ratio
    
    # === 6. RSI (8分) ===
    rsi = klines.get("rsi", 50)
    if 35 < rsi < 65:
        score += 8
        reasons.append(f"RSI{rsi:.0f} 健康")
    elif 25 < rsi <= 35:
        score += 6
        reasons.append(f"RSI{rsi:.0f} 偏低")
    elif 65 <= rsi < 75:
        score += 4
    elif rsi >= 75:
        score += 1
        reasons.append(f"RSI{rsi:.0f} 过热⚠️")
    elif rsi <= 25:
        score += 5
        reasons.append(f"RSI{rsi:.0f} 超卖")
    
    details["rsi"] = rsi
    
    # === 7. 连续上涨检测 (8分) ===
    klines_4h = klines.get("klines_4h", [])
    if len(klines_4h) >= 3:
        consecutive_up = 0
        for i in range(len(klines_4h)-1, max(0, len(klines_4h)-4), -1):
            if i > 0:
                prev_close = float(klines_4h[i-1][4]) if len(klines_4h[i-1]) > 4 else 0
                curr_close = float(klines_4h[i][4]) if len(klines_4h[i]) > 4 else 0
                if prev_close > 0 and curr_close > prev_close:
                    consecutive_up += 1
                else:
                    break
        
        if consecutive_up >= 3:
            score += 8
            reasons.append(f"连续{consecutive_up}小时上涨")
            details["consecutive_up"] = consecutive_up
        elif consecutive_up >= 2:
            score += 4
            details["consecutive_up"] = consecutive_up

    # ============================================================
    # v3.0 新增: 技术指标维度
    # ============================================================
    
    # === 8. EMA多周期趋势 (10分) ===
    ema_trend = klines.get("ema_trend", "neutral")
    ema_align = klines.get("ema_align", "")
    ema_spread = klines.get("ema_spread", 0)
    ema_slope = klines.get("ema_slope", 0)
    ema20 = klines.get("ema20", 0)
    close = klines.get("close", 0)
    
    if ema_trend == "bullish":
        score += 10
        reasons.append(f"EMA多头排列 离散{ema_spread:+.1f}%")
    elif ema_trend == "recovering":
        score += 7
        reasons.append("EMA金叉形成中")
    elif ema_trend == "neutral":
        score += 3
    elif ema_trend == "weakening":
        score -= 3
        reasons.append("EMA死叉形成中⚠️")
    elif ema_trend == "bearish":
        score -= 5
        reasons.append("EMA空头排列⚠️")
    
    # EMA斜率加分
    if ema_slope > 0.15:
        score += 3
        reasons.append(f"EMA21强势上行 {ema_slope:+.2f}%")
    elif ema_slope < -0.15:
        score -= 3
        reasons.append(f"EMA21下行 {ema_slope:+.2f}%")
    
    # 价格在EMA20上方
    if ema20 > 0 and close > 0:
        if close > ema20:
            score += 2
            reasons.append("价格>EMA20")
    
    details["ema_trend"] = ema_trend
    details["ema_align"] = ema_align
    details["ema_spread"] = ema_spread
    details["ema_slope"] = ema_slope
    
    # === 9. MACD信号 (10分) ===
    macd_cross = klines.get("macd_cross", "none")
    macd_trend = klines.get("macd_trend", "neutral")
    histogram_action = klines.get("histogram_action", "continuing")
    histogram_momentum = klines.get("histogram_momentum", "unknown")
    
    if macd_cross == "golden_cross":
        score += 10
        reasons.append("MACD金叉🔥")
    elif macd_cross == "death_cross":
        score -= 5
        reasons.append("MACD死叉⚠️")
    
    if macd_trend == "bullish_zone":
        score += 4
        reasons.append("MACD零轴上方")
    elif macd_trend == "bearish_zone":
        score -= 2
    
    # 柱状图走平+趋势转换 = 即将金叉 (提前布局)
    if histogram_action == "flattening" and macd_trend in ("turning_bullish", "bearish_zone"):
        score += 3
        reasons.append("MACD柱状图走平 即将金叉")
    
    details["macd_cross"] = macd_cross
    details["macd_trend"] = macd_trend
    details["histogram_action"] = histogram_action
    
    # v3.1 趋势确认组合加分
    trend_bonus = 0
    trend_signals = []
    if ema_trend == "bullish" and macd_trend == "bullish_zone":
        trend_bonus += 5
        trend_signals.append("EMA多头+MACD零轴上")
    if ema_trend == "bullish" and macd_cross == "golden_cross":
        trend_bonus += 8
        trend_signals.append("EMA多头+MACD金叉")
    if ema_trend == "bullish" and klines.get("bb_pct_b", 0.5) < 0.3:
        trend_bonus += 5
        trend_signals.append("EMA多头+布林下方超卖")
    if ema_trend == "bullish" and klines.get("vol_breakout", False):
        trend_bonus += 5
        trend_signals.append("EMA多头+放量突破")
    if trend_bonus > 0:
        score += trend_bonus
        reasons.append(f"趋势共振+{trend_bonus} {'|'.join(trend_signals)}")
    
    # === 10. 布林带位置 (8分) ===
    bb_pct_b = klines.get("bb_pct_b", 0.5)
    bb_squeeze = klines.get("bb_squeeze", False)
    bb_bandwidth = klines.get("bb_bandwidth", 0)
    bb_upper = klines.get("bb_upper", 0)
    bb_lower = klines.get("bb_lower", 0)
    bb_middle = klines.get("bb_middle", 0)
    
    if bb_pct_b < 0.15:
        # 触及下轨附近 = 超卖反弹机会
        score += 8
        reasons.append(f"触及布林下轨 %B={bb_pct_b:.2f} 超卖")
    elif bb_pct_b < 0.3:
        score += 5
        reasons.append(f"布林带下方 %B={bb_pct_b:.2f}")
    elif bb_pct_b > 0.85:
        score -= 3
        reasons.append(f"触及布林上轨 %B={bb_pct_b:.2f} 超买⚠️")
    
    if bb_squeeze:
        score += 4
        reasons.append(f"布林带挤压 bandwidth={bb_bandwidth:.1f}% 即将变盘")
    
    details["bb_pct_b"] = bb_pct_b
    details["bb_squeeze"] = bb_squeeze
    
    # === 11. 量价配合模式 (8分) ===
    vol_pattern = klines.get("vol_pattern", "normal")
    vol_health = klines.get("vol_health", "neutral")
    vol_signal = klines.get("vol_signal", "none")
    
    if vol_pattern == "vol_breakout":
        score += 8
        reasons.append("放量突破🔥")
    elif vol_pattern == "vol_shrink_pullback":
        score += 7
        reasons.append("缩量回调=健康调整 买入机会")
    elif vol_pattern == "vol_expansion":
        score += 6
        reasons.append("放量上涨")
    elif vol_pattern == "vol_divergence_top":
        score -= 5
        reasons.append("量价顶背离⚠️")
    elif vol_pattern == "vol_panic":
        score -= 6
        reasons.append("恐慌放量⚠️")
    elif vol_pattern == "vol_divergence_bottom":
        score += 4
        reasons.append("底背离 抛压衰竭")
    
    details["vol_pattern"] = vol_pattern
    details["vol_health"] = vol_health
    
    # === 12. 支撑阻力位置 (8分) ===
    sr_position = klines.get("sr_position", 50)
    sr_support = klines.get("sr_support", 0)
    sr_resistance = klines.get("sr_resistance", 0)
    
    if sr_position < 20:
        score += 8
        reasons.append(f"接近支撑位 位置{sr_position:.0f}% 强支撑")
    elif sr_position < 35:
        score += 5
        reasons.append(f"偏支撑位 位置{sr_position:.0f}%")
    elif sr_position > 85:
        score -= 4
        reasons.append(f"接近阻力位 位置{sr_position:.0f}%⚠️")
    elif sr_position > 70:
        score -= 2
        reasons.append(f"偏阻力位 位置{sr_position:.0f}%")
    
    details["sr_position"] = sr_position
    details["sr_support"] = sr_support
    details["sr_resistance"] = sr_resistance
    
    # === 13. 箱体位置 (5分) ===
    box_status = klines.get("box_status", "not_detected")
    box_position = klines.get("box_position", 50)
    box_high = klines.get("box_high", 0)
    box_low = klines.get("box_low", 0)
    
    if box_status == "confirmed":
        if box_position < 25:
            score += 5
            reasons.append(f"箱体下沿附近 位置{box_position:.0f}%")
        elif box_position > 75:
            score -= 3
            reasons.append(f"箱体上沿附近 位置{box_position:.0f}%⚠️")
    elif box_status == "breaking_up":
        score += 5
        reasons.append("箱体向上突破🔥")
    elif box_status == "breaking_down":
        score -= 4
        reasons.append("箱体向下破位⚠️")
    
    details["box_status"] = box_status
    details["box_position"] = box_position
    
    # === 应用模式权重 ===
    if pattern in LONG_PATTERNS:
        pattern_info = LONG_PATTERNS[pattern]
        weighted_score = int(score * pattern_info["weight"])
        stop_loss_pct = pattern_info["stop_loss"]
        details["pattern_weight"] = pattern_info["weight"]
        details["raw_score"] = score
        score = weighted_score
    else:
        stop_loss_pct = 0.06  # 默认6%
    
    # === 过滤条件 ===
    # RSI过热不开仓
    if rsi > 80:
        score = max(0, score - 20)
        reasons.append(f"RSI{rsi:.0f} 极度过热，大幅扣分")
    
    # 多头过热不开仓
    if long_ratio > 70:
        score = max(0, score - 15)
        reasons.append(f"多头{long_ratio:.0f}% 极度拥挤，大幅扣分")
    
    # MACD死叉+量价顶背离 = 双重看空，大幅扣分
    if macd_cross == "death_cross" and vol_pattern == "vol_divergence_top":
        score = max(0, score - 15)
        reasons.append("MACD死叉+量价顶背离 双重看空")
    
    # 布林带超买+EMA空头排列 = 趋势转弱
    if bb_pct_b > 0.9 and ema_trend == "bearish":
        score = max(0, score - 10)
        reasons.append("布林超买+EMA空头 趋势转弱")
    
    return min(max(0, score), 100), details, reasons, pattern, stop_loss_pct


def get_entry_reasons_v2(details: dict) -> list:
    """获取入场理由 v3.0"""
    reasons = []
    
    pattern = details.get("oi_pattern", "none")
    pattern_desc = {
        "new_capital": "新资金进场(最强)",
        "short_squeeze": "空头清算(强势)",
        "dip_buying": "多头抄底(中等)",
        "capital_out": "资金撤离上涨(弱势)",
    }
    if pattern in pattern_desc:
        reasons.append(f"模式: {pattern_desc[pattern]}")
    
    if details.get("price_change_24h", 0) > 5:
        reasons.append("强势上涨")
    if details.get("oi_change", 0) > 5:
        reasons.append(f"OI增{details['oi_change']:.1f}%")
    if details.get("funding_rate", 0) < 0:
        reasons.append("负费率有利")
    if details.get("long_ratio", 50) < 45:
        reasons.append("空头拥挤")
    if details.get("volume_ratio", 1) > 2:
        reasons.append("放量上涨")
    if details.get("rsi", 50) < 40:
        reasons.append("RSI偏低")
    
    # v3.0 新增理由
    ema_trend = details.get("ema_trend", "neutral")
    if ema_trend == "bullish":
        reasons.append("EMA多头排列")
    elif ema_trend == "recovering":
        reasons.append("EMA金叉形成中")
    
    macd_cross = details.get("macd_cross", "none")
    if macd_cross == "golden_cross":
        reasons.append("MACD金叉")
    
    bb_pct_b = details.get("bb_pct_b", 0.5)
    if bb_pct_b < 0.2:
        reasons.append("布林下轨超卖")
    
    vol_pattern = details.get("vol_pattern", "normal")
    if vol_pattern == "vol_shrink_pullback":
        reasons.append("缩量回调=健康")
    elif vol_pattern == "vol_breakout":
        reasons.append("放量突破")
    
    sr_pos = details.get("sr_position", 50)
    if sr_pos < 25:
        reasons.append("接近支撑位")
    
    box_status = details.get("box_status", "")
    if box_status == "breaking_up":
        reasons.append("箱体突破")
    
    return reasons
