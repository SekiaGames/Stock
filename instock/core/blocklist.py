#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import threading

__author__ = 'myh '
__date__ = '2026/8/6 '

_BLOCK_LOCK = threading.Lock()
_CODE_PATTERN = re.compile(r"(?<!\d)(?:sh|sz|bj)?(\d{6})(?!\d)", re.IGNORECASE)

# 屏蔽文件（位于 instock/config/ 下）
GROWTH_YEAR_ZERO_FILE = "blocklist_dividendGrowthYearZero.txt"
YIELD_BELOW_ONE_FILE = "blocklist_dividendYieldBelowOne.txt"
NEGATIVE_EPS_FILE = "blocklist_negativeEps.txt"
INDUSTRY_STOCKS_FILE = "blocklist_industryStocks.txt"


def _block_file_path(file_name):
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, "config", file_name)


def _ensure_file(path):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            pass
    return path


def get_blocked_codes(file_name):
    """读取屏蔽文件中的股票代码列表；文件不存在时自动创建。"""
    path = _ensure_file(_block_file_path(file_name))
    with _BLOCK_LOCK:
        codes = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].split("//", 1)[0].strip()
                if not line:
                    continue
                match = _CODE_PATTERN.search(line)
                if match:
                    codes.append(match.group(1))
        return codes


def add_blocked(file_name, code, name=""):
    """记录一只股票到屏蔽文件；已存在时不重复添加。"""
    path = _ensure_file(_block_file_path(file_name))
    with _BLOCK_LOCK:
        existing = set()
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].split("//", 1)[0].strip()
                if not line:
                    continue
                match = _CODE_PATTERN.search(line)
                if match:
                    existing.add(match.group(1))
        if code in existing:
            return
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{code} {name}".rstrip() + "\n")
