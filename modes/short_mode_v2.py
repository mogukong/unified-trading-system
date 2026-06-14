"""
short_mode_v2 兼容层 — 重导出 short_mode (v3.0) 的公开 API
测试脚本 test_short_v2.py 依赖此模块名
"""
from .short_mode import calc_short_score_v2, SHORT_PATTERNS  # noqa: F401
