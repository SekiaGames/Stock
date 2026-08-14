#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import threading

import instock.lib.database as mdb
import instock.lib.mysql as mysql
import instock.core.stocklist as stocklist
from instock.core.common import (
    _now,
    _ensure_cache_tables,
    _PROFILE_CACHE_TABLE,
)

__author__ = 'myh '
__date__ = '2026/8/6 '

_INDUSTRY_REFRESH_LOCK = threading.Lock()
_INDUSTRY_REFRESH_RUNNING = False
_MARKET_CAP_REFRESH_LOCK = threading.Lock()
_MARKET_CAP_REFRESH_RUNNING = False


def _read_profile_cache(db, stock_codes):
    if not stock_codes:
        return {}
    placeholders = ",".join(["%s"] * len(stock_codes))
    rows = db.query(f"""
        SELECT `code`, `market_cap`, `industry_name`,
               `industry_fetched_at`, `market_cap_fetched_at`, `fetched_at`
        FROM `{_PROFILE_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *stock_codes)
    return {row["code"]: row for row in rows}


def _is_industry_cache_stale(cache_row, now):
    """行业只抓一次：无缓存或行业为空视为缺失，之后不再刷新（申万二级行业基本不变）。"""
    if cache_row is None:
        return True
    return not (cache_row.get("industry_name") or "")


def _week_friday(date):
    """date 所在周的最后一个交易日（周五）：周一至周四为本周五，周五为当天，周末为刚过的周五。"""
    weekday = date.weekday()
    if weekday >= 5:
        return date - datetime.timedelta(days=weekday - 4)
    return date + datetime.timedelta(days=4 - weekday)


def _is_market_cap_cache_stale(cache_row, now):
    """市值取每周最后一个交易日（周五）的收盘数据。

    周一至周四（周五15点收盘前）：保持上周最后一个交易日收盘数据，不刷新；
    周五15点后：需要抓取本周收盘数据。无缓存或市值为空立即抓取。
    """
    if cache_row is None:
        return True
    if cache_row.get("market_cap") is None:
        return True
    week_close = datetime.datetime.combine(_week_friday(now.date()), datetime.time(15, 0))
    if now < week_close:
        return False
    fetched_at = cache_row.get("market_cap_fetched_at") or cache_row.get("fetched_at")
    if not fetched_at:
        return True
    return fetched_at < week_close


def _write_industry_cache(db, code, industry_name, now):
    db.execute(f"""
        INSERT INTO `{_PROFILE_CACHE_TABLE}`
            (`code`, `industry_name`, `industry_fetched_at`, `fetched_at`)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `industry_name` = VALUES(`industry_name`),
            `industry_fetched_at` = VALUES(`industry_fetched_at`),
            `fetched_at` = VALUES(`fetched_at`)
    """,
               code,
               industry_name,
               now.strftime("%Y-%m-%d %H:%M:%S"),
               now.strftime("%Y-%m-%d %H:%M:%S"))


def _write_market_cap_cache(db, code, market_cap, now):
    db.execute(f"""
        INSERT INTO `{_PROFILE_CACHE_TABLE}`
            (`code`, `market_cap`, `market_cap_fetched_at`, `fetched_at`)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `market_cap` = VALUES(`market_cap`),
            `market_cap_fetched_at` = VALUES(`market_cap_fetched_at`),
            `fetched_at` = VALUES(`fetched_at`)
    """,
               code,
               market_cap,
               now.strftime("%Y-%m-%d %H:%M:%S"),
               now.strftime("%Y-%m-%d %H:%M:%S"))


def _refresh_industries(stock_codes):
    global _INDUSTRY_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        now = _now()
        profile_by_code = _read_profile_cache(db, stock_codes)
        # 只请求缺失行业的股票；并发或重复调度时，已抓到行业的股票不再请求
        need_codes = [
            code for code in stock_codes
            if _is_industry_cache_stale(profile_by_code.get(code), now)
        ]
        if not need_codes:
            return
        industry_data = stocklist.fetch_industry_data(need_codes)
        if not industry_data:
            return
        for code in need_codes:
            industry_name = industry_data.get(code)
            if industry_name:
                _write_industry_cache(db, code, industry_name, now)
    except Exception as error:
        print(f"profile._refresh_industries处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _INDUSTRY_REFRESH_LOCK:
            _INDUSTRY_REFRESH_RUNNING = False


def _schedule_industry_refresh(stock_codes):
    global _INDUSTRY_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    with _INDUSTRY_REFRESH_LOCK:
        if _INDUSTRY_REFRESH_RUNNING:
            return
        _INDUSTRY_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_industries, args=(stock_codes,), daemon=True)
    thread.start()
    return thread


def _refresh_market_caps(stock_codes):
    global _MARKET_CAP_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        now = _now()
        profile_by_code = _read_profile_cache(db, stock_codes)
        # 只请求市值过期的股票；并发或重复调度时，仍新鲜的股票不再请求
        need_codes = [
            code for code in stock_codes
            if _is_market_cap_cache_stale(profile_by_code.get(code), now)
        ]
        if not need_codes:
            return
        market_cap_data = stocklist.fetch_market_cap_data(need_codes)
        if not market_cap_data:
            return
        for code in need_codes:
            market_cap = market_cap_data.get(code)
            if market_cap is not None:
                _write_market_cap_cache(db, code, market_cap, now)
    except Exception as error:
        print(f"profile._refresh_market_caps处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _MARKET_CAP_REFRESH_LOCK:
            _MARKET_CAP_REFRESH_RUNNING = False


def _schedule_market_cap_refresh(stock_codes):
    global _MARKET_CAP_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    with _MARKET_CAP_REFRESH_LOCK:
        if _MARKET_CAP_REFRESH_RUNNING:
            return
        _MARKET_CAP_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_market_caps, args=(stock_codes,), daemon=True)
    thread.start()
    return thread


def _get_cached_profile_rows(db, stock_codes):
    """Read profile cache; returns dict keyed by code, missing entries have None value."""
    return _read_profile_cache(db, stock_codes)
