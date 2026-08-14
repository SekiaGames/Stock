#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import json
import threading

import instock.lib.database as mdb
import instock.lib.mysql as mysql
from instock.core.common import (
    _to_float,
    _now,
    _date_text,
    _json_default,
    _throttle_external_request,
    _ensure_cache_tables,
    _history_hash,
    _changed_report_date,
    _is_daily_report_cache_stale,
    _is_in_changed_display_window,
    _DIVIDEND_FETCHER,
    _DIVIDEND_HISTORY_CACHE_TABLE,
    _REPORT_AFTER_CLOSE_REFRESH_INTERVAL_HOURS,
)

__author__ = 'myh '
__date__ = '2026/8/6 '

_DIVIDEND_REFRESH_LOCK = threading.Lock()
_DIVIDEND_REFRESH_RUNNING = False


_DIVIDEND_BATCH_SIZE = 10
_DIVIDEND_BATCH_PAGE_SIZE = 400


def _fetch_dividend_histories_batch(codes):
    """批量抓取派息历史，每批10只一次请求，返回 {code: history}。

    派息历史每股最多约30条，10只一批、pageSize=400 保证不截断。
    批次抓取失败时该批股票不出现在结果中（保持原缓存，下次重试）；
    批次成功但无派息记录的股票返回空列表（确认无分红）。
    """
    result = {}
    if not codes:
        return result
    for i in range(0, len(codes), _DIVIDEND_BATCH_SIZE):
        batch = codes[i:i + _DIVIDEND_BATCH_SIZE]
        code_list = '","'.join(batch)
        _throttle_external_request()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_SHAREBONUS_DET",
            "columns": "ALL",
            "quoteColumns": "",
            "filter": f'(SECURITY_CODE in ("{code_list}"))',
            "pageNumber": "1",
            "pageSize": str(_DIVIDEND_BATCH_PAGE_SIZE),
            "sortTypes": "-1",
            "sortColumns": "REPORT_DATE",
            "source": "WEB",
            "client": "WEB",
        }
        try:
            response = _DIVIDEND_FETCHER.make_request(url, params=params, timeout=15)
            payload = response.json()
            if payload.get("success") and payload.get("result"):
                for code in batch:
                    result.setdefault(code, [])
                for row in payload["result"].get("data") or []:
                    code = row.get("SECURITY_CODE")
                    if code in batch:
                        result[code].append(row)
        except Exception:
            continue
    return result


def _history_name(history):
    for item in history:
        name = item.get("SECURITY_NAME_ABBR")
        if name:
            return name
    return ""


# 批量读取单批股票数：history_json 单行可达数十 KB，分批控制单条 SQL 与结果集大小
_CACHE_QUERY_BATCH_SIZE = 250


def _read_dividend_history_cache_batch(db, codes):
    """批量读取派息历史缓存，返回 {code: (cache_row, history)}；分批 IN 查询，
    替代逐股查询（页面流水线每股一次 → 全量约 4 次）。"""
    result = {}
    for i in range(0, len(codes), _CACHE_QUERY_BATCH_SIZE):
        batch = codes[i:i + _CACHE_QUERY_BATCH_SIZE]
        placeholders = ",".join(["%s"] * len(batch))
        rows = db.query(f"""
            SELECT `code`, `name`, `history_json`, `history_hash`, `checked_on`, `checked_at`,
                   `changed_at`, `changed_report_date`
            FROM `{_DIVIDEND_HISTORY_CACHE_TABLE}`
            WHERE `code` IN ({placeholders})
        """, *batch)
        for row in rows:
            try:
                result[row["code"]] = (row, json.loads(row["history_json"]))
            except Exception:
                result[row["code"]] = (row, [])
    return result


def _is_dividend_history_cache_stale(cache_row, history, now):
    return _is_daily_report_cache_stale(
        cache_row,
        now,
        after_close_interval_hours=_REPORT_AFTER_CLOSE_REFRESH_INTERVAL_HOURS
    )


def _write_dividend_history_cache(db, code, history, now, changed):
    changed_at = now.strftime("%Y-%m-%d %H:%M:%S") if changed else None
    changed_report_date = _changed_report_date(now).strftime("%Y-%m-%d") if changed else None
    db.execute(f"""
        INSERT INTO `{_DIVIDEND_HISTORY_CACHE_TABLE}`
            (`code`, `name`, `history_json`, `history_hash`, `checked_on`, `checked_at`,
             `changed_at`, `changed_report_date`)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `name` = VALUES(`name`),
            `history_json` = VALUES(`history_json`),
            `history_hash` = VALUES(`history_hash`),
            `checked_on` = VALUES(`checked_on`),
            `checked_at` = VALUES(`checked_at`),
            `changed_at` = IF(VALUES(`changed_at`) IS NULL, `changed_at`, VALUES(`changed_at`)),
            `changed_report_date` = IF(VALUES(`changed_report_date`) IS NULL,
                                       `changed_report_date`,
                                       VALUES(`changed_report_date`))
    """,
               code,
               _history_name(history),
               json.dumps(history, ensure_ascii=False, default=_json_default),
               _history_hash(history),
               now.strftime("%Y-%m-%d"),
               now.strftime("%Y-%m-%d %H:%M:%S"),
               changed_at,
               changed_report_date)


def _has_recent_dividend_notice(history, changed_at):
    if changed_at is None:
        return False
    if isinstance(changed_at, str):
        changed_at = datetime.datetime.fromisoformat(changed_at)
    notice_threshold = changed_at.date() - datetime.timedelta(days=1)
    for item in history or []:
        for field in ("NOTICE_DATE", "PLAN_NOTICE_DATE"):
            notice_date = _date_text(item.get(field))
            if len(notice_date) >= 10 and datetime.date.fromisoformat(notice_date[:10]) >= notice_threshold:
                return True
    return False


def _refresh_dividend_histories(stock_codes):
    global _DIVIDEND_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        now = _now()
        # 先过滤过期股票，再批量抓取（每批10只）；批量读取一次，过期过滤与变更检测共用
        cache_by_code = _read_dividend_history_cache_batch(db, stock_codes)
        need_codes = [
            code for code in stock_codes
            if _is_dividend_history_cache_stale(*cache_by_code.get(code, (None, [])), now)
        ]
        if not need_codes:
            return
        histories = _fetch_dividend_histories_batch(need_codes)
        for code in need_codes:
            if code not in histories:
                continue  # 所在批次抓取失败，保持原缓存，下次重试
            fresh_history = histories[code]
            cache_row, history = cache_by_code.get(code, (None, []))
            old_hash = None
            if cache_row is not None:
                old_hash = cache_row.get("history_hash")
            fresh_hash = _history_hash(fresh_history)
            changed = (
                old_hash is not None
                and bool(history)
                and bool(fresh_history)
                and fresh_hash != old_hash
                and _has_recent_dividend_notice(fresh_history, now)
            )
            _write_dividend_history_cache(db, code, fresh_history, now, changed)
    except Exception as error:
        print(f"dividend._refresh_dividend_histories处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _DIVIDEND_REFRESH_LOCK:
            _DIVIDEND_REFRESH_RUNNING = False


def _schedule_dividend_history_refresh(stock_codes):
    global _DIVIDEND_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    with _DIVIDEND_REFRESH_LOCK:
        if _DIVIDEND_REFRESH_RUNNING:
            return
        _DIVIDEND_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_dividend_histories, args=(stock_codes,), daemon=True)
    thread.start()
    return thread


def _build_cached_dividend_history(cache_row, history, now):
    """由批量读取的派息缓存行装配派息历史（纯计算，不查库）。"""
    changed_at = None if cache_row is None else cache_row.get("changed_at")
    changed_report_date = None if cache_row is None else cache_row.get("changed_report_date")
    is_stale = _is_dividend_history_cache_stale(cache_row, history, now)
    dividend_changed = (
        _is_in_changed_display_window(changed_at, changed_report_date, now)
        and _has_recent_dividend_notice(history, changed_at)
    )
    return history, dividend_changed, is_stale


def _sum_fiscal_year_dividend(history, year):
    total_per_10 = 0.0
    details = []
    for item in history:
        report_date = _date_text(item.get("REPORT_DATE"))
        if not report_date.startswith(str(year)):
            continue

        cash_per_10 = _to_float(item.get("PRETAX_BONUS_RMB"))
        if cash_per_10 is None or cash_per_10 <= 0:
            continue

        total_per_10 += cash_per_10
        details.append({
            "name": item.get("SECURITY_NAME_ABBR") or "",
            "report_date": report_date,
            "cash_per_10": cash_per_10,
            "progress": item.get("ASSIGN_PROGRESS") or "",
            "plan": item.get("IMPL_PLAN_PROFILE") or "",
            "plan_notice_date": _date_text(item.get("PLAN_NOTICE_DATE")),
            "notice_date": _date_text(item.get("NOTICE_DATE")),
            "ex_dividend_date": _date_text(item.get("EX_DIVIDEND_DATE")),
        })

    return total_per_10, details


def _dividend_amounts_by_year(history):
    """按财年聚合现金派息总额（每10股），用于息增年计算与悬浮提示。"""
    dividends_by_year = {}
    for item in history:
        report_date = _date_text(item.get("REPORT_DATE"))
        if len(report_date) < 4:
            continue
        cash_per_10 = _to_float(item.get("PRETAX_BONUS_RMB"))
        if cash_per_10 is None or cash_per_10 <= 0:
            continue
        fiscal_year = int(report_date[:4])
        dividends_by_year[fiscal_year] = dividends_by_year.get(fiscal_year, 0.0) + cash_per_10
    return dividends_by_year


def _consecutive_non_decline_years(history, year):
    """返回息增年：从最近已完结财年起，派息不低于上一年的连续年数。

    持平（相等）算作继续，只有下降才中断；
    无派息的断档财年按 0 参与比较，恢复派息（0→正）算作增加；
    连续无派息年份不计数（0→0 中断），避免多年断档虚增年数；
    不超过已记录年份范围。
    """
    dividends_by_year = _dividend_amounts_by_year(history)

    if not dividends_by_year:
        return 0
    first_year = min(dividends_by_year)
    years = 0
    current_year = int(year)
    while current_year - 1 >= first_year:
        current = dividends_by_year.get(current_year, 0.0)
        previous = dividends_by_year.get(current_year - 1, 0.0)
        if current < previous:
            break
        if current == 0 and previous == 0:
            break
        years += 1
        current_year -= 1
    return years
