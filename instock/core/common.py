#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import hashlib
import json
import random
import threading
import time
from decimal import Decimal
from zoneinfo import ZoneInfo

from instock.core.eastmoney_fetcher import eastmoney_fetcher

__author__ = 'myh '
__date__ = '2026/8/6 '

_DIVIDEND_FETCHER = eastmoney_fetcher()
_CACHE_TABLE_READY = False
_CACHE_TABLE_LOCK = threading.Lock()
_EXTERNAL_REQUEST_LOCK = threading.Lock()
_LAST_EXTERNAL_REQUEST_AT = 0.0
_EXTERNAL_REQUEST_INTERVAL_SECONDS = 1
_PRICE_CACHE_TABLE = "cn_high_dividend_price_cache"
_DIVIDEND_HISTORY_CACHE_TABLE = "cn_high_dividend_dividend_history_cache"
_FINANCE_REPORT_CACHE_TABLE = "cn_high_dividend_finance_report_cache"
_CASHFLOW_CACHE_TABLE = "cn_high_dividend_cashflow_cache"
_MA120_CACHE_TABLE = "cn_high_dividend_ma120_cache"
_KLINE_CACHE_TABLE = "cn_high_dividend_kline_cache"
_DETAIL_KLINE_CACHE_TABLE = "cn_high_dividend_detail_kline_cache"
_LIQ_CACHE_TABLE = "cn_high_dividend_liq_cache"
_PROFILE_CACHE_TABLE = "cn_high_dividend_profile_cache"
_SETTINGS_TABLE = "cn_high_dividend_settings"
_FISCAL_YEAR_BASE_KEY = "fiscal_year_base"
_LVHI_TRADES_TABLE = "cn_high_dividend_lvhi_trades"
_LVHI_KLINE_CACHE_TABLE = "cn_high_dividend_lvhi_kline_cache"
_LVHI_INITIAL_CAPITAL_KEY = "lvhi_initial_capital"
_LVHI_BUILD_STATUS_KEY = "lvhi_build_status"
_LVHI_BUILD_DATE_KEY = "lvhi_build_date"
_LVHI_KLINE_COUNT_KEY = "lvhi_kline_count"
_LVHI_DEFAULT_CAPITAL = 1000000
_LVHI_DEFAULT_KLINE_COUNT = 640
_LVHI_BUILD_STOCK_CODE = "600900"
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_DIVIDEND_REFRESH_HOUR = 8
_DIVIDEND_AFTER_CLOSE_REFRESH_START_HOUR = 16
_DIVIDEND_AFTER_CLOSE_REFRESH_END_HOUR = 23
_REPORT_AFTER_CLOSE_REFRESH_INTERVAL_HOURS = 4
_CASHFLOW_OFFSEASON_REFRESH_DAYS = 7


def _to_float(value):
    try:
        if value in ("", None, "--", "-"):
            return None
        if isinstance(value, Decimal):
            return float(value)
        return float(value)
    except Exception:
        return None


def _now():
    return datetime.datetime.now(_SHANGHAI_TZ).replace(tzinfo=None)


def _date_text(value):
    if not value:
        return ""
    return str(value)[:10]


def _json_default(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _throttle_external_request():
    # 间隔加 ±20% 抖动，避免固定节律被风控识别
    global _LAST_EXTERNAL_REQUEST_AT
    with _EXTERNAL_REQUEST_LOCK:
        elapsed = time.time() - _LAST_EXTERNAL_REQUEST_AT
        target = _EXTERNAL_REQUEST_INTERVAL_SECONDS * random.uniform(0.8, 1.2)
        if elapsed < target:
            time.sleep(target - elapsed)
        _LAST_EXTERNAL_REQUEST_AT = time.time()


def _get_or_sync_fiscal_year_base(db):
    """读取财年基准年份（settings 表），不存在时写入当前年份。

    跨年（now.year 大于存储值，如2026→2027）时自动重置为当前年份，
    旧财年收益/新财年收益随基准年份平移（旧=基准-2，新=基准-1）。
    """
    now = _now()
    row = db.get(
        f"SELECT `setting_value` FROM `{_SETTINGS_TABLE}` WHERE `setting_key` = %s",
        _FISCAL_YEAR_BASE_KEY,
    )
    base = now.year
    if row is not None:
        try:
            base = int(row.get("setting_value"))
        except Exception:
            base = now.year
    if row is None or now.year > base:
        db.execute(f"""
            INSERT INTO `{_SETTINGS_TABLE}` (`setting_key`, `setting_value`, `updated_at`)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                `setting_value` = VALUES(`setting_value`),
                `updated_at` = VALUES(`updated_at`)
        """, _FISCAL_YEAR_BASE_KEY, str(now.year), now.strftime("%Y-%m-%d %H:%M:%S"))
        base = now.year
    return base


def _ensure_cache_tables(db):
    global _CACHE_TABLE_READY
    if _CACHE_TABLE_READY:
        return

    with _CACHE_TABLE_LOCK:
        if _CACHE_TABLE_READY:
            return

        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_PRICE_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `name` varchar(20) DEFAULT NULL,
                `price_date` date DEFAULT NULL,
                `current_price` decimal(12,4) DEFAULT NULL,
                `fetched_at` datetime NOT NULL,
                `market_phase` varchar(20) DEFAULT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_fetched_at` (`fetched_at`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_PRICE_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `pre_close_price` decimal(12,4) DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_PRICE_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `change_rate` decimal(12,4) DEFAULT NULL")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_PROFILE_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `market_cap` decimal(16,4) DEFAULT NULL,
                `industry_name` varchar(50) DEFAULT NULL,
                `fetched_at` datetime NOT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_fetched_at` (`fetched_at`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_PROFILE_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `industry_fetched_at` datetime DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_PROFILE_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `market_cap_fetched_at` datetime DEFAULT NULL")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_DIVIDEND_HISTORY_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `name` varchar(20) DEFAULT NULL,
                `history_json` longtext NOT NULL,
                `history_hash` char(64) DEFAULT NULL,
                `checked_on` date NOT NULL,
                `checked_at` datetime NOT NULL,
                `changed_at` datetime DEFAULT NULL,
                `changed_report_date` date DEFAULT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_checked_on` (`checked_on`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_DIVIDEND_HISTORY_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `history_hash` char(64) DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_DIVIDEND_HISTORY_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `changed_at` datetime DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_DIVIDEND_HISTORY_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `changed_report_date` date DEFAULT NULL")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_FINANCE_REPORT_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `name` varchar(20) DEFAULT NULL,
                `finance_json` longtext NOT NULL,
                `finance_hash` char(64) DEFAULT NULL,
                `checked_on` date NOT NULL,
                `checked_at` datetime NOT NULL,
                `changed_at` datetime DEFAULT NULL,
                `changed_report_date` date DEFAULT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_checked_on` (`checked_on`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_FINANCE_REPORT_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `finance_hash` char(64) DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_FINANCE_REPORT_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `changed_at` datetime DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_FINANCE_REPORT_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `changed_report_date` date DEFAULT NULL")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_CASHFLOW_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `name` varchar(20) DEFAULT NULL,
                `cashflow_json` longtext NOT NULL,
                `checked_on` date NOT NULL,
                `checked_at` datetime NOT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_checked_on` (`checked_on`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_MA120_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `trade_date` date DEFAULT NULL,
                `close_price` decimal(12,4) DEFAULT NULL,
                `ma120` decimal(12,4) DEFAULT NULL,
                `ma120_position` decimal(12,4) DEFAULT NULL,
                `fetched_at` datetime NOT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_fetched_at` (`fetched_at`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_KLINE_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `trade_date` date NOT NULL,
                `open_price` decimal(12,4) DEFAULT NULL,
                `close_price` decimal(12,4) DEFAULT NULL,
                `high_price` decimal(12,4) DEFAULT NULL,
                `low_price` decimal(12,4) DEFAULT NULL,
                `volume` decimal(20,2) DEFAULT NULL,
                PRIMARY KEY (`code`, `trade_date`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_KLINE_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `open_price` decimal(12,4) DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_KLINE_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `volume` decimal(20,2) DEFAULT NULL")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_DETAIL_KLINE_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `trade_date` date NOT NULL,
                `open_price` decimal(12,4) DEFAULT NULL,
                `close_price` decimal(12,4) DEFAULT NULL,
                `high_price` decimal(12,4) DEFAULT NULL,
                `low_price` decimal(12,4) DEFAULT NULL,
                `volume` decimal(20,2) DEFAULT NULL,
                PRIMARY KEY (`code`, `trade_date`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_LIQ_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `trade_date` date DEFAULT NULL,
                `liq_score` decimal(8,4) DEFAULT NULL,
                `price_pos` decimal(8,4) DEFAULT NULL,
                `turnover_pct` decimal(8,4) DEFAULT NULL,
                `pressure_pct` decimal(8,4) DEFAULT NULL,
                `vol20` decimal(8,4) DEFAULT NULL,
                `turnover` decimal(12,4) DEFAULT NULL,
                `fetched_at` datetime NOT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_fetched_at` (`fetched_at`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_LIQ_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `liq_daily` decimal(8,4) DEFAULT NULL")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_SETTINGS_TABLE}` (
                `setting_key` varchar(50) NOT NULL,
                `setting_value` varchar(100) DEFAULT NULL,
                `updated_at` datetime DEFAULT NULL,
                PRIMARY KEY (`setting_key`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        _CACHE_TABLE_READY = True


_LVHI_TABLE_READY = False
_LVHI_TABLE_LOCK = threading.Lock()


def _ensure_lvhi_tables(db):
    """创建 LVHI 模拟组合的两张表（账本 + 扩展K线缓存），单飞缓存，模式同 _ensure_cache_tables。

    独立于 _ensure_cache_tables，避免影响高股息管线；首次使用 LVHI 页面时调用。
    """
    global _LVHI_TABLE_READY
    if _LVHI_TABLE_READY:
        return

    with _LVHI_TABLE_LOCK:
        if _LVHI_TABLE_READY:
            return

        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_LVHI_TRADES_TABLE}` (
                `id` bigint NOT NULL AUTO_INCREMENT,
                `trade_date` date NOT NULL,
                `trade_time` datetime NOT NULL,
                `direction` varchar(4) NOT NULL,
                `code` varchar(6) NOT NULL,
                `name` varchar(20) DEFAULT NULL,
                `price` decimal(12,4) NOT NULL,
                `shares` int NOT NULL,
                `amount` decimal(16,2) NOT NULL,
                `cash_after` decimal(16,2) NOT NULL,
                `note` varchar(200) DEFAULT '',
                `created_at` datetime NOT NULL,
                PRIMARY KEY (`id`),
                INDEX `idx_trade_date` (`trade_date`),
                INDEX `idx_code` (`code`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_LVHI_KLINE_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `trade_date` date NOT NULL,
                `close_price` decimal(12,4) DEFAULT NULL,
                PRIMARY KEY (`code`, `trade_date`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        _LVHI_TABLE_READY = True


def _market_phase(now):
    if now.weekday() >= 5:
        return "closed"
    current = now.time()
    if datetime.time(9, 30) <= current < datetime.time(15, 0):
        return "intraday"
    if current >= datetime.time(15, 0):
        return "after_close"
    return "before_open"


def _previous_trading_day(date):
    """返回 date 之前的最近一个交易日（周一至周五），跨周末时回到周五。"""
    prev = date - datetime.timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= datetime.timedelta(days=1)
    return prev


def _history_hash(history):
    history_text = json.dumps(history, ensure_ascii=False, sort_keys=True, default=_json_default)
    return hashlib.sha256(history_text.encode("utf-8")).hexdigest()


def _changed_report_date(now):
    if now.hour >= _DIVIDEND_AFTER_CLOSE_REFRESH_START_HOUR:
        return now.date() + datetime.timedelta(days=1)
    return now.date()


def _is_daily_report_cache_stale(cache_row, now, after_close_interval_hours=None, after_close_once=False):
    if cache_row is None:
        return True

    checked_on = cache_row.get("checked_on")
    checked_at = cache_row.get("checked_at")
    if not checked_on or not checked_at:
        return True
    if isinstance(checked_on, datetime.datetime):
        checked_on = checked_on.date()

    if now.weekday() >= 5:
        return False

    if _DIVIDEND_AFTER_CLOSE_REFRESH_START_HOUR <= now.hour <= _DIVIDEND_AFTER_CLOSE_REFRESH_END_HOUR:
        close_time = datetime.datetime.combine(now.date(), datetime.time(15, 0))
        if after_close_once:
            return checked_at < close_time
        interval_hours = after_close_interval_hours or _REPORT_AFTER_CLOSE_REFRESH_INTERVAL_HOURS
        return checked_at <= now - datetime.timedelta(hours=interval_hours)
    if now.time() >= datetime.time(_DIVIDEND_REFRESH_HOUR, 0):
        return checked_on < now.date()
    return False


def _is_in_changed_display_window(changed_at, changed_report_date, now):
    if changed_at is None:
        return False
    if isinstance(changed_at, str):
        changed_at = datetime.datetime.fromisoformat(changed_at)
    if changed_report_date is None:
        changed_report_date = _changed_report_date(changed_at)
    elif isinstance(changed_report_date, datetime.datetime):
        changed_report_date = changed_report_date.date()
    elif isinstance(changed_report_date, str):
        changed_report_date = datetime.date.fromisoformat(changed_report_date[:10])
    return changed_at <= now and now.date() <= changed_report_date
