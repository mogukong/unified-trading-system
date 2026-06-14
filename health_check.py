#!/usr/bin/env python3
"""
健康检查脚本 - 每小时运行一次
1. 检查 unified_engine 进程是否在运行
2. 如果未运行，通过 launchctl kickstart 重启
3. 检查最近1小时的引擎日志是否有错误
4. 检查是否有持仓超过24小时
5. 发现问题发送 TG 警报
"""
import subprocess
import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "engine_log.txt"
TRADE_MEMORY = BASE_DIR / "memory" / "trade_memory.json"
EXIT_MEMORY = BASE_DIR / "memory" / "exit_history.json"
LAUNCHD_LABEL = "com.miboy.unified-engine"


def check_process_running():
    """检查 unified_engine 进程是否在运行"""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if "unified_engine" in line and "python" in line.lower() and "health_check" not in line:
                return True
        return False
    except Exception as e:
        print(f"[WARN] ps check failed: {e}")
        return False


def restart_engine():
    """通过 launchctl kickstart 重启引擎"""
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
            capture_output=True, text=True, timeout=15
        )
        print(f"[INFO] kickstart result: {result.stdout.strip()}")
        return result.returncode == 0
    except Exception as e:
        print(f"[ERROR] kickstart failed: {e}")
        return False


def check_log_errors():
    """检查最近1小时的日志是否有错误"""
    errors = []
    if not LOG_FILE.exists():
        return errors

    cutoff = datetime.now() - timedelta(hours=1)

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 从后往前检查，找到1小时前的日志就停止
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue

            # 解析时间戳 [2026-06-08 16:18:08]
            if line.startswith("[") and "]" in line:
                ts_str = line[1:line.index("]")]
                try:
                    log_time = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    if log_time < cutoff:
                        break
                except ValueError:
                    continue

            # 检查错误关键词
            error_keywords = ["❌", "ERROR", "错误", "失败", "异常", "Traceback"]
            for kw in error_keywords:
                if kw in line:
                    errors.append(line)
                    break

    except Exception as e:
        print(f"[WARN] log check failed: {e}")

    return errors


def check_stale_positions():
    """检查是否有持仓超过24小时"""
    stale = []
    if not TRADE_MEMORY.exists():
        return stale

    try:
        with open(TRADE_MEMORY, "r", encoding="utf-8") as f:
            trades = json.load(f)

        # 加载已平仓记录
        exited_ids = set()
        if EXIT_MEMORY.exists():
            with open(EXIT_MEMORY, "r", encoding="utf-8") as f:
                exits = json.load(f)
                for ex in exits:
                    exited_ids.add(ex.get("trade_id", ""))

        cutoff = datetime.now() - timedelta(hours=24)

        for trade in trades:
            tid = trade.get("trade_id", "")
            if tid in exited_ids:
                continue

            ts_str = trade.get("timestamp", "")
            if not ts_str:
                continue

            try:
                open_time = datetime.fromisoformat(ts_str)
                if open_time < cutoff:
                    symbol = trade.get("symbol", "?")
                    direction = trade.get("direction", "?")
                    hours = (datetime.now() - open_time).total_seconds() / 3600
                    stale.append(f"{symbol} {direction} ({hours:.0f}h)")
            except ValueError:
                continue

    except Exception as e:
        print(f"[WARN] position check failed: {e}")

    return stale


def send_alert(message):
    """发送 TG 警报"""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from notifier import send
        send(f"⚠️ 健康检查警报\n\n{message}")
        print(f"[INFO] TG alert sent")
    except Exception as e:
        print(f"[ERROR] TG send failed: {e}")


def main():
    issues = []

    # 1. 检查进程
    if check_process_running():
        print("[OK] unified_engine is running")
    else:
        print("[WARN] unified_engine is NOT running, restarting...")
        if restart_engine():
            issues.append("🔧 引擎进程已停止，已自动重启")
        else:
            issues.append("🚨 引擎进程已停止，重启失败!")

    # 2. 检查日志错误
    errors = check_log_errors()
    if errors:
        print(f"[WARN] Found {len(errors)} errors in last hour")
        issues.append(f"📝 最近1小时有 {len(errors)} 条错误")
        # 只附加前3条
        for err in errors[:3]:
            # 截取关键信息
            if "]" in err:
                err_short = err[err.index("]") + 1:].strip()[:80]
            else:
                err_short = err[:80]
            issues.append(f"  - {err_short}")
    else:
        print("[OK] No errors in last hour")

    # 3. 检查超时持仓
    stale = check_stale_positions()
    if stale:
        print(f"[WARN] Found {len(stale)} positions open > 24h")
        issues.append(f"⏰ 持仓超24h: {', '.join(stale[:5])}")
    else:
        print("[OK] No stale positions")

    # 4. 发送警报（如果有问题）
    if issues:
        alert_msg = "\n".join(issues)
        send_alert(alert_msg)
        print(f"\n[ALERT]\n{alert_msg}")
    else:
        print("[OK] All checks passed")


if __name__ == "__main__":
    main()
