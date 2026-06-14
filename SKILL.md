---
name: unified-trading-system
description: "Binance Futures automated trading system with multi-dimensional analysis, OI flow detection, and smart position management"
version: "3.2.0"
author: mogukong
license: MIT
platforms: [linux, macos]
tags: [trading, binance, futures, quantitative, crypto]
---

# Unified Trading System v3.2

A sophisticated Binance Futures trading system featuring multi-dimensional signal analysis, OI flow detection, and intelligent position management.

## Features

### Multi-Dimensional Scoring
- **Long Mode**: 8 dimensions, 100-point scale
- **Short Mode**: 6 dimensions + pattern detection
- **Trend Confirmation**: EMA + MACD + Bollinger + Volume resonance

### Smart Detection
- **Pullback vs Reversal Analysis**: 5 dimensions to distinguish corrections from trend changes
- **Uptrend Quality Analysis**: 5 dimensions to assess rally health
- **Startup Pattern Detection**: Washout recovery, silent accumulation, silent start

### Risk Management
- Physical stop-loss via Algo Orders
- Trailing take-profit with configurable parameters
- Position replacement for weak performers
- Daily loss limit protection

### Monitoring
- Real-time Telegram notifications
- 6-hour automated review
- Health check system
- Performance tracking

## Quick Start

### 1. Clone Repository
```bash
git clone https://github.com/mogukong/unified-trading-system.git
cd unified-trading-system
```

### 2. Install Dependencies
```bash
bash install.sh
```

### 3. Configure API Keys
Edit `.env` file:
```env
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here
TELEGRAM_BOT_TOKEN=your_bot_token
TG_CHAT_ID=your_chat_id
```

### 4. Start System
```bash
python3 unified_engine.py --loop
```

## Configuration

### Trading Parameters (config.json)
```json
{
  "modes": {
    "long": {
      "leverage": 10,
      "max_positions": 4,
      "position_pct": 0.2,
      "stop_loss": 0.08,
      "entry_score": 70
    },
    "short": {
      "leverage": 10,
      "max_positions": 4,
      "position_pct": 0.2,
      "stop_loss": 0.08,
      "entry_score": 60
    }
  }
}
```

### Risk Management
- **Stop Loss**: 8% (configurable)
- **Take Profit**: 40% first batch, then trailing
- **Max Positions**: 4 per direction
- **Daily Loss Limit**: 100% (configurable)

## Architecture

### Core Components
1. **unified_engine.py** - Main engine with scanning, scoring, and execution
2. **modes/long_mode.py** - Long position scoring (8 dimensions)
3. **modes/short_mode.py** - Short position scoring (6 dimensions)
4. **modes/oi_flow_analyzer.py** - OI flow analysis and phase detection
5. **notifier.py** - Telegram notification system

### Scoring Dimensions

#### Long Mode (100 points)
1. Price Momentum (20)
2. OI Flow (25)
3. Volume (12)
4. Funding Rate (10)
5. Long/Short Ratio (10)
6. RSI (8)
7. EMA Multi-timeframe (±10)
8. MACD (±10)
9. Bollinger Bands (±8)
10. Volume Pattern (±8)
11. Support/Resistance (±8)
12. Consolidation Box (±5)

#### Short Mode (100 points)
1. Price Weakness (25)
2. EMA Trend (12)
3. OI Divergence (20)
4. Funding Rate (15)
5. Long/Short Ratio (10)
6. RSI (10)
7. Volume (18)
8. MACD (±10)
9. Bollinger Bands (±8)
10. Volume Pattern (±8)
11. Support/Resistance (±8)
12. Consolidation Box (±5)

## Advanced Features

### Pullback vs Reversal Analysis
Analyzes 5 dimensions to determine if a price drop is a correction or trend reversal:
1. OI + Price combination
2. Funding rate cost
3. Whale positioning
4. Retail sentiment
5. Volume panic level

### Uptrend Quality Analysis
Evaluates rally health across 5 dimensions:
1. OI + Price combination
2. Funding rate cost
3. Whale positioning
4. Retail sentiment
5. Volume confirmation

### Startup Pattern Detection
Identifies three types of startup patterns:
1. **Washout Recovery**: Sharp drop + stabilization + volume increase
2. **Silent Accumulation**: Low volatility + shrinking volume + breakout
3. **Silent Start**: Low volume but continuous price increase

## Monitoring

### Telegram Notifications
- Trade open/close notifications
- Periodic status reports
- Review summaries
- Health check alerts

### Automated Review
- 6-hour automated review (cron job)
- Daily summary at 23:00
- Performance tracking
- Strategy optimization suggestions

## Deployment

### As a Service (macOS)
```bash
# Create launchd plist
cat > ~/Library/LaunchAgents/com.unified-trading.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.unified-trading</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/unified_engine.py</string>
        <string>--loop</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
EOF

# Load service
launchctl load ~/Library/LaunchAgents/com.unified-trading.plist
```

### As a Service (Linux)
```bash
# Create systemd service
sudo cat > /etc/systemd/system/unified-trading.service << 'EOF'
[Unit]
Description=Unified Trading System
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/unified-trading-system
ExecStart=/usr/bin/python3 unified_engine.py --loop
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
sudo systemctl enable unified-trading
sudo systemctl start unified-trading
```

## Performance Metrics

Based on 7-day backtesting:
- **Long Win Rate**: 28%
- **Short Win Rate**: 18%
- **Best Exit Type**: Trailing TP (+48U)
- **Worst Exit Type**: Auto Close (-655U)
- **Optimal Hold Time**: >12 hours

## Risk Warnings

⚠️ **This is a high-risk trading system. Use at your own risk.**

- Past performance does not guarantee future results
- Always use proper risk management
- Never invest more than you can afford to lose
- Test thoroughly on paper trading first
- Monitor the system regularly

## Support

- GitHub: https://github.com/mogukong/unified-trading-system
- Issues: Report bugs via GitHub Issues

## License

MIT License - See LICENSE file for details

---

**Version History**

- **v3.2** (2026-06-14): Added pullback vs reversal analysis, uptrend quality analysis, startup pattern detection
- **v3.1** (2026-06-12): Added trend confirmation, multi-dimensional scoring
- **v3.0** (2026-06-10): Initial unified system release
