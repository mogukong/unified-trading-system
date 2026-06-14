#!/usr/bin/env python3
"""
做空评分 v2.0 历史数据验证
使用 RIVER/RAVE/H/SAHARA/OPN 的真实历史数据验证模式识别准确性
"""

import requests
import pandas as pd
import json
import time
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modes.short_mode_v2 import calc_short_score_v2, SHORT_PATTERNS

# ============================================================
# 数据获取
# ============================================================
def get_klines(symbol, interval='4h', limit=500):
    """获取K线数据"""
    url = 'https://fapi.binance.com/fapi/v1/klines'
    params = {'symbol': symbol, 'interval': interval, 'limit': limit}
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            df = pd.DataFrame(data, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume']:
                df[col] = df[col].astype(float)
            return df
    except Exception as e:
        print(f"获取K线失败 {symbol}: {e}")
    return None

def get_oi_data(symbol, period='4h', limit=500):
    """获取OI数据"""
    url = 'https://fapi.binance.com/futures/data/openInterestHist'
    params = {'symbol': symbol, 'period': period, 'limit': limit}
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            df = pd.DataFrame(data)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['sumOpenInterest'] = df['sumOpenInterest'].astype(float)
            return df
    except Exception as e:
        print(f"获取OI失败 {symbol}: {e}")
    return None

def get_funding_rate(symbol, limit=500):
    """获取资金费率"""
    url = 'https://fapi.binance.com/fapi/v1/fundingRate'
    params = {'symbol': symbol, 'limit': limit}
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            df = pd.DataFrame(data)
            df['fundingTime'] = pd.to_datetime(df['fundingTime'], unit='ms')
            df['fundingRate'] = df['fundingRate'].astype(float)
            return df
    except Exception as e:
        print(f"获取资金费率失败 {symbol}: {e}")
    return None

# ============================================================
# 技术指标计算
# ============================================================
def calculate_ema(prices, period):
    """计算EMA"""
    if len(prices) < period:
        return prices[-1] if prices else 0
    multiplier = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_rsi(prices, period=14):
    """计算RSI"""
    if len(prices) < period + 1:
        return 50
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def count_consecutive_red(klines_df, current_idx):
    """统计连续阴线数量"""
    count = 0
    for i in range(current_idx, max(0, current_idx - 10), -1):
        if klines_df.iloc[i]['close'] < klines_df.iloc[i]['open']:
            count += 1
        else:
            break
    return count

# ============================================================
# 数据准备
# ============================================================
def prepare_backtest_data(symbol):
    """准备回测数据"""
    print(f"\n{'='*60}")
    print(f"获取 {symbol} 历史数据...")
    print(f"{'='*60}")
    
    # 获取K线数据（4小时，500根 ≈ 83天）
    klines_df = get_klines(symbol, '4h', 500)
    if klines_df is None or len(klines_df) < 100:
        print(f"❌ {symbol} K线数据不足")
        return None
    
    # 获取OI数据
    oi_df = get_oi_data(symbol, '4h', 500)
    
    # 获取资金费率
    funding_df = get_funding_rate(symbol, 500)
    
    print(f"✅ K线: {len(klines_df)} 根")
    print(f"✅ OI: {len(oi_df) if oi_df is not None else 0} 条")
    print(f"✅ 资金费率: {len(funding_df) if funding_df is not None else 0} 条")
    
    # 计算技术指标
    klines_df['price_change_24h'] = klines_df['close'].pct_change(6) * 100  # 6根4h = 24h
    klines_df['price_change_4h'] = klines_df['close'].pct_change() * 100
    klines_df['ema20'] = klines_df['close'].ewm(span=20).mean()
    klines_df['ema50'] = klines_df['close'].ewm(span=50).mean()
    klines_df['rsi'] = klines_df['close'].rolling(14).apply(
        lambda x: calculate_rsi(x.tolist()), raw=False
    )
    klines_df['volume_ma'] = klines_df['volume'].rolling(20).mean()
    klines_df['volume_ratio'] = klines_df['volume'] / klines_df['volume_ma']
    
    # 计算OI变化
    if oi_df is not None and len(oi_df) > 6:
        oi_df['oi_change_24h'] = oi_df['sumOpenInterest'].pct_change(6) * 100
        oi_df['oi_change_4h'] = oi_df['sumOpenInterest'].pct_change() * 100
    
    return {
        'klines': klines_df,
        'oi': oi_df,
        'funding': funding_df,
    }

# ============================================================
# 回测逻辑
# ============================================================
def run_backtest(symbol, data, lookback=100):
    """
    运行回测
    lookback: 回测的K线数量
    """
    klines_df = data['klines']
    oi_df = data['oi']
    funding_df = data['funding']
    
    results = []
    
    # 从第50根K线开始（确保EMA有足够数据）
    start_idx = max(50, len(klines_df) - lookback)
    
    for i in range(start_idx, len(klines_df) - 6):  # 留6根作为未来验证
        current_time = klines_df.iloc[i]['timestamp']
        current_price = klines_df.iloc[i]['close']
        
        # 准备评分数据
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
        
        # OI数据
        oi_data = {'oi_change_24h': 0, 'oi_change_4h': 0}
        if oi_df is not None:
            oi_match = oi_df[oi_df['timestamp'] <= current_time].tail(1)
            if len(oi_match) > 0:
                oi_data['oi_change_24h'] = oi_match.iloc[0].get('oi_change_24h', 0) or 0
                oi_data['oi_change_4h'] = oi_match.iloc[0].get('oi_change_4h', 0) or 0
        
        # 资金费率
        funding_data = {'rate': 0}
        if funding_df is not None:
            funding_match = funding_df[funding_df['fundingTime'] <= current_time].tail(1)
            if len(funding_match) > 0:
                funding_data['rate'] = funding_match.iloc[0]['fundingRate']
        
        # 多空比（模拟，实际需要API）
        ls_ratio = {'long_ratio': 55}  # 默认值
        
        # 成交量数据
        taker_data = {
            'volume_ratio': klines_df.iloc[i]['volume_ratio'] if not pd.isna(klines_df.iloc[i]['volume_ratio']) else 1,
            'taker_sell_ratio': 50,  # 默认值
        }
        
        # 计算评分
        score, details, reasons, pattern, stop_loss_pct = calc_short_score_v2(
            symbol, klines_data, funding_data, oi_data, ls_ratio, taker_data, ema_data
        )
        
        # 计算未来24h（6根4h K线）的价格变化
        if i + 6 < len(klines_df):
            future_price = klines_df.iloc[i + 6]['close']
            future_change = (future_price - current_price) / current_price * 100
        else:
            future_change = None
        
        # 记录结果
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

# ============================================================
# 结果分析
# ============================================================
def analyze_results(symbol, results):
    """分析回测结果"""
    print(f"\n{'='*60}")
    print(f"{symbol} 回测结果分析")
    print(f"{'='*60}")
    
    # 过滤有效结果
    valid_results = [r for r in results if r['future_24h_change'] is not None]
    
    if not valid_results:
        print("❌ 无有效结果")
        return None
    
    # 统计总览
    total_signals = len(valid_results)
    pattern_signals = len([r for r in valid_results if r['pattern'] is not None])
    high_score_signals = len([r for r in valid_results if r['score'] >= 50])
    
    print(f"\n📊 总体统计:")
    print(f"  总信号数: {total_signals}")
    print(f"  模式识别数: {pattern_signals} ({pattern_signals/total_signals*100:.1f}%)")
    print(f"  高分信号(≥50): {high_score_signals} ({high_score_signals/total_signals*100:.1f}%)")
    
    # 按模式分析
    print(f"\n🎯 模式识别分析:")
    pattern_stats = {}
    for pattern_id in SHORT_PATTERNS.keys():
        pattern_results = [r for r in valid_results if r['pattern'] == pattern_id]
        if pattern_results:
            avg_future = sum(r['future_24h_change'] for r in pattern_results) / len(pattern_results)
            correct = len([r for r in pattern_results if r['future_24h_change'] < 0])
            accuracy = correct / len(pattern_results) * 100
            
            pattern_stats[pattern_id] = {
                'count': len(pattern_results),
                'avg_future_change': avg_future,
                'accuracy': accuracy,
            }
            
            print(f"\n  {SHORT_PATTERNS[pattern_id]['name']}:")
            print(f"    信号数: {len(pattern_results)}")
            print(f"    未来24h平均变化: {avg_future:.2f}%")
            print(f"    下跌准确率: {accuracy:.1f}%")
    
    # 按评分区间分析
    print(f"\n📈 评分区间分析:")
    score_ranges = [
        (80, 100, "强信号"),
        (60, 79, "中等信号"),
        (40, 59, "弱信号"),
        (20, 39, "极弱信号"),
        (0, 19, "无信号"),
    ]
    
    score_stats = {}
    for low, high, label in score_ranges:
        range_results = [r for r in valid_results if low <= r['score'] <= high]
        if range_results:
            avg_future = sum(r['future_24h_change'] for r in range_results) / len(range_results)
            correct = len([r for r in range_results if r['future_24h_change'] < 0])
            accuracy = correct / len(range_results) * 100
            
            score_stats[label] = {
                'count': len(range_results),
                'avg_future_change': avg_future,
                'accuracy': accuracy,
            }
            
            print(f"\n  {label} ({low}-{high}分):")
            print(f"    信号数: {len(range_results)}")
            print(f"    未来24h平均变化: {avg_future:.2f}%")
            print(f"    下跌准确率: {accuracy:.1f}%")
    
    # 最佳信号分析
    print(f"\n🏆 最佳信号分析:")
    best_signals = sorted(valid_results, key=lambda x: x['score'], reverse=True)[:10]
    
    print(f"\n  Top 10 高分信号:")
    for i, r in enumerate(best_signals, 1):
        future = r['future_24h_change']
        status = "✅" if future and future < 0 else "❌"
        pattern_name = SHORT_PATTERNS.get(r['pattern'], {}).get('name', '无模式')
        print(f"    {i}. {r['timestamp']} | 评分:{r['score']} | 模式:{pattern_name} | 未来24h:{future:+.2f}% {status}")
    
    # 暴涨后见顶信号详细分析
    print(f"\n🔍 暴涨后见顶信号详细分析:")
    surge_top_signals = [r for r in valid_results if r['pattern'] == 'surge_top']
    if surge_top_signals:
        print(f"  信号数: {len(surge_top_signals)}")
        for r in surge_top_signals[:5]:
            future = r['future_24h_change']
            status = "✅下跌" if future and future < 0 else "❌上涨"
            print(f"    {r['timestamp']} | 价格:{r['price']:.4f} | 评分:{r['score']} | 未来24h:{future:+.2f}% {status}")
            print(f"      理由: {', '.join(r['reasons'][:3])}")
    else:
        print("  未发现暴涨后见顶信号")
    
    return {
        'pattern_stats': pattern_stats,
        'score_stats': score_stats,
        'total_signals': total_signals,
        'pattern_signals': pattern_signals,
    }

# ============================================================
# 主函数
# ============================================================
def main():
    """主函数"""
    print("=" * 60)
    print("做空评分 v2.0 历史数据验证")
    print("=" * 60)
    
    # 要验证的币种
    symbols = ['RIVERUSDT', 'RAVEUSDT', 'HUSDT', 'SAHARAUSDT', 'OPNUSDT']
    
    all_results = {}
    all_stats = {}
    
    for symbol in symbols:
        # 准备数据
        data = prepare_backtest_data(symbol)
        if data is None:
            continue
        
        # 运行回测
        results = run_backtest(symbol, data, lookback=200)
        
        # 分析结果
        stats = analyze_results(symbol, results)
        
        if stats:
            all_results[symbol] = results
            all_stats[symbol] = stats
        
        time.sleep(1)  # 避免API限流
    
    # 汇总分析
    print("\n" + "=" * 60)
    print("📊 全币种汇总分析")
    print("=" * 60)
    
    # 汇总模式统计
    print("\n🎯 模式识别汇总:")
    total_pattern_signals = 0
    total_correct = 0
    
    for pattern_id in SHORT_PATTERNS.keys():
        pattern_name = SHORT_PATTERNS[pattern_id]['name']
        total_count = 0
        total_future = 0
        total_accuracy_count = 0
        
        for symbol, stats in all_stats.items():
            if pattern_id in stats['pattern_stats']:
                ps = stats['pattern_stats'][pattern_id]
                total_count += ps['count']
                total_future += ps['avg_future_change'] * ps['count']
                total_accuracy_count += ps['count'] * ps['accuracy'] / 100
        
        if total_count > 0:
            avg_future = total_future / total_count
            avg_accuracy = total_accuracy_count / total_count * 100
            print(f"\n  {pattern_name}:")
            print(f"    总信号数: {total_count}")
            print(f"    平均未来24h变化: {avg_future:.2f}%")
            print(f"    平均下跌准确率: {avg_accuracy:.1f}%")
            
            total_pattern_signals += total_count
            total_correct += total_accuracy_count
    
    # 总体准确率
    overall_accuracy = 0
    if total_pattern_signals > 0:
        overall_accuracy = total_correct / total_pattern_signals * 100
        print(f"\n📈 总体模式识别准确率: {overall_accuracy:.1f}%")
    
    # 汇总评分统计
    print("\n📊 评分区间汇总:")
    for label in ["强信号", "中等信号", "弱信号", "极弱信号", "无信号"]:
        total_count = 0
        total_future = 0
        total_accuracy_count = 0
        
        for symbol, stats in all_stats.items():
            if label in stats['score_stats']:
                ss = stats['score_stats'][label]
                total_count += ss['count']
                total_future += ss['avg_future_change'] * ss['count']
                total_accuracy_count += ss['count'] * ss['accuracy'] / 100
        
        if total_count > 0:
            avg_future = total_future / total_count
            avg_accuracy = total_accuracy_count / total_count * 100
            print(f"\n  {label}:")
            print(f"    总信号数: {total_count}")
            print(f"    平均未来24h变化: {avg_future:.2f}%")
            print(f"    平均下跌准确率: {avg_accuracy:.1f}%")
    
    # 保存详细结果
    output_file = '~/demon-coin-detector/unified_system/backtest_results.json'
    save_data = {
        'timestamp': datetime.now().isoformat(),
        'symbols': symbols,
        'stats': all_stats,
    }
    
    with open(output_file, 'w') as f:
        json.dump(save_data, f, indent=2, default=str)
    
    print(f"\n💾 详细结果已保存到: {output_file}")
    
    # 集成建议
    print("\n" + "=" * 60)
    print("🚀 集成建议")
    print("=" * 60)
    
    if total_pattern_signals > 0 and overall_accuracy > 50:
        print("✅ 新评分系统表现良好，建议集成")
        print("\n集成步骤:")
        print("1. 备份当前系统: cp unified_system/modes/short_mode.py unified_system/modes/short_mode_backup.py")
        print("2. 更新导入: from modes.short_mode_v2 import calc_short_score_v2")
        print("3. 小仓位测试: 先用5%仓位验证")
        print("4. 监控日志: 观察模式识别是否准确")
    else:
        print("⚠️ 新评分系统表现一般，建议进一步优化")
        print("\n优化方向:")
        print("1. 调整模式条件阈值")
        print("2. 增加更多特征维度")
        print("3. 优化权重分配")

if __name__ == "__main__":
    main()
