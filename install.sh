#!/bin/bash
# Unified Trading System - One-Click Deploy Script
# Version: v3.2 (2026-06-14)

set -e

echo "🚀 Unified Trading System Installer"
echo "===================================="

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found. Please install Python 3.8+"
    exit 1
fi

echo "✅ Python3 found: $(python3 --version)"

# Install dependencies
echo "📦 Installing dependencies..."
pip3 install requests python-dotenv --quiet

# Create .env if not exists
if [ ! -f .env ]; then
    echo "📝 Creating .env from template..."
    cp .env.example .env
    echo ""
    echo "⚠️  Please edit .env file with your API keys:"
    echo "   - BINANCE_API_KEY"
    echo "   - BINANCE_API_SECRET"
    echo "   - TELEGRAM_BOT_TOKEN"
    echo "   - TG_CHAT_ID"
    echo ""
    read -p "Press Enter after configuring .env..."
fi

# Create necessary directories
mkdir -p memory
mkdir -p logs

# Validate configuration
echo "🔍 Validating configuration..."
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()

required = ['BINANCE_API_KEY', 'BINANCE_API_SECRET']
missing = [k for k in required if not os.getenv(k)]

if missing:
    print(f'❌ Missing required env vars: {missing}')
    exit(1)
else:
    print('✅ Configuration valid')
"

echo ""
echo "✅ Installation complete!"
echo ""
echo "🚀 To start the system:"
echo "   python3 unified_engine.py --loop"
echo ""
echo "📊 To check status:"
echo "   python3 unified_engine.py --status"
echo ""
echo "📖 Documentation: SYSTEM_FLOW.md"
