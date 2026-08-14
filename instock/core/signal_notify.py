#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import threading
import time

import requests

from instock.core.common import _now

__author__ = 'myh '
__date__ = '2026/8/11 '

_NOTIFY_FILE_NAME = "signal_notify_daily.txt"
_PUSH_CONFIG_FILE_NAME = "qq_push.conf"
_PUSH_CONFIG_DEFAULT_TEXT = """\
# QQ群推送配置：买卖点信号写入后推送到QQ群（NapCat OneBot HTTP）
# 每行 key=value，# 开头或空行忽略。
# enabled：1 开启推送，0 关闭（默认关闭，配好 group_id 后改为 1）
# api_url：NapCat HTTP 地址。InStock 与 NapCat 在同一 Docker 网络时用容器名 http://NapCat:3000；
#          不同网络/宿主机调试时可用 http://host.docker.internal:3000
# group_id：目标 QQ 群号
# token：NapCat 设置的 Access Token，未设置留空
enabled=0
api_url=http://NapCat:3000
group_id=
token=
"""
_FILTER_FILE_NAME = "signal_filter.txt"
_FILTER_DEFAULT_RULES = (
    ("deducted_profit_growth", ">", -10.0),
    ("dividend_yield", ">", 3.0),
    ("fcf_dividend", ">", 50.0),
)
_FILTER_DEFAULT_TEXT = """\
# 买卖点信号写入过滤条件（signal_notify_daily.txt 配套）
# 不满足任一条件的信号不会写入通知文件。
# 每行一个条件，格式：字段 操作符 阈值，例如 扣非>-10
# 支持操作符：> >= < <=
# 字段：扣非（最新季报扣非净利润同比增长%）、股息率（%）、FCF/股息（覆盖率%）
# 以#开头或空行会被忽略；删除对应行即关闭该过滤。修改后即时生效，无需重启。
扣非>-10
股息率>3
FCF/股息>50
"""
_NOTIFY_FILE_LOCK = threading.Lock()
_SENT_SIGNALS = set()  # {(日期, 代码)} 每只股票每天只触发一次
_SENT_SIGNALS_LOADED = False
_SENT_SIGNALS_LOAD_LOCK = threading.Lock()


def _notify_file_path():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, "config", _NOTIFY_FILE_NAME)


def _filter_file_path():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, "config", _FILTER_FILE_NAME)


def _push_config_file_path():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, "config", _PUSH_CONFIG_FILE_NAME)


def ensure_push_config_file():
    """推送配置文件不存在时创建默认模板。服务启动时调用，方便提前配置。"""
    path = _push_config_file_path()
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_PUSH_CONFIG_DEFAULT_TEXT)
        except Exception as error:
            print(f"signal_notify 创建推送配置文件失败：{error}")


def _read_push_config():
    """读取 QQ 群推送配置，返回 {key: value}；未开启/未配置时返回空字典。"""
    ensure_push_config_file()
    config = {}
    try:
        with open(_push_config_file_path(), "r", encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                config[key.strip()] = value.strip()
    except Exception as error:
        print(f"signal_notify 读取推送配置失败：{error}")
        return {}
    if config.get("enabled") != "1" or not config.get("group_id"):
        return {}
    return config


def _push_to_group(line):
    """将信号行推送到 QQ 群（NapCat OneBot HTTP send_group_msg）。

    未开启推送、缺群号/地址时静默跳过；失败重试一次后放弃，
    只打印日志，不影响信号文件的写入。
    """
    config = _read_push_config()
    if not config:
        return
    api_url = config.get("api_url", "").rstrip("/")
    group_id = config.get("group_id", "")
    if not api_url or not group_id:
        return
    headers = {"Content-Type": "application/json"}
    token = config.get("token", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {"group_id": int(group_id), "message": line}
    for attempt in range(2):
        try:
            resp = requests.post(f"{api_url}/send_group_msg", json=payload,
                                 headers=headers, timeout=3)
            if resp.status_code < 300:
                return
            print(f"signal_notify QQ推送失败 HTTP {resp.status_code}：{resp.text[:100]}")
        except Exception as error:
            print(f"signal_notify QQ推送异常：{error}")
        if attempt == 0:
            time.sleep(1)


def ensure_filter_file():
    """过滤配置文件不存在时创建默认规则文件。服务启动时调用，方便提前配置。"""
    path = _filter_file_path()
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_FILTER_DEFAULT_TEXT)
        except Exception as error:
            print(f"signal_notify 创建过滤配置文件失败：{error}")


_FILTER_FIELDS = (
    ("扣非", "deducted_profit_growth"),
    ("股息率", "dividend_yield"),
    ("FCF/股息", "fcf_dividend"),
)


def _parse_filter_rule(text):
    """解析一行过滤条件：字段 操作符 阈值，如 扣非>-10；解析失败返回 None。"""
    for field_name, field_key in _FILTER_FIELDS:
        if text.startswith(field_name):
            match = re.match(r"^(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)$", text[len(field_name):].strip())
            if match:
                return (field_key, match.group(1), float(match.group(2)))
    return None


def _load_filter_rules():
    """读取过滤配置，返回 [(字段键, 操作符, 阈值), ...]；文件不存在时先创建默认。"""
    ensure_filter_file()
    rules = []
    try:
        with open(_filter_file_path(), "r", encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                rule = _parse_filter_rule(line)
                if rule:
                    rules.append(rule)
    except Exception as error:
        print(f"signal_notify 读取过滤配置失败：{error}")
    return rules


def passes_signal_filter(metrics):
    """按过滤配置文件判断信号是否写入：扣非>-10%、股息率>3%、FCF/股息>50%（默认）。

    FCF/股息配置按百分比（缓存值为倍数，内部×100 后比较）；
    字段缺失视为不通过；无配置规则时全部通过。
    """
    rules = _load_filter_rules()
    if not rules:
        return True
    for field_key, op, threshold in rules:
        value = metrics.get(field_key)
        if field_key == "fcf_dividend" and value is not None:
            value = value * 100
        if value is None:
            return False
        if op == ">":
            ok = value > threshold
        elif op == ">=":
            ok = value >= threshold
        elif op == "<":
            ok = value < threshold
        else:
            ok = value <= threshold
        if not ok:
            return False
    return True


def _load_sent_signals_from_file():
    """服务重启后从通知文件重建去重集合，避免当天已触发的信号重复写入。"""
    global _SENT_SIGNALS
    path = _notify_file_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return
    if not lines:
        return
    first = lines[0].strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", first):
        return  # 首行不是日期，不重建
    for line in lines[1:]:
        match = re.search(r"(\d{6})｜", line)
        if match:
            _SENT_SIGNALS.add((first, match.group(1)))


def _ensure_sent_signals_loaded():
    global _SENT_SIGNALS_LOADED
    if _SENT_SIGNALS_LOADED:
        return
    with _SENT_SIGNALS_LOAD_LOCK:
        if _SENT_SIGNALS_LOADED:
            return
        _load_sent_signals_from_file()
        _SENT_SIGNALS_LOADED = True


def _fmt_2f(value):
    """现价：与前端 formatNumber(x, 2) 一致，2位小数。"""
    return f"{value:.2f}" if value is not None else "--"


def _fmt_signed_pct2(value):
    """涨跌幅：前端无直接显示（隐藏列），沿用模板 formatSignedPercent 风格，正数带+、2位小数。"""
    return f"{value:+.2f}%" if value is not None else "--"


def _fmt_pct1(value):
    """股息率、扣非：与前端 formatBlankPercent(x, 1) 一致，1位小数。"""
    return f"{value:.1f}%" if value is not None else "--"


def _fmt_signed_pct1(value):
    """MA120位置：与前端 formatBlankPercent(x, 1) 一致的1位小数，正数带+号便于区分位置方向。"""
    return f"{value:+.1f}%" if value is not None else "--"


def _fmt_pct0(value):
    """FCF/股息：与前端 formatBlankPercent(x*100, 0) 一致，整数百分比。"""
    return f"{value:.0f}%" if value is not None else "--"


def _fmt_int(value):
    """息增年、市值：与前端 formatBlankNumber/formatOptionalNumber(x, 0) 一致，整数。"""
    return f"{value:.0f}" if value is not None else "--"


def _fmt_chinese_time(dt):
    """写入时间（中文风格）：上午9点11分 / 下午2点30分。"""
    hour = dt.hour
    period = "上午" if hour < 12 else "下午"
    hour_12 = hour % 12
    if hour_12 == 0:
        hour_12 = 12
    return f"{period}{hour_12}点{dt.minute}分"


def write_signal_notify(code, name, signal, metrics, now=None):
    """记录一条买卖点信号到 signal_notify_daily.txt；每只股票每天只触发一次（去重）。

    文件结构：第一行为当天日期，之后每行按信号触发顺序排列（标签带写入时间），例如：
        2026-08-11
        买入信号·上午9点11分：000680｜山推股份、现价10.84、涨跌幅-4.91%、MA120位置-4.2%、股息率3.6%、扣非10.0%、息增年1、FCF/股息110%、市值100、电力
    metrics 为附加字段字典，字段缺失显示 --；跨天后首次写入会重写文件，只保留当天的信号。
    写入失败只打印日志，不影响主流程。
    """
    _ensure_sent_signals_loaded()
    if now is None:
        now = _now()
    today = now.date().isoformat()
    key = (today, code)
    with _NOTIFY_FILE_LOCK:
        if key in _SENT_SIGNALS:
            return False
        # 信号标签带写入时间，如 买入信号·上午9点11分：
        label = f"{'买入信号' if signal == 'buy' else '卖出信号'}·{_fmt_chinese_time(now)}"
        fcf_dividend = metrics.get("fcf_dividend")
        fcf_text = _fmt_pct0(fcf_dividend * 100) if fcf_dividend is not None else "--"
        line = (
            f"{label}：{code}｜{name}、现价{_fmt_2f(metrics.get('current_price'))}、"
            f"涨跌幅{_fmt_signed_pct2(metrics.get('change_rate'))}、"
            f"MA120位置{_fmt_signed_pct1(metrics.get('ma120_position'))}、"
            f"股息率{_fmt_pct1(metrics.get('dividend_yield'))}、"
            f"扣非{_fmt_pct1(metrics.get('deducted_profit_growth'))}、"
            f"息增年{_fmt_int(metrics.get('dividend_growth_years'))}、"
            f"FCF/股息{fcf_text}、市值{_fmt_int(metrics.get('market_cap'))}、"
            f"{metrics.get('industry_name') or '--'}"
        )
        path = _notify_file_path()
        try:
            lines = []
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
            if not lines or lines[0].strip() != today:
                # 跨天/当天首条：重写文件，第一行为当天日期
                lines = [today]
                _SENT_SIGNALS.clear()
            lines.append(line)
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            os.replace(tmp_path, path)
            _SENT_SIGNALS.add(key)
            # QQ群推送（NapCat）：失败不影响文件记录
            _push_to_group(line)
            return True
        except Exception as error:
            print(f"signal_notify 写入失败：{error}")
            return False
