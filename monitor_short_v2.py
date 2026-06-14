#!/usr/bin/env python3
"""
做空评分 v2.0 集成验证脚本
监控新系统的模式识别
"""

import time
import re
from datetime import datetime

LOG_FILE = '~/demon-coin-detector/unified_system/engine_log.txt'

def monitor_log():
    """监控日志，检查模式识别"""
    print("=" * 60)
    print("做空评分 v2.0 集成验证")
    print("=" * 60)
    print(f"\n监控日志: {LOG_FILE}")
    print("等待新信号...")
    print()
    
    last_position = 0
    pattern_count = 0
    
    while True:
        try:
            with open(LOG_FILE, 'r') as f:
                f.seek(last_position)
                new_lines = f.readlines()
                last_position = f.tell()
            
            for line in new_lines:
                line = line.strip()
                
                # 检查做空信号
                if 'SHORT信号' in line or '做空' in line:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] {line}")
                
                # 检查模式识别
                if '模式' in line or 'pattern' in line.lower():
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🎯 {line}")
                    pattern_count += 1
                
                # 检查评分
                if '评分' in line and ('SHORT' in line or '做空' in line):
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 {line}")
                
                # 检查止损
                if '止损' in line and 'SHORT' in line:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🛡️ {line}")
            
            time.sleep(5)
            
        except KeyboardInterrupt:
            print(f"\n\n监控结束。共发现 {pattern_count} 个模式识别。")
            break
        except Exception as e:
            print(f"错误: {e}")
            time.sleep(5)

if __name__ == "__main__":
    monitor_log()
