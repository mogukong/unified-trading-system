#!/usr/bin/env python3
"""
做空评分 v2.0 测试脚本
验证四种做空模式的识别
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modes.short_mode_v2 import calc_short_score_v2, SHORT_PATTERNS

def test_surge_top():
    """测试暴涨后见顶模式"""
    print("=" * 60)
    print("测试1: 暴涨后见顶模式 (OPN案例)")
    print("=" * 60)
    
    klines = {
        "close": 0.15,
        "price_change_24h": 145.33,
        "price_change_4h": 20.5,
        "rsi": 75,
    }
    
    funding = {"rate": 0.0097}
    oi_data = {
        "oi_change_24h": 184.76,
        "oi_change_4h": 45.2,
    }
    ls_ratio = {"long_ratio": 68}
    taker = {"volume_ratio": 1.8, "taker_sell_ratio": 55}
    
    score, details, reasons, pattern, sl_pct = calc_short_score_v2(
        "OPNUSDT", klines, funding, oi_data, ls_ratio, taker
    )
    
    print(f"评分: {score}/100")
    print(f"模式: {pattern} ({SHORT_PATTERNS.get(pattern, {}).get('name', 'N/A')})")
    print(f"止损: {sl_pct*100:.1f}%")
    print(f"理由: {reasons}")
    print(f"详情: {details}")
    print()
    
    # 验证
    assert pattern == "surge_top", f"预期模式 surge_top，实际 {pattern}"
    assert score >= 70, f"预期评分 >= 70，实际 {score}"
    assert sl_pct == 0.05, f"预期止损 5%，实际 {sl_pct*100}%"
    print("✅ 测试通过!")
    print()

def test_trend_weak():
    """测试趋势转弱模式"""
    print("=" * 60)
    print("测试2: 趋势转弱模式 (RAVE案例)")
    print("=" * 60)
    
    klines = {
        "close": 0.35,
        "price_change_24h": -8.5,
        "price_change_4h": -5.2,
        "rsi": 45,
        "consecutive_red": 3,  # 连续3根阴线
    }
    
    funding = {"rate": 0.002}
    oi_data = {
        "oi_change_24h": 3.5,
        "oi_change_4h": 1.2,
    }
    ls_ratio = {"long_ratio": 55}
    taker = {"volume_ratio": 1.6, "taker_sell_ratio": 52}
    ema_data = {
        "ema20": 0.38,
        "ema50": 0.40,
    }
    
    score, details, reasons, pattern, sl_pct = calc_short_score_v2(
        "RAVEUSDT", klines, funding, oi_data, ls_ratio, taker, ema_data
    )
    
    print(f"评分: {score}/100")
    print(f"模式: {pattern} ({SHORT_PATTERNS.get(pattern, {}).get('name', 'N/A')})")
    print(f"止损: {sl_pct*100:.1f}%")
    print(f"理由: {reasons}")
    print()
    
    # 验证
    assert pattern == "trend_weak", f"预期模式 trend_weak，实际 {pattern}"
    assert score >= 50, f"预期评分 >= 50，实际 {score}"
    assert sl_pct == 0.06, f"预期止损 6%，实际 {sl_pct*100}%"
    print("✅ 测试通过!")
    print()

def test_oi_diverge():
    """测试OI背离模式"""
    print("=" * 60)
    print("测试3: OI背离模式")
    print("=" * 60)
    
    klines = {
        "close": 0.25,
        "price_change_24h": -5.5,
        "price_change_4h": -2.8,
        "rsi": 48,
    }
    
    funding = {"rate": 0.003}
    oi_data = {
        "oi_change_24h": 15.5,
        "oi_change_4h": 6.8,
    }
    ls_ratio = {"long_ratio": 58}
    taker = {"volume_ratio": 1.3, "taker_sell_ratio": 54}
    
    score, details, reasons, pattern, sl_pct = calc_short_score_v2(
        "TESTUSDT", klines, funding, oi_data, ls_ratio, taker
    )
    
    print(f"评分: {score}/100")
    print(f"模式: {pattern} ({SHORT_PATTERNS.get(pattern, {}).get('name', 'N/A')})")
    print(f"止损: {sl_pct*100:.1f}%")
    print(f"理由: {reasons}")
    print()
    
    # 验证
    assert pattern == "oi_diverge", f"预期模式 oi_diverge，实际 {pattern}"
    assert score >= 50, f"预期评分 >= 50，实际 {score}"
    assert sl_pct == 0.06, f"预期止损 6%，实际 {sl_pct*100}%"
    print("✅ 测试通过!")
    print()

def test_panic_sell():
    """测试恐慌性抛售模式"""
    print("=" * 60)
    print("测试4: 恐慌性抛售模式 (H案例)")
    print("=" * 60)
    
    klines = {
        "close": 0.08,
        "price_change_24h": -25.5,
        "price_change_4h": -22.8,
        "rsi": 25,
    }
    
    funding = {"rate": -0.002}
    oi_data = {
        "oi_change_24h": -18.5,
        "oi_change_4h": -8.2,
    }
    ls_ratio = {"long_ratio": 45}
    taker = {"volume_ratio": 4.5, "taker_sell_ratio": 65}
    
    score, details, reasons, pattern, sl_pct = calc_short_score_v2(
        "HUSDT", klines, funding, oi_data, ls_ratio, taker
    )
    
    print(f"评分: {score}/100")
    print(f"模式: {pattern} ({SHORT_PATTERNS.get(pattern, {}).get('name', 'N/A')})")
    print(f"止损: {sl_pct*100:.1f}%")
    print(f"理由: {reasons}")
    print()
    
    # 验证
    assert pattern == "panic_sell", f"预期模式 panic_sell，实际 {pattern}"
    assert score >= 40, f"预期评分 >= 40，实际 {score}"
    assert sl_pct == 0.08, f"预期止损 8%，实际 {sl_pct*100}%"
    print("✅ 测试通过!")
    print()

def test_no_signal():
    """测试无信号情况"""
    print("=" * 60)
    print("测试5: 无信号情况")
    print("=" * 60)
    
    klines = {
        "close": 1.0,
        "price_change_24h": 2.5,
        "price_change_4h": 0.8,
        "rsi": 55,
    }
    
    funding = {"rate": 0.001}
    oi_data = {
        "oi_change_24h": 1.5,
        "oi_change_4h": 0.5,
    }
    ls_ratio = {"long_ratio": 52}
    taker = {"volume_ratio": 0.9, "taker_sell_ratio": 48}
    
    score, details, reasons, pattern, sl_pct = calc_short_score_v2(
        "NEUTRALUSDT", klines, funding, oi_data, ls_ratio, taker
    )
    
    print(f"评分: {score}/100")
    print(f"模式: {pattern}")
    print(f"理由: {reasons}")
    print()
    
    # 验证
    assert pattern is None, f"预期无模式，实际 {pattern}"
    assert score < 30, f"预期评分 < 30，实际 {score}"
    print("✅ 测试通过!")
    print()

def main():
    """运行所有测试"""
    print("=" * 60)
    print("做空评分 v2.0 测试")
    print("=" * 60)
    print()
    
    try:
        test_surge_top()
        test_trend_weak()
        test_oi_diverge()
        test_panic_sell()
        test_no_signal()
        
        print("=" * 60)
        print("🎉 所有测试通过!")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
