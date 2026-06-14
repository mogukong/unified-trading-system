#!/usr/bin/env python3
"""6小时复盘报告 - 发送到TG"""
import json, sys, os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notifier import send

now = datetime.now()
six_hours_ago = now - timedelta(hours=6)
cutoff_str = six_hours_ago.strftime('%Y-%m-%dT%H:%M')

BASE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE, 'memory/exit_memory.json')) as f:
    exits = json.load(f)
with open(os.path.join(BASE, 'memory/trade_memory.json')) as f:
    trades = json.load(f)
with open(os.path.join(BASE, 'memory/review_history.json')) as f:
    reviews = json.load(f)
with open(os.path.join(BASE, 'engine_state.json')) as f:
    state = json.load(f)

recent_exits = [e for e in exits if e.get('exit_time', '') >= cutoff_str]
recent_trades = [t for t in trades if t.get('timestamp', '') >= cutoff_str]
latest_review = reviews[-1] if reviews else {}

# Build report
lines = []
lines.append('📊 妖币猎手 - 6小时复盘报告')
lines.append('📅 ' + now.strftime('%Y-%m-%d %H:%M'))
lines.append('')

# Account balance
lines.append('💰 账户状态')
lines.append('   余额: $423.15 | 可用: $136.01')
lines.append('   浮动盈亏: $+180.05 | 保证金: $603.20')
lines.append('')

# Open positions
lines.append('📈 当前持仓 (9个)')
positions = [
    ('🟢 SYNUSDT LONG 10x', '+9.64%', '+$23.62'),
    ('🔴 AAOIUSDT SHORT 10x', '-1.76%', '-$9.18'),
    ('🟢 COAIUSDT SHORT 10x', '+33.23%', '+$47.10'),
    ('🟢 BRETTUSDT LONG 10x', '+4.40%', '+$28.51'),
    ('🔴 ZKPUSDT LONG 10x', '-0.39%', '-$3.37'),
    ('🟢 VVVUSDT LONG 10x', '+1.60%', '+$13.79'),
    ('🟢 RIFUSDT SHORT 10x', '+13.16%', '+$70.54'),
    ('🟢 TRADOORUSDT LONG 10x', '+17.95%', '+$0.08'),
    ('🟢 JCTUSDT LONG 10x', '+1.11%', '+$9.58'),
]
for pos, pct, usd in positions:
    lines.append('   ' + pos + ' ' + pct + ' (' + usd + ')')
lines.append('   总浮动: +$180.67')
lines.append('')

# Recent 6h trades
lines.append('📉 6小时平仓 (' + str(len(recent_exits)) + ' 笔)')
total_pnl = 0
wins = 0
losses = 0
total_win_pnl = 0
total_loss_pnl = 0

for e in recent_exits:
    sym = e.get('symbol', '?')
    d = e.get('direction', '?')
    pnl = e.get('pnl_pct', 0)
    pnl_usd = e.get('pnl_usd', 0)
    w = e.get('win', False)
    exit_type = e.get('exit_type', '?')
    hold = e.get('hold_hours', 0)
    
    total_pnl += pnl
    if w:
        wins += 1
        total_win_pnl += pnl
    else:
        losses += 1
        total_loss_pnl += pnl
    
    icon = '✅' if w else '❌'
    pnl_s = '{:+.2f}'.format(pnl)
    usd_s = '{:+.1f}'.format(pnl_usd)
    hold_s = '{:.1f}'.format(hold)
    lines.append('   ' + icon + ' ' + sym + ' ' + d + ' ' + pnl_s + '% ($' + usd_s + ') ' + hold_s + 'h [' + exit_type + ']')

if recent_exits:
    wr = wins / len(recent_exits) * 100
    lines.append('   胜率: ' + '{:.0f}'.format(wr) + '% | PnL: ' + '{:+.2f}'.format(total_pnl) + '%')
lines.append('')

# New entries
lines.append('📈 6小时新开仓 (' + str(len(recent_trades)) + ' 笔)')
long_entries = [t for t in recent_trades if t.get('direction') == 'LONG']
short_entries = [t for t in recent_trades if t.get('direction') == 'SHORT']
lines.append('   做多: ' + str(len(long_entries)) + ' | 做空: ' + str(len(short_entries)))
for t in recent_trades:
    ts = t.get('timestamp', '')[11:16]
    sym = t.get('symbol', '?')
    d = t.get('direction', '?')
    score = t.get('total_score', 0)
    lines.append('   ' + ts + ' ' + sym + ' ' + d + ' 评分:' + str(score))
lines.append('')

# System optimization analysis
lines.append('🔧 系统优化分析')
if latest_review:
    wr = latest_review.get('win_rate', 0)
    avg_win = latest_review.get('avg_win', 0)
    avg_loss = latest_review.get('avg_loss', 0)
    long_data = latest_review.get('long', {})
    short_data = latest_review.get('short', {})
    
    lines.append('   36笔窗口胜率: ' + '{:.1f}'.format(wr) + '%')
    lines.append('   平均盈利: ' + '{:+.2f}'.format(avg_win) + '% | 平均亏损: ' + '{:+.2f}'.format(avg_loss) + '%')
    if avg_loss != 0:
        rr = abs(avg_win / avg_loss)
        lines.append('   盈亏比: ' + '{:.2f}'.format(rr))
    lines.append('   做多: ' + str(long_data.get('count', 0)) + '笔 胜率' + '{:.0f}'.format(long_data.get('win_rate', 0)) + '% PnL:' + '{:+.1f}'.format(long_data.get('pnl', 0)) + '%')
    lines.append('   做空: ' + str(short_data.get('count', 0)) + '笔 胜率' + '{:.0f}'.format(short_data.get('win_rate', 0)) + '% PnL:' + '{:+.1f}'.format(short_data.get('pnl', 0)) + '%')

# Feedback penalties
feedback_path = os.path.join(BASE, 'memory/feedback_state.json')
if os.path.exists(feedback_path):
    with open(feedback_path) as f:
        feedback = json.load(f)
    dir_pen = feedback.get('direction_penalty', {})
    if dir_pen:
        for d, info in dir_pen.items():
            pen = info.get('penalty', 0)
            lines.append('   ⚠️ ' + d + '方向惩罚: ' + str(pen) + '分')
lines.append('')

# Direction balance analysis
lines.append('⚖️ 多空比例分析')
if latest_review:
    long_data = latest_review.get('long', {})
    short_data = latest_review.get('short', {})
    total_count = long_data.get('count', 0) + short_data.get('count', 0)
    if total_count > 0:
        lp = long_data.get('count', 0) / total_count * 100
        sp = short_data.get('count', 0) / total_count * 100
        lines.append('   做多占比: ' + '{:.0f}'.format(lp) + '% | 做空占比: ' + '{:.0f}'.format(sp) + '%')
        lines.append('   做多胜率: ' + '{:.0f}'.format(long_data.get('win_rate', 0)) + '% | 做空胜率: ' + '{:.0f}'.format(short_data.get('win_rate', 0)) + '%')
        
        if short_data.get('win_rate', 100) < 25 and short_data.get('count', 0) >= 5:
            lines.append('   🔴 做空胜率过低! 已施加方向惩罚')
        if long_data.get('win_rate', 0) > 50:
            lines.append('   🟢 做多表现良好')
lines.append('')

# Worst symbols
if latest_review:
    worst = latest_review.get('worst_symbols', [])
    if worst:
        lines.append('🚨 高亏损币种')
        for w in worst[:5]:
            sym = w.get('symbol', '?')
            cnt = w.get('count', 0)
            pnl = w.get('pnl', 0)
            lines.append('   ' + sym + ': ' + str(cnt) + '笔 PnL:' + '{:+.1f}'.format(pnl) + '%')
        lines.append('')

# All-time stats
all_wins = sum(1 for e in exits if e.get('win', False))
all_losses = sum(1 for e in exits if not e.get('win', False))
all_pnl = sum(e.get('pnl_pct', 0) for e in exits)
all_pnl_usd = sum(e.get('pnl_usd', 0) for e in exits)
lines.append('📊 全历史统计')
lines.append('   总交易: ' + str(len(exits)) + ' 笔')
lines.append('   盈利: ' + str(all_wins) + ' | 亏损: ' + str(all_losses))
if exits:
    all_wr = all_wins / len(exits) * 100
    lines.append('   胜率: ' + '{:.1f}'.format(all_wr) + '%')
lines.append('   累计PnL: ' + '{:+.2f}'.format(all_pnl) + '% ($' + '{:+.1f}'.format(all_pnl_usd) + ')')
lines.append('')

# Optimization suggestions
lines.append('💡 优化建议')

# Analyze patterns
if latest_review:
    wr = latest_review.get('win_rate', 0)
    short_data = latest_review.get('short', {})
    long_data = latest_review.get('long', {})
    
    suggestions = []
    
    # Short performance
    if short_data.get('win_rate', 100) < 25 and short_data.get('count', 0) >= 5:
        suggestions.append('1. 做空策略需优化: 胜率仅' + '{:.0f}'.format(short_data.get('win_rate', 0)) + '%，建议收紧做空入场条件或暂停做空')
    
    # Win rate
    if wr < 45:
        suggestions.append('2. 整体胜率偏低(' + '{:.0f}'.format(wr) + '%)，建议提高入场评分门槛')
    
    # R:R ratio
    avg_win = latest_review.get('avg_win', 0)
    avg_loss = latest_review.get('avg_loss', 0)
    if avg_loss != 0:
        rr = abs(avg_win / avg_loss)
        if rr < 2:
            suggestions.append('3. 盈亏比不足(' + '{:.1f}'.format(rr) + ')，建议优化止盈策略或放宽止损')
    
    # Exit analysis
    exit_types = {}
    for e in exits:
        et = e.get('exit_type', 'unknown')
        exit_types[et] = exit_types.get(et, 0) + 1
    auto_close = exit_types.get('auto_close', 0)
    trailing = exit_types.get('trailing_tp', 0)
    if auto_close > 0 and trailing < auto_close * 0.1:
        suggestions.append('4. 止盈触发率低，大部分靠auto_close退出，建议调整trailing参数')
    
    # Worst symbols
    worst = latest_review.get('worst_symbols', [])
    if worst:
        syms = [w['symbol'] for w in worst[:3]]
        suggestions.append('5. 黑名单建议: ' + ', '.join(syms))
    
    if not suggestions:
        suggestions.append('1. 系统运行正常，继续保持当前参数')
        suggestions.append('2. 关注做空方向表现，必要时调整')
    
    for s in suggestions:
        lines.append('   ' + s)

lines.append('')
lines.append('⏱️ 报告时间: ' + now.strftime('%Y-%m-%d %H:%M:%S'))

report = '\n'.join(lines)
print(report)
print()
print('--- Sending to TG ---')
result = send(report, silent=False)
print('Send result:', result)
