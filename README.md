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
BINANCE_API_KEY=your_k...n## 📊 Performance

Based on 7-day backtesting:
- **Long Win Rate**: 28%
- **Short Win Rate**: 18%
- **Best Exit Type**: Trailing TP (+48U)
- **Optimal Hold Time**: >12 hours

## 🛡️ Risk Management

- Physical stop-loss via Algo Orders
- Trailing take-profit with configurable parameters
- Position replacement for weak performers
- Daily loss limit protection

## 📖 Documentation

- [SKILL.md](SKILL.md) - Complete system documentation
- [SYSTEM_FLOW.md](SYSTEM_FLOW.md) - System architecture
- [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md) - Integration guide

## ⚠️ Risk Warning

**This is a high-risk trading system. Use at your own risk.**

- Past performance does not guarantee future results
- Always use proper risk management
- Never invest more than you can afford to lose
- Test thoroughly on paper trading first

## 📄 License

MIT License

---

**Version**: 3.2.0 | **Last Updated**: 2026-06-14
