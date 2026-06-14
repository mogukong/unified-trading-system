#!/bin/bash
# 复盘Dashboard启动脚本
# 深色主题 Bootstrap 5 + Chart.js

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🚀 启动复盘Dashboard..."
echo "📊 访问地址: http://localhost:5001"
echo ""

# 检查Flask是否安装
python3 -c "import flask" 2>/dev/null || {
    echo "❌ Flask未安装，正在安装..."
    pip3 install flask --quiet
}

# 初始化数据库并同步数据
python3 -c "
from database import init_db, sync_from_json
init_db()
sync_from_json()
"

echo ""
echo "🌐 启动Web服务器..."
