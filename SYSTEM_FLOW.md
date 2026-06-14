# 妖币猎手统一系统 v1.0 - 系统流程与自检报告

## 📋 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        妖币猎手统一系统 v1.0                          │
├─────────────────────────────────────────────────────────────────────┤
│  双模式(做多+做空) + 四层记忆 + 推送中心 + 三级扫描                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                          文件结构                                      │
├─────────────────────────────────────────────────────────────────────┤
│  unified_engine.py      # 主引擎 (扫描/持仓管理/平仓检测)              │
│  notifier.py            # TG推送中心                                  │
│  config.json            # 配置文件                                    │
│  .env                   # API密钥                                     │
│  engine_state.json      # 运行状态                                    │
│  engine_log.txt         # 运行日志                                    │
│                                                                      │
│  modes/                                                              │
│  ├── long_mode.py       # 做多评分 (OI/价格四象限)                     │
│  ├── short_mode.py      # 做空评分 (六维评分)                          │
│  └── oi_flow_analyzer.py # OI资金流分析                               │
│                                                                      │
│  memory/                                                             │
│  ├── trade_memory.py    # L1: 开仓记忆                                │
│  ├── exit_memory.py     # L2: 平仓记忆                                │
│  ├── review_memory.py   # L3: 复盘记忆                                │
│  ├── feedback_engine.py # L4: 反馈引擎                                │
│  └── trades.db          # SQLite交易记录                              │
└─────────────────────────────────────────────────────────────────────┘
```

## 🔄 主循环流程

```
┌─────────────────────────────────────────────────────────────────────┐
│                          主循环 (60秒/次)                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  1. 获取状态                                                         │
│     ├── get_balance()     → 余额/可用/未实现盈亏                      │
│     └── get_positions()   → 当前持仓列表                              │
│                                                                      │
│  2. 平仓检测                                                         │
│     └── detect_closed_positions()                                    │
│         ├── 对比 last_positions vs current_positions                  │
│         ├── 获取 Binance REALIZED_PNL                                │
│         ├── 获取 userTrades 计算出场价                                │
│         └── record_exit() → exit_memory.json                         │
│                                                                      │
│  3. 持仓管理                                                         │
│     └── manage_positions()                                           │
│         ├── 分批止盈: pnl >= 40% → 卖50%                             │
│         ├── 追踪止盈: pnl >= 10% → 峰值回撤15%触发                    │
│         └── 止损: pnl <= -6% → 全部平仓                              │
│                                                                      │
│  4. 三级扫描 (做多/做空分别执行)                                       │
│     ├── Tier1 (30分钟): Top300 → Top100 (快速模式, 2 API/币)         │
│     ├── Tier2 (20分钟): Top100 → Top50  (快速模式, 2 API/币)         │
│     └── Tier3 (10分钟): Top50  → Top5   (完整模式, 7 API/币)         │
│                                                                      │
│  5. 开仓决策                                                         │
│     └── if top5 and best.score >= entry_score:                       │
│         ├── open_position() → 市价开仓                               │
│         ├── record_entry() → trade_memory.json                       │
│         └── place_stop_loss() → Algo Order API                      │
│                                                                      │
│  6. 定期任务                                                         │
│     ├── 每30分钟: TG持仓报告                                         │
│     ├── 每1小时: 复盘 + 反馈                                         │
│     └── 每1小时: 交叉验证                                            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## 📊 评分系统

### 做多评分 (满分85分)
```
价格动量 (25分): 4h/1h涨幅 + EMA20
OI资金流 (25分): OI变化 + OI/价格四象限
成交量   (15分): 量比 + Taker买入比
资金费率 (10分): 费率方向
多空比   (10分): 散户多头比例
```

### 做空评分 (满分100分)
```
价格弱势 (30分): 24h跌幅 + 4h动量 + EMA20
EMA趋势 (15分): 连续阴线 + 价格<EMA20
OI背离   (20分): 价格跌+OI增 = 新空开仓
资金费率 (15分): 负费率 = 空头情绪
多空比   (10分): 散户多头>65% = 可被收割
成交量   (10分): Taker卖出>55%
```

### 最终评分公式
```
最终分 = 基础评分 × 模式权重(1.0-1.3) + OI质量加分(0-30) + 记忆反馈(±5)
```

## 🎯 入场阈值
```
做多: 90分 (回测平均108.6分, 5/5≥70)
做空: 30分 (回测平均20.4分, 0/5≥70)
```

## ⚠️ 发现的问题

### 🔴 严重问题

#### 1. 平仓重复检测 (每次重启)
**现象**: exit_memory.json 有30条记录，其中 EDGEUSDT_SHORT 重复7次，CATIUSDT_SHORT 重复7次
**原因**: 系统每次重启都会重新检测已平仓的仓位，但 trade_memory 中的记录没有被清除
**影响**: 
- exit_memory 膨胀
- 复盘数据不准确
- 每次重启浪费90秒处理重复平仓

**修复方案**:
```python
# 在 detect_closed_positions 中，平仓后清除 trade_memory 中的记录
def detect_closed_positions(current_positions):
    ...
    for key in closed_keys:
        # 记录平仓后，从 trade_memory 中移除
        remove_trade_from_memory(key)
```

#### 2. 三级扫描计时器混乱
**现象**: Tier1/Tier2/Tier3 的计时器是全局的，不是按 mode 分开的
**原因**: `last_tier1_time` 在 `for mode in ["long", "short"]` 循环中被更新
**影响**: 
- 做多扫描后更新计时器，做空扫描也会更新同一个计时器
- 可能导致做多和做空同时触发扫描

**修复方案**:
```python
# 按 mode 分开计时器
last_tier1_time = {"long": 0, "short": 0}
last_tier2_time = {"long": 0, "short": 0}
last_tier3_time = {"long": 0, "short": 0}
```

#### 3. Tier2/Tier3 依赖问题
**现象**: Tier2 依赖 Tier1 的结果，Tier3 依赖 Tier2 的结果，但计时器是独立的
**原因**: Tier2 可能在 Tier1 完成前就触发了
**影响**: 
- Tier2 使用旧的 Tier1 结果
- 可能导致扫描结果不准确

**修复方案**:
```python
# Tier2 只在 Tier1 完成后触发
if now - last_tier1_time[mode] >= tier1_interval:
    scanner.tier1_results[mode] = scanner.scan_tier1(mode, config)
    last_tier1_time[mode] = now

# Tier2 使用 Tier1 的结果，但有自己的计时器
if now - last_tier2_time[mode] >= tier2_interval and scanner.tier1_results[mode]:
    scanner.tier2_results[mode] = scanner.scan_tier2(mode, config, scanner.tier1_results[mode])
    last_tier2_time[mode] = now
```

### 🟡 中等问题

#### 4. trade_memory 与交易所不同步
**现象**: trade_memory 显示0个未平仓，但交易所有7个持仓
**原因**: trade_memory 中的记录没有被正确更新
**影响**: 
- 启动时无法正确加载历史持仓
- 平仓检测可能遗漏

**修复方案**:
```python
# 启动时从交易所获取真实持仓，同步到 trade_memory
def sync_positions_with_exchange():
    exchange_positions = get_positions()
    trade_mem = load_trades()
    
    # 更新 trade_memory 中的持仓状态
    for t in trade_mem:
        if t.get("status") == "open":
            # 检查是否还在交易所
            if not any(p["symbol"] == t["symbol"] and p["direction"] == t["direction"] 
                      for p in exchange_positions):
                t["status"] = "closed"
    
    # 添加交易所中有但 trade_memory 中没有的持仓
    for p in exchange_positions:
        if not any(t["symbol"] == p["symbol"] and t["direction"] == p["direction"] 
                  for t in trade_mem if t.get("status") == "open"):
            # 添加到 trade_memory
            ...
```

#### 5. 快速模式缺少数据
**现象**: fetch_symbol_data(quick=True) 只获取 1h K线和 OI
**原因**: calculate_score 函数可能需要 funding、top_ls 等数据
**影响**: 
- 快速模式下评分可能不准确
- 可能漏掉一些信号

**修复方案**:
```python
# 在 calculate_score 中处理缺失数据
def calculate_score(sym, mode, kline_data, funding_data, oi_data, ls_data, taker_data):
    # 如果缺少关键数据，使用默认值
    if not funding_data:
        funding_data = {"rate": 0}
    if not ls_data:
        ls_data = {"long_ratio": 50}
    if not taker_data:
        taker_data = {"volume_ratio": 1}
    ...
```

#### 6. 止盈止损重复触发
**现象**: manage_positions 在每次循环都运行，可能重复触发止盈止损
**原因**: 没有记录已触发的止盈止损
**影响**: 
- 可能重复平仓
- 通知重复发送

**修复方案**:
```python
# 记录已触发的止盈止损
triggered_tp = set()
triggered_sl = set()

def manage_positions(positions, config):
    ...
    for pos in positions:
        pos_key = f"{pos['symbol']}_{pos['direction']}"
        
        # 分批止盈
        if pnl_pct >= cfg.get("tp1_pct", 0.40) and pos_key not in triggered_tp:
            triggered_tp.add(pos_key)
            ...
        
        # 止损
        if pnl_pct <= -cfg.get("stop_loss", 0.06) and pos_key not in triggered_sl:
            triggered_sl.add(pos_key)
            ...
```

### 🟢 轻微问题

#### 7. 日志文件过大
**现象**: engine_log.txt 持续增长，没有轮转
**原因**: 没有日志轮转机制
**影响**: 
- 磁盘空间占用
- 日志查询变慢

**修复方案**:
```python
# 添加日志轮转
import logging
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
```

#### 8. 配置文件路径硬编码
**现象**: 配置文件路径在代码中硬编码
**原因**: 没有使用环境变量或命令行参数
**影响**: 
- 不便于部署
- 不便于测试

**修复方案**:
```python
# 使用环境变量或命令行参数
CONFIG_FILE = os.getenv("CONFIG_FILE", os.path.join(BASE_DIR, "config.json"))
```

## 📈 性能分析

### API 调用统计
```
Tier1 (300币 × 2 API): 600 API/30分钟 = 20 API/分钟
Tier2 (100币 × 2 API): 200 API/20分钟 = 10 API/分钟
Tier3 (50币 × 7 API):  350 API/10分钟 = 35 API/分钟
持仓管理 (7币 × 1 API): 7 API/分钟
总计: ~72 API/分钟
```

### 预计耗时
```
Tier1: 300币 × 2 API × 0.5秒/300ms延迟 = ~5分钟
Tier2: 100币 × 2 API × 0.5秒/300ms延迟 = ~2分钟
Tier3: 50币 × 7 API × 0.5秒/300ms延迟 = ~3分钟
总计: ~10分钟/轮
```

## 🔧 建议修复优先级

1. **🔴 严重**: 平仓重复检测 → 立即修复
2. **🔴 严重**: 三级扫描计时器混乱 → 立即修复
3. **🟡 中等**: trade_memory 与交易所不同步 → 尽快修复
4. **🟡 中等**: 快速模式缺少数据 → 尽快修复
5. **🟡 中等**: 止盈止损重复触发 → 尽快修复
6. **🟢 轻微**: 日志文件过大 → 计划修复
7. **🟢 轻微**: 配置文件路径硬编码 → 计划修复
