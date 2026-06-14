#!/usr/bin/env python3
"""
做空评分 v2.1 vs v2.0 对比回测
验证优化效果
"""

import requests
import pandas as pd
import json
import time
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modes.short_mode_v2 import calc_short_score_v2, SHORT_PATTERNS as PATTERNS_V2
from modes.short_mode_v21 import calc_short_score_v21, SHORT_PATTERNS as PATTERNS_V21

# 复用v2的数据获取函数
from backtest_short_v2 import (
    get_klines, get_oi_data, get_funding_rate,
    calculate_ema, calculate_rsi, count_consecutive_red,
    prepare_backtest_data
)

def run_backtest_v21(symbol, data, lookback=200):
    """运行v2.1回测"""
    klines_df = data['klines']
    oi_df = data['oi']
    funding_df = data['funding']
    
    results = []
    start_idx = max(50, len(klines_df) - lookback)
    
    for i in range(start_idx, len(klines_df) - 6):
        current_time = klines_df.iloc[i]['timestamp']
        current_price = klines_df.iloc[i]['close']
        
        klines_data = {
            'close': current_price,
            'price_change_24h': klines_df.iloc[i]['price_change_24h'] if not pd.isna(klines_df.iloc[i]['price_change_24h']) else 0,
            'price_change_4h': klines_df.iloc[i]['price_change_4h'] if not pd.isna(klines_df.iloc[i]['price_change_4h']) else 0,
            'rsi': klines_df.iloc[i]['rsi'] if not pd.isna(klines_df.iloc[i]['rsi']) else 50,
            'consecutive_red': count_consecutive_red(klines_df, i),
        }
        
        ema_data = {
            'ema20': klines_df.iloc[i]['ema20'],
            'ema50': klines_df.iloc[i]['ema50'],
        }
        
        oi_data = {'oi_change_24h': 0, 'oi_change_4h': 0}
        if oi_df is not None:
            oi_match = oi_df[oi_df['timestamp'] <= current_time].tail(1)
            if len(oi_match) > 0:
                oi_data['oi_change_24h'] = oi_match.iloc[0].get('oi_change_24h', 0) or 0
                oi_data['oi_change_4h'] = oi_match.iloc[0].get('oi_change_4h', 0) or 0
        
        funding_data = {'rate': 0}
        if funding_df is not None:
            funding_match = funding_df[funding_df['fundingTime'] <= current_time].tail(1)
            if len(funding_match) > 0:
                funding_data['rate'] = funding_match.iloc[0]['fundingRate']
        
        ls_ratio = {'long_ratio': 55}
        taker_data = {
            'volume_ratio': klines_df.iloc[i]['volume_ratio'] if not pd.isna(klines_df.iloc[i]['volume_ratio']) else 1,
            'taker_sell_ratio': 50,
        }
        
        # v2.1评分
        score, details, reasons, pattern, stop_loss_pct = calc_short_score_v21(
            symbol, klines_data, funding_data, oi_data, ls_ratio, taker_data, ema_data
        )
        
        if i + 6 < len(klines_df):
            future_price = klines_df.iloc[i + 6]['close']
            future_change = (future_price - current_price) / current_price * 100
        else:
            future_change = None
        
        results.append({
            'timestamp': current_time,
            'price': current_price,
            'score': score,
            'pattern': pattern,
            'stop_loss_pct': stop_loss_pct,
            'future_24h_change': future_change,
            'details': details,
            'reasons': reasons,
        })
    
    return results

def compare_versions(symbol, data):
    """对比v2.0和v2.1的表现"""
    print(f"\n{'='*60}")
    print(f"{symbol} 版本对比")
    print(f"{'='*60}")
    
    # 运行v2.0回测
    from backtest_short_v2 import run_backtest
    results_v2 = run_backtest(symbol, data, lookback=200)
    
    # 运行v2.1回测
    results_v21 = run_backtest_v21(symbol, data, lookback=200)
    
    # 分析结果
    valid_v2 = [r for r in results_v2 if r['future_24h_change'] is not None]
    valid_v21 = [r for r in results_v21 if r['future_24h_change'] is not None]
    
    print(f"\n📊 v2.0 统计:")
    analyze_results("v2.0", valid_v2, PATTERNS_V2)
    
    print(f"\n📊 v2.1 统计:")
    analyze_results("v2.1", valid_v21, PATTERNS_V21)
    
    return valid_v2, valid_v21

def analyze_results(version, results, patterns):
    """分析结果"""
    if not results:
        print("  无有效结果")
        return
    
    total = len(results)
    pattern_signals = len([r for r in results if r['pattern'] is not None])
    high_score = len([r for r in results if r['score'] >= 50])
    
    print(f"  总信号: {total}")
    print(f"  模式识别: {pattern_signals} ({pattern_signals/total*100:.1f}%)")
    print(f"  高分(≥50): {high_score} ({high_score/total*100:.1f}%)")
    
    # 按模式统计
    print(f"\n  模式识别:")
    for pattern_id, pattern_def in patterns.items():
        pattern_results = [r for r in results if r['pattern'] == pattern_id]
        if pattern_results:
            avg_future = sum(r['future_24h_change'] for r in pattern_results) / len(pattern_results)
            correct = len([r for r in pattern_results if r['future_24h_change'] < 0])
            accuracy = correct / len(pattern_results) * 100
            print(f"    {pattern_def['name']}: {len(pattern_results)}个, 平均变化{avg_future:.2f}%, 准确率{accuracy:.1f}%")
    
    # 高分信号分析
    high_score_results = [r for r in results if r['score'] >= 50]
    if high_score_results:
        avg_future = sum(r['future_24h_change'] for r in high_score_results) / len(high_score_results)
        correct = len([r for r in high_score_results if r['future_24h_change'] < 0])
        accuracy = correct / len(high_score_results) * 100
        print(f"\n  高分信号(≥50): 平均变化{avg_future:.2f}%, 准确率{accuracy:.1f}%")

def main():
    """主函数"""
    print("=" * 60)
    print("做空评分 v2.0 vs v2.1 对比回测")
    print("=" * 60)
    
    symbols = ['RIVERUSDT', 'RAVEUSDT', 'HUSDT', 'SAHARAUSDT', 'OPNUSDT']
    
    all_v2 = []
    all_v21 = []
    
    for symbol in symbols:
        data = prepare_backtest_data(symbol)
        if data is None:
            continue
        
        v2, v21 = compare_versions(symbol, data)
        all_v2.extend(v2)
        all_v21.extend(v21)
        
        time.sleep(1)
    
    # 汇总对比
    print("\n" + "=" * 60)
    print("📊 全币种汇总对比")
    print("=" * 60)
    
    print("\n🔴 v2.0:")
    analyze_results("v2.0", all_v2, PATTERNS_V2)
    
    print("\n🟢 v2.1:")
    analyze_results("v2.1", all_v21, PATTERNS_V21)
    
    # 改进分析
    print("\n📈 改进分析:")
    
    v2_pattern_count = len([r for r in all_v2 if r['pattern'] is not None])
    v21_pattern_count = len([r for r in all_v21 if r['pattern'] is not None])
    
    v2_high_score = len([r for r in all_v2 if r['score'] >= 50])
    v21_high_score = len([r for r in all_v21 if r['score'] >= 50])
    
    v2_correct = len([r for r in all_v2 if r['score'] >= 50 and r['future_24h_change'] and r['future_24h_change'] < 0])
    v21_correct = len([r for r in all_v21 if r['score'] >= 50 and r['future_24h_change'] and r['future_24h_change'] < 0])
    
    v2_accuracy = v2_correct / v2_high_score * 100 if v2_high_score > 0 else 0
    v21_accuracy = v21_correct / v21_high_score * 100 if v21_high_score > 0 else 0
    
    print(f"  模式识别数量: {v2_pattern_count} → {v21_pattern_count} ({v21_pattern_count - v2_pattern_count:+d})")
    print(f"  高分信号数量: {v2_high_score} → {v21_high_score} ({v21_high_score - v2_high_score:+d})")
    print(f"  高分信号准确率: {v2_accuracy:.1f}% → {v21_accuracy:.1f}% ({v21_accuracy - v2_accuracy:+.1f}%)")
    
    # 保存结果
    output_file = '~/demon-coin-detector/unified_system/backtest_comparison.json'
    save_data = {
        'timestamp': datetime.now().isoformat(),
        'v2_stats': {
            'pattern_count': v2_pattern_count,
            'high_score_count': v2_high_score,
            'accuracy': v2_accuracy,
        },
        'v21_stats': {
            'pattern_count': v21_pattern_count,
            'high_score_count': v21_high_score,
            'accuracy': v21_accuracy,
        },
    }
    
    with open(output_file, 'w') as f:
        json.dump(save_data, f, indent=2)
    
    print(f"\n💾 对比结果已保存到: {output_file}")
    
    # 建议
    print("\n" + "=" * 60)
    print("🚀 集成建议")
    print("=" * 60)
    
    if v21_accuracy > v2_accuracy:
        print("✅ v2.1 表现更优，建议集成")
        print(f"\n改进幅度: {v21_accuracy - v2_accuracy:+.1f}%")
    elif v21_accuracy == v2_accuracy:
        print("⚠️ v2.1 与 v2.0 表现相当")
    else:
        print("❌ v2.1 表现不如 v2.0，建议保持原版本")

if __name__ == "__main__":
    main()
