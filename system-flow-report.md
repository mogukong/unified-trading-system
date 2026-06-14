# 妖币统一系统 — 完整流程与优化架构

> 更新时间: 2026-06-09
> 状态: 运行中
> 版本: 三级并行扫描 + 四层记忆系统 + 双模式(做多/做空)

## 一、系统架构总览

主循环 (10秒间隔)
├── Step 1: 获取余额 + 仓位
├── Step 2: 检测平仓 (detect_closed_positions)
│   ├── /fapi/v1/income + /fapi/v1/userTrades
│   ├── L1 trade_memory 查 entry_price/hold_hours
│   ├── L2 auto_tag_loss(market_context) 标签
│   └── record_exit → exit_memory.json
├── Step 3: 持仓管理 (manage_positions)
│   ├── 部分止盈 (+40% → 平50%)
│   ├── 追踪止盈 (peak回撤>15% → 全平)
│   ├── 止损 (pnl < -stop_loss → 全平)
│   └── 每次平仓: L1 factors → L2 market_context → record
├── Step 4: 三级并行扫描 (LONG/SHORT 各自独立执行)
│   └── for mode in [long, short]:
│       ├── Tier1 (每30min): ticker预筛选 + fetch_batch(10线程)
│       ├── Tier2 (每20min): fetch_batch(10线程) 精细评分
│       └── Tier3 (每10min): fetch_batch(10线程) 完整评分 → 开仓
├── Step 5: 定期报告 (每30min)
└── Step 6: 定期复盘 (每小时)

## 二、三级并行扫描详细流程

### Tier1 (每30分钟)
1. 1次API: /fapi/v1/ticker/24hr (全部663个USDT币)
2. pre_filter_by_ticker():
   LONG: 成交量>10万U + 涨幅>0% → ~80个
   SHORT: 成交量>10万U + 跌幅<0% → ~60个
3. fetch_batch(10线程, quick=True): 2 API/币 (klines+OI)
4. calculate_score() → 按分数排序 → Top50

### Tier2 (每20分钟)
1. 输入: Tier1的Top50
2. fetch_batch(10线程, quick=True): 2 API/币
3. 精细评分排序 → Top50

### Tier3 (每10分钟)
1. 输入: Tier2的Top50
2. fetch_batch(10线程, quick=False): 7 API/币 (完整数据)
3. 完整评分: 基础+OI质量+L4反馈
4. 最高分 ≥ 阈值 → open_position()
5. record_entry() → trade_memory.json
6. notify_scan_report() → TG

### 并发架构
import concurrent.futures
max_workers = 10 (ThreadPoolExecutor)
quick=True: 2 API/币 (klines+OI)
quick=False: 7 API/币 (klines+OI+funding+LS+taker+ticker+OI历史)

### 性能对比
Tier1: 串行20min → 并行8min (2.5x)
Tier2: 串行7min → 并行3min (2.3x)
Tier3: 串行10min → 并行5min (2x)
总耗时: 37min → 16min (2.3x)

## 三、四层记忆系统数据流

开仓 → L1 record_entry() → trade_memory.json
  记录: 25个参数 (symbol/direction/price/score/factors/estimated_ev等)

平仓 → L2 record_exit() → exit_memory.json
  记录: entry/exit/pnl/hold_hours/market_context
  auto_tag_loss(): 15种标签 (速度/模式/市场/覆盖)

复盘 → L3 run_review() → review_history.json (每小时)
  36笔滚动窗口: 胜率/盈亏/最差币种/动作建议

反馈 → L4 apply_review_feedback() → feedback_state.json
  adjust_score(): 币种扣分/方向扣分/防守模式/覆盖门槛

## 四、评分系统

做多 (满分~150分, 入场≥70):
  基础85分(OI四象限25+共振20+量15+动量10+资金10)
  + 模式权重(×1.0~1.3) + OI质量(0~30) + 连续上涨(+10) + L4反馈(±5)

做空 (满分~175分, 入场≥25):
  基础100分(弱势25+OI背离20+费率15+拥挤20+成交10+流动10)
  + 模式权重(×1.0~1.3) + OI质量(0~30) + OI暴增(+15) + 恐慌(+15) + L4反馈(±5)

## 五、优化历史
v1.0 (06-05): 初始串行扫描
v1.1 (06-06): 三级扫描(Tier1/2/3)
v2.0 (06-07): 并发优化(10线程+预筛选)
v2.1 (06-08): 做多v2.0(OI四象限) + 做空v2.0(4形态)
v2.2 (06-09): 四层记忆系统修复 + 标签系统完善
