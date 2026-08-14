#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import threading

from instock.core.common import _now

__author__ = 'myh '
__date__ = '2026/8/12 '

_HIGH_ATTENTION_FILE_NAME = "high_attention_daily.txt"
# 股息率≥此百分比的股票视为高关注度，盘中行情刷新间隔缩短（见 market_quotes 的 _PRICE_REFRESH_MINUTES_*）
_HIGH_ATTENTION_YIELD_THRESHOLD = 4.0
_HIGH_ATTENTION_LOCK = threading.Lock()


def _high_attention_file_path():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, "config", _HIGH_ATTENTION_FILE_NAME)


def ensure_high_attention_file():
    """高关注度文件不存在时创建空文件（首行为当天日期）。服务启动时调用。

    文件内容由页面请求按最新股息率重写（见 update_high_attention），
    日期行保证跨天后读取视为空，重新按当天股息率生成。
    """
    path = _high_attention_file_path()
    if not os.path.exists(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(_now().strftime("%Y-%m-%d") + "\n")
        except Exception as error:
            print(f"high_attention 创建高关注度文件失败：{error}")


def get_high_attention_codes():
    """读取高关注度股票代码集合。

    文件首行为当天日期，跨天后读取视为空（每日清空），重新按当天股息率生成；
    文件缺失时返回空集合。读失败只影响本次刷新间隔划分，不影响页面。
    """
    path = _high_attention_file_path()
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if not lines or lines[0].strip() != _now().strftime("%Y-%m-%d"):
            return set()
        codes = set()
        for line in lines[1:]:
            parts = line.split()
            if parts:
                codes.add(parts[0])
        return codes
    except Exception as error:
        print(f"high_attention 读取高关注度文件失败：{error}")
        return set()


def update_high_attention(rows, now):
    """将股息率≥阈值的股票写入高关注度文件（每日清空：首行为当天日期，跨天重写只保留当天）。

    每次页面请求按最新股息率重写当天列表（页面未打开时不写入）；
    收盘后、周末打开页面同样重写，日期行保证次日盘中读取时列表为空，重新按当天股息率生成。
    """
    entries = sorted(
        (row.get("code"), row.get("name") or "")
        for row in rows or []
        if row.get("dividend_yield") is not None and row["dividend_yield"] >= _HIGH_ATTENTION_YIELD_THRESHOLD
    )
    with _HIGH_ATTENTION_LOCK:
        try:
            with open(_high_attention_file_path(), "w", encoding="utf-8") as f:
                f.write(now.strftime("%Y-%m-%d") + "\n")
                for code, name in entries:
                    f.write(f"{code} {name}\n")
        except Exception as error:
            print(f"high_attention 写入高关注度文件失败：{error}")
