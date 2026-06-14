#!/usr/bin/env python3
"""
做空评分 v2.0 vs v2.1 vs v2.2 三版本对比回测
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
from modes.short_mode_v22 import calc_short_score_v22, SHORT_PATTERNS as PATTERNS_V22

from backtest_short_v2 import (
    get_klines, get_oi_data, get_funding_rate,
    calculate_ema, calculate_rsi, count_consecutive_red,
    prepare_backtest_data, run_backtest
)

def run_backtest_v22(symbol, data, lookback=200):
    """运行v2.2回测"""
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
        
        # v2.2评分
        score, details, reasons, pattern, stop_loss_pct = calc_short_score_v22(
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

def analyze_results(version, results, patterns):
    """分析结果"""
    if not results:
        return {'total': 0, 'pattern_count': 0, 'high_score': 0, 'accuracy': 0}
    
    total = len(results)
    pattern_signals = len([r for r in results if r['pattern'] is not None])
    high_score = len([r for r in results if r['score'] >= 50])
    
    high_score_results = [r for r in results if r['score'] >= 50 and r['future_24h_change'] is not None]
    if high_score_results:
        correct = len([r for r in high_score_results if r['future_24h_change'] < 0])
        accuracy = correct / len(high_score_results) * 100
    else:
        accuracy = 0
    
    # 按模式统计
    pattern_stats = {}
    for pattern_id, pattern_def in patterns.items():
        pattern_results = [r for r in results if r['pattern'] == pattern_id and r['future_24h_change'] is not None]
        if pattern_results:
            avg_future = sum(r['future_24h_change'] for r in pattern_results) / len(pattern_results)
            correct = len([r for r in pattern_results if r['future_24h_change'] < 0])
            pattern_accuracy = correct / len(pattern_results) * 100
            pattern_stats[pattern_id] = {
                'count': len(pattern_results),
                'avg_future': avg_future,
                'accuracy': pattern_accuracy,
            }
    
    return {
        'total': total,
        'pattern_count': pattern_signals,
        'high_score': high_score,
        'accuracy': accuracy,
        'pattern_stats': pattern_stats,
    }

def main():
    """主函数"""
    print("=" * 60)
    print("做空评分 v2.0 vs v2.1 vs v2.2 三版本对比回测")
    print("=" * 60)
    
    symbols = ['RIVERUSDT', 'RAVEUSDT', 'HUSDT', 'SAHARAUSDT', 'OPNUSDT']
    
    all_results = {
        'v2.0': [],
        'v2.1': [],
        'v2.2': [],
    }
    
    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"分析 {symbol}")
        print(f"{'='*60}")
        
        data = prepare_backtest_data(symbol)
        if data is None:
            continue
        
        # v2.0
        results_v2 = run_backtest(symbol, data, lookback=200)
        all_results['v2.0'].extend(results_v2)
        
        # v2.1
        from backtest_compare import run_backtest_v21
        results_v21 = run_backtest_v21(symbol, data, lookback=200)
        all_results['v2.1'].extend(results_v21)
        
        # v2.2
        results_v22 = run_backtest_v22(symbol, data, lookback=200)
        all_results['v2.2'].extend(results_v22)
        
        time.sleep(1)
    
    # 汇总对比
    print("\n" + "=" * 60)
    print("📊 三版本汇总对比")
    print("=" * 60)
    
    stats = {}
    for version, results in all_results.items():
        if version == 'v2.0':
            patterns = PATTERNS_V2
        elif version == 'v2.1':
            patterns = PATTERNS_V21
        else:
            patterns = PATTERNS_V22
        
        stats[version] = analyze_results(version, results, patterns)
    
    # 打印对比表格
    print(f"\n{'指标':<20} {'v2.0':<15} {'v2.1':<15} {'v2.2':<15}")
    print("-" * 65)
    print(f"{'模式识别数':<20} {stats['v2.0']['pattern_count']:<15} {stats['v2.1']['pattern_count']:<15} {stats['v2.2']['pattern_count']:<15}")
    print(f"{'高分信号数(≥50)':<20} {stats['v2.0']['high_score']:<15} {stats['v2.1']['high_score']:<15} {stats['v2.2']['high_score']:<15}")
    print(f"{'高分信号准确率':<20} {stats['v2.0']['accuracy']:.1f}%{'':<10} {stats['v2.1']['accuracy']:.1f}%{'':<10} {stats['v2.2']['accuracy']:.1f}%{'':<10}")
    
    # 模式详细对比
    print(f"\n{'='*60}")
    print("🎯 模式识别详细对比")
    print(f"{'='*60}")
    
    for pattern_name in ['趋势转弱', 'OI背离']:
        print(f"\n{pattern_name}:")
        for version in ['v2.0', 'v2.1', 'v2.2']:
            if version == 'v2.0':
                pattern_id = 'trend_weak' if pattern_name == '趋势转弱' else 'oi_diverge'
            elif version == 'v2.1':
                pattern_id = 'trend_weak' if pattern_name == '趋势转弱' else 'oi_diverge'
            else:
                pattern_id = 'trend_weak' if pattern_name == '趋势转弱' else 'oi_diverge'
            
            ps = stats[version].get('pattern_stats', {}).get(pattern_id)
            if ps:
                print(f"  {version}: {ps['count']}个, 平均变化{ps['avg_future']:.2f}%, 准确率{ps['accuracy']:.1f}%")
            else:
                print(f"  {version}: 无信号")
    
    # 找出最佳版本
    print(f"\n{'='*60}")
    print("🏆 最佳版本评估")
    print(f"{'='*60}")
    
    # 评估标准：准确率 > 信号数量
    best_version = max(stats.items(), key=lambda x: x[1]['accuracy'])
    
    print(f"\n最佳版本: {best_version[0]}")
    print(f"  高分信号准确率: {best_version[1]['accuracy']:.1f}%")
    print(f"  高分信号数量: {best_version[1]['high_score']}")
    print(f"  模式识别数量: {best_version[1]['pattern_count']}")
    
    # 保存结果
    output_file = '~/demon-coin-detector/unified_system/backtest_3way_comparison.json'
    save_data = {
        'timestamp': datetime.now().isoformat(),
        'stats': stats,
        'best_version': best_version[0],
    }
    
    with open(output_file, 'w') as f:
        json.dump(save_data, f, indent=2, default=str)
    
    print(f"\n💾 对比结果已保存到: {output_file}")
    
    # 集成建议
    print(f"\n{'='*60}")
    print("🚀 集成建议")
    print(f"{'='*60}")
    
    if best_version[0] == 'v2.0':
        print("✅ 建议保持 v2.0 原版本")
        print("原因: 严格阈值带来更高准确率")
    elif best_version[0] == 'v2.2':
        print("✅ 建议集成 v2.2")
        print("原因: OI权重提升带来更好表现")
    else:
        print("⚠️ v2.1 表现一般，不建议集成")

if __name__ == "__main__":
    main()
