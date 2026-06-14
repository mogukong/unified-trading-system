# Unified Trading System v3.2

A sophisticated Binance Futures automated trading system with multi-dimensional analysis and intelligent position management.

## 🚀 Features

- **Multi-Dimensional Scoring**: 8+ dimensions for comprehensive signal analysis
- **OI Flow Detection**: Smart money tracking and phase identification
- **Trend Confirmation**: EMA + MACD + Bollinger + Volume resonance
- **Smart Position Management**: Trailing TP, position replacement, risk limits
- **Real-time Monitoring**: Telegram notifications and automated reviews

## 📦 Quick Start

```bash
# Clone repository
git clone https://github.com/mogukong/unified-trading-system.git
cd unified-trading-system

# Install dependencies
bash install.sh

# Configure API keys
cp .env.example .env
nano .env

# Start system
python3 unified_engine.py --loop
```

## ⚙️ Configuration

Edit `.env` file with your API keys:

```env
BINANCE_API_KEY=your_binance_api_key
BINANCE_API_SECRET=your_binance_api_secret
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TG_CHAT_ID=your_telegram_chat_id
TG_PROXY=http://127.0.0.1:1080
```

## 📊 System Workflow

### 1. Data Collection
```
┌─────────────────┐
│  Binance API    │
│  - Klines (1h)  │
│  - OI History   │
│  - Funding Rate │
│  - Long/Short   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Data Pipeline  │
│  - Clean        │
│  - Normalize    │
│  - Calculate    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Indicators     │
│  - EMA (7/20/50)│
│  - MACD         │
│  - RSI          │
│  - Bollinger    │
│  - Volume       │
└─────────────────┘
```

### 2. Signal Scanning
```
┌─────────────────────────────────────┐
│         Three-Tier Scanning         │
├─────────────────────────────────────┤
│  Tier 1 (15min)                     │
│  └─ Top 300 → Top 100              │
│                                     │
│  Tier 2 (10min)                     │
│  └─ Top 100 → Top 50               │
│                                     │
│  Tier 3 (5min)                      │
│  └─ Top 50 → Top 5                 │
│                                     │
│  Burst Detection (5min)             │
│  └─ 24h Ticker Analysis            │
│                                     │
│  Watchlist Rescan (5min)            │
│  └─ Previously Detected Coins      │
└─────────────────────────────────────┘
```

### 3. Scoring System
```
┌─────────────────────────────────────┐
│         Long Mode (100pts)          │
├─────────────────────────────────────┤
│  Price Momentum        │  20pts    │
│  OI Flow               │  25pts    │
│  Volume                │  12pts    │
│  Funding Rate          │  10pts    │
│  Long/Short Ratio      │  10pts    │
│  RSI                   │   8pts    │
│  EMA Multi-timeframe   │  ±10pts   │
│  MACD                  │  ±10pts   │
│  Bollinger Bands       │   ±8pts   │
│  Volume Pattern        │   ±8pts   │
│  Support/Resistance    │   ±8pts   │
│  Consolidation Box     │   ±5pts   │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│        Short Mode (100pts)          │
├─────────────────────────────────────┤
│  Price Weakness        │  25pts    │
│  EMA Trend             │  12pts    │
│  OI Divergence         │  20pts    │
│  Funding Rate          │  15pts    │
│  Long/Short Ratio      │  10pts    │
│  RSI                   │  10pts    │
│  Volume                │  18pts    │
│  MACD                  │  ±10pts   │
│  Bollinger Bands       │   ±8pts   │
│  Volume Pattern        │   ±8pts   │
│  Support/Resistance    │   ±8pts   │
│  Consolidation Box     │   ±5pts   │
└─────────────────────────────────────┘
```

### 4. Advanced Analysis
```
┌─────────────────────────────────────┐
│    Pullback vs Reversal Analysis    │
├─────────────────────────────────────┤
│  Dimension 1: OI + Price Combo      │
│  - OI↑ + Price↓ = New Capital Dip  │
│  - OI↓ + Price↓ = Profit Taking    │
│                                     │
│  Dimension 2: Funding Rate Cost     │
│  - Negative = High Short Cost       │
│  - Positive = High Long Cost        │
│                                     │
│  Dimension 3: Whale Positioning     │
│  - Whale Long > 60% = Bullish      │
│  - Whale Short > 60% = Bearish     │
│                                     │
│  Dimension 4: Retail Sentiment      │
│  - Retail > 2.0 = Overheated       │
│  - Retail < 0.5 = Oversold         │
│                                     │
│  Dimension 5: Volume Panic          │
│  - High Volume + Drop = Panic       │
│  - Low Volume + Drop = Correction   │
└─────────────────────────────────────┘
```

### 5. Position Management
```
┌─────────────────────────────────────┐
│         Position Lifecycle          │
├─────────────────────────────────────┤
│  Entry                              │
│  ├─ Score ≥ 70 (Long)              │
│  ├─ Score ≥ 60 (Short)             │
│  ├─ Physical Stop-Loss (8%)        │
│  └─ Position Size: 20%             │
│                                     │
│  Monitoring                         │
│  ├─ Real-time PnL Tracking         │
│  ├─ Peak PnL Recording             │
│  └─ Trailing Stop Activation       │
│                                     │
│  Exit                               │
│  ├─ Take Profit 1: +40% (50% sell) │
│  ├─ Trailing Stop: Peak -15%       │
│  ├─ Stop-Loss Hit: -8%             │
│  └─ Score Drop: < 55               │
│                                     │
│  Replacement                        │
│  ├─ Weak Position Detected         │
│  ├─ Stronger Signal Found          │
│  └─ Auto Replace if Enabled        │
└─────────────────────────────────────┘
```

### 6. Risk Management
```
┌─────────────────────────────────────┐
│         Risk Controls               │
├─────────────────────────────────────┤
│  Position Limits                    │
│  ├─ Max 4 Long Positions           │
│  ├─ Max 4 Short Positions          │
│  └─ 20% per Position               │
│                                     │
│  Loss Limits                        │
│  ├─ Stop-Loss: 8% per Trade        │
│  ├─ Daily Loss: 100% (configurable)│
│  └─ Max Drawdown: 50%              │
│                                     │
│  Cooldown                           │
│  ├─ Same Coin: 2 hours             │
│  ├─ Consecutive Loss: 2 times      │
│  └─ Price Protection: 4h > 20%     │
│                                     │
│  Emergency                          │
│  ├─ Stop-Loss Fail → Close         │
│  ├─ API Error → Retry 3x           │
│  └─ Manual Override Available      │
└─────────────────────────────────────┘
```

### 7. Notification System
```
┌─────────────────────────────────────┐
│         Telegram Notifications      │
├─────────────────────────────────────┤
│  Trade Events                       │
│  ├─ 🟢 Position Opened             │
│  ├─ 🔴 Position Closed             │
│  ├─ 💰 Take Profit Hit             │
│  └─ 🛑 Stop-Loss Hit               │
│                                     │
│  Periodic Reports                   │
│  ├─ 📊 30-min Status Update        │
│  ├─ 📈 Daily Summary               │
│  └─ 🔍 Weekly Review               │
│                                     │
│  Alerts                             │
│  ├─ ⚠️ Risk Warning                │
│  ├─ 🚨 Emergency Alert             │
│  └─ 📢 Signal Detected             │
└─────────────────────────────────────┘
```

### 8. Automated Review
```
┌─────────────────────────────────────┐
│         Review System               │
├─────────────────────────────────────┤
│  6-Hour Review                      │
│  ├─ Trade Performance              │
│  ├─ Win Rate Analysis              │
│  ├─ PnL Breakdown                  │
│  └─ Strategy Optimization          │
│                                     │
│  Daily Review (23:00)               │
│  ├─ Full Day Summary               │
│  ├─ Top Performers                 │
│  ├─ Worst Performers               │
│  └─ Next Day Strategy              │
│                                     │
│  Health Check                       │
│  ├─ System Status                  │
│  ├─ API Connection                 │
│  ├─ Memory Usage                   │
│  └─ Error Rate                     │
└─────────────────────────────────────┘
```

## 📈 Performance Metrics

Based on 7-day backtesting:

| Metric | Value |
|--------|-------|
| Long Win Rate | 28% |
| Short Win Rate | 18% |
| Best Exit Type | Trailing TP (+48U) |
| Worst Exit Type | Auto Close (-655U) |
| Optimal Hold Time | >12 hours |
| Average Win | +10.65U |
| Average Loss | -7.21U |

## 🛡️ Risk Management

- **Physical Stop-Loss**: Via Binance Algo Orders
- **Trailing Take-Profit**: Configurable activation and drawdown
- **Position Replacement**: Auto-replace weak performers
- **Daily Loss Limit**: Configurable (default 100%)
- **Cooldown System**: 2-hour cooldown after consecutive losses

## 📖 Documentation

- [SKILL.md](SKILL.md) - Complete system documentation
- [SYSTEM_FLOW.md](SYSTEM_FLOW.md) - System architecture
- [config.json](config.json) - Configuration parameters

## ⚠️ Risk Warning

**This is a high-risk trading system. Use at your own risk.**

- Past performance does not guarantee future results
- Always use proper risk management
- Never invest more than you can afford to lose
- Test thoroughly on paper trading first
- Monitor the system regularly

## 📄 License

MIT License

---

**Version**: 3.2.0 | **Last Updated**: 2026-06-14
