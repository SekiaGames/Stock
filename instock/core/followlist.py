#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import threading

__author__ = 'myh '
__date__ = '2026/7/30 '

_FOLLOW_LOCK = threading.Lock()
_CODE_PATTERN = re.compile(r"(?<!\d)(?:sh|sz|bj)?(\d{6})(?!\d)", re.IGNORECASE)


def _follow_file_path():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, "config", "followlist.txt")


def _ensure_file():
    path = _follow_file_path()
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            pass
    return path


def get_follow_codes():
    """获取所有关注的股票代码列表。"""
    path = _follow_file_path()
    if not os.path.isfile(path):
        return []
    with _FOLLOW_LOCK:
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


def toggle_follow(code):
    """切换关注状态。返回 True 表示当前已关注。"""
    path = _ensure_file()
    with _FOLLOW_LOCK:
        lines = []
        found = False
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.split("#", 1)[0].split("//", 1)[0].strip()
                    match = _CODE_PATTERN.search(stripped) if stripped else None
                    if match and match.group(1) == code:
                        found = True
                        # 跳过该行（取消关注）
                    else:
                        lines.append(line.rstrip("\n"))

        if found:
            with open(path, "w", encoding="utf-8") as f:
                content = "\n".join(lines)
                if content:
                    f.write(content + "\n")
            return False

        # 添加关注
        with open(path, "a", encoding="utf-8") as f:
            f.write(code + "\n")
        return True
