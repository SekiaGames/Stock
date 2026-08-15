#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import threading

import instock.lib.database as mdb
import instock.lib.mysql as mysql
import instock.core.stocklist as stocklist
import instock.core.dividend as dividend
from instock.core.common import (
    _to_float,
    _now,
    _date_text,
    _market_phase,
    _previous_trading_day,
    _ensure_cache_tables,
    _PRICE_CACHE_TABLE,
    _MA120_CACHE_TABLE,
    _KLINE_CACHE_TABLE,
    _SETTINGS_TABLE,
)

__author__ = 'myh '
__date__ = '2026/8/6 '

# 盘中行情刷新：高关注度（股息率≥4%，见 instock/config/high_attention_daily.txt）每次后台调度刷新，
# 非高关注度股票每 _PRICE_REFRESH_POLL_COUNT 次调度刷新一次（首次调度全部刷新，之后5次漏掉非高关注度，依次循环）
_PRICE_REFRESH_MINUTES_HIGH = 5
_PRICE_REFRESH_POLL_COUNT = 6
_PRICE_POLL_COUNT_KEY = "price_poll_count"
_KLINE_REFRESH_LOCK = threading.Lock()
_KLINE_REFRESH_RUNNING = False
_KLINE_REFRESH_ATTEMPTS = {}


def _stale_price_codes(cached_rows, stock_codes, high_attention_codes, now, refresh_all=False):
    """返回需要刷新现价缓存的代码。

    盘中：高关注度股票每次后台调度都刷新；
    非高关注度股票只在 refresh_all（每 _PRICE_REFRESH_POLL_COUNT 次调度一次）时刷新，
    其余调度仅补齐缓存缺失/现价无效的股票。
    盘前、周末不刷新；收盘后当日收盘值只更新一次（首次请求时刷新，之后保持）。
    """
    phase = _market_phase(now)
    if phase not in ("intraday", "after_close"):
        return []
    fetched_at_by_code = {row["code"]: row.get("fetched_at") for row in cached_rows}
    price_ok_by_code = {row["code"]: (_to_float(row.get("current_price")) or 0) > 0 for row in cached_rows}
    stale = []
    for code in stock_codes:
        fetched_at = fetched_at_by_code.get(code)
        if fetched_at is None or not price_ok_by_code.get(code, False):
            stale.append(code)
            continue
        if phase == "intraday":
            if code in high_attention_codes:
                if fetched_at <= now - datetime.timedelta(minutes=_PRICE_REFRESH_MINUTES_HIGH):
                    stale.append(code)
            elif refresh_all:
                stale.append(code)
        else:  # after_close：当日收盘后已更新过（>=15:00 当天）不再更新
            close_time = datetime.datetime.combine(now.date(), datetime.time(15, 0))
            if fetched_at < close_time or fetched_at.date() != now.date():
                stale.append(code)
    return stale


def _bump_price_poll_count(db, now):
    """递增盘中行情调度计数并返回递增后的值（settings 表，跨日归零重新计数）。

    首次调度返回 1，`(计数-1) % 倍数 == 0` 时为全量刷新调度（首次即命中）；
    每次调度 +1，供非高关注度股票分级刷新使用。盘后/盘前/周末不递增。
    """
    today_text = now.strftime("%Y-%m-%d")
    row = db.get(
        f"SELECT `setting_value`, `updated_at` FROM `{_SETTINGS_TABLE}` WHERE `setting_key` = %s",
        _PRICE_POLL_COUNT_KEY,
    )
    if row is not None and str(row.get("updated_at"))[:10] == today_text:
        try:
            value = int(row.get("setting_value")) + 1
        except Exception:
            value = 1
        db.execute(f"""
            UPDATE `{_SETTINGS_TABLE}`
            SET `setting_value` = %s, `updated_at` = %s
            WHERE `setting_key` = %s
        """, str(value), now.strftime("%Y-%m-%d %H:%M:%S"), _PRICE_POLL_COUNT_KEY)
    else:
        value = 1
        db.execute(f"""
            INSERT INTO `{_SETTINGS_TABLE}` (`setting_key`, `setting_value`, `updated_at`)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                `setting_value` = VALUES(`setting_value`),
                `updated_at` = VALUES(`updated_at`)
        """, _PRICE_POLL_COUNT_KEY, "1", now.strftime("%Y-%m-%d %H:%M:%S"))
    return value


def _read_price_cache(db, stock_codes):
    if not stock_codes:
        return []
    placeholders = ",".join(["%s"] * len(stock_codes))
    return db.query(f"""
        SELECT `code`, `name`, `price_date`, `current_price`, `pre_close_price`, `change_rate`, `fetched_at`, `market_phase`
        FROM `{_PRICE_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *stock_codes)


def _write_price_cache(db, price_data, now):
    phase = _market_phase(now)
    for row in price_data:
        current_price = _to_float(row.get("new_price"))
        pre_close_price = _to_float(row.get("pre_close_price"))
        change_rate = _to_float(row.get("change_rate"))
        if current_price is None or current_price <= 0:
            current_price = pre_close_price
        db.execute(f"""
            INSERT INTO `{_PRICE_CACHE_TABLE}`
                (`code`, `name`, `price_date`, `current_price`, `pre_close_price`, `change_rate`, `fetched_at`, `market_phase`)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                `name` = VALUES(`name`),
                `price_date` = VALUES(`price_date`),
                `current_price` = VALUES(`current_price`),
                `pre_close_price` = VALUES(`pre_close_price`),
                `change_rate` = VALUES(`change_rate`),
                `fetched_at` = VALUES(`fetched_at`),
                `market_phase` = VALUES(`market_phase`)
        """,
                   row.get("code"),
                   row.get("name"),
                   row.get("date"),
                   current_price,
                   pre_close_price,
                   change_rate,
                   now.strftime("%Y-%m-%d %H:%M:%S"),
                   phase)


def _sync_ma120_cache_for_prices(db, stock_codes, now):
    """用现价同步更新 MA120 缓存中的 close_price 和 ma120_position。"""
    price_rows = _read_price_cache(db, stock_codes)
    price_by_code = {row["code"]: _to_float(row.get("current_price")) for row in price_rows}

    ma120_rows = _read_ma120_cache(db, stock_codes)
    for code, row in ma120_rows.items():
        current_price = price_by_code.get(code)
        if current_price is None or current_price <= 0:
            continue
        ma120 = _to_float(row.get("ma120"))
        if ma120 is None or ma120 <= 0:
            continue
        new_ma120_pos = (current_price / ma120 - 1) * 100
        db.execute(f"""
            UPDATE `{_MA120_CACHE_TABLE}`
            SET `close_price` = %s, `ma120_position` = %s
            WHERE `code` = %s
        """, current_price, new_ma120_pos, code)


def _get_cached_price_rows(db, stock_codes, errors, high_attention_codes=None):
    """读取现价缓存，只抓取过期的股票刷新。

    盘中：高关注度每次后台调度刷新，非高关注度每 _PRICE_REFRESH_POLL_COUNT 次调度刷新一次
    （首次调度全部刷新，之后5次漏掉非高关注度，依次循环），调度计数存 settings 表。
    high_attention_codes 为当日高关注度股票代码集合（见 high_attention 模块），
    为 None 时按无高关注度处理（全部走每6次调度一档）。
    """
    now = _now()
    cached_rows = _read_price_cache(db, stock_codes)
    refresh_all = False
    if _market_phase(now) == "intraday":
        refresh_all = (_bump_price_poll_count(db, now) - 1) % _PRICE_REFRESH_POLL_COUNT == 0
    stale_codes = _stale_price_codes(cached_rows, stock_codes, high_attention_codes or set(), now, refresh_all)
    if stale_codes:
        try:
            price_data = stocklist.make_selected_stock_rows(now.date(), codes=stale_codes)
            if price_data is not None:
                _write_price_cache(db, price_data, now)
                cached_rows = _read_price_cache(db, stock_codes)
                # 同步更新 MA120 缓存（只更新本次刷新的股票）
                try:
                    _sync_ma120_cache_for_prices(db, stale_codes, now)
                except Exception:
                    pass
        except Exception as error:
            errors.append(f"行情缓存刷新失败，已使用旧缓存：{error}")
    return {row["code"]: row for row in cached_rows}


def _read_ma120_cache(db, stock_codes):
    if not stock_codes:
        return {}
    placeholders = ",".join(["%s"] * len(stock_codes))
    rows = db.query(f"""
        SELECT `code`, `trade_date`, `close_price`, `ma120`, `ma120_position`, `fetched_at`
        FROM `{_MA120_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *stock_codes)
    return {row["code"]: row for row in rows}


_MA120_STAGE_PERCENT = 10


def _ma120_stage(position):
    """MA120 相对位置所处阶段序号，每 10% 为一个阶段（向下取整）。"""
    return int(position // _MA120_STAGE_PERCENT)


def _ma120_trade_signal(change_rate, pre_close_price, ma120_position, ma120):
    """判断 MA120 位置的买卖点信号。

    以 MA120 相对位置每 10% 为一个阶段，现价跨越阶段边界时触发提示：
    买点：涨跌幅为负、最新位置位于 0% 以下，且从更高阶段跨入更低阶段。
    卖点：涨跌幅为正、最新位置位于 10% 以上，且从更低阶段跨入更高阶段。
    涨跌幅来自行情接口，收盘后仍有效（收盘后昨收与现价重合，无法再用现价比较判断涨跌）。
    昨日阶段位置用昨日收盘价（K线缓存最新一根，前复权同口径）计算。
    返回 "buy"、"sell" 或空字符串。
    """
    if change_rate is None or pre_close_price is None or pre_close_price <= 0:
        return ""
    if ma120_position is None or ma120 is None or ma120 <= 0:
        return ""
    prev_position = (pre_close_price / ma120 - 1) * 100
    stage_diff = _ma120_stage(prev_position) - _ma120_stage(ma120_position)
    if change_rate < 0 and ma120_position < 0 and stage_diff > 0:
        return "buy"
    if change_rate > 0 and ma120_position > 10 and stage_diff < 0:
        return "sell"
    return ""


def _recent_pre_close(recent, phase, today_text, pre_open_expected_kline_date):
    """从K线缓存最近两根收盘价中取昨日收盘价（前复权），供买卖点信号判断使用。

    盘中最新一根即上一交易日收盘；
    收盘后当天K线已入库时，最新一根为当天、倒数第二根代表昨日（买卖点提示盘后依然有效）；
    盘前（0点至9点半开盘）与休市（周末）K线仍为上一交易日，同样用倒数第二根延续盘后提示。
    recent 为 [(日期, 收盘), (日期, 收盘)]（新→旧），无缓存时返回 None。
    """
    if not recent:
        return None
    latest_date, latest_close = recent[0]
    if len(recent) > 1 and (
        (phase == "after_close" and latest_date == today_text)
        or (phase in ("before_open", "closed") and latest_date == pre_open_expected_kline_date)
    ):
        # 盘后当天K线已入库 / 盘前、休市延续盘后提示：昨日收盘用倒数第二根
        return recent[1][1]
    # 盘中/盘后未更新/假日：最新一根即上一交易日收盘
    return latest_close


def _is_ma120_cache_stale(cache_row, now):
    phase = _market_phase(now)
    if cache_row is None:
        return True
    fetched_at = cache_row.get("fetched_at")
    if not fetched_at:
        return True
    if phase in ("intraday", "before_open"):
        # 下午3点前使用前一交易日收盘数据
        prev_trading_day = _previous_trading_day(now.date())
        prev_close = datetime.datetime.combine(prev_trading_day, datetime.time(15, 0))
        return fetched_at < prev_close
    if phase == "after_close":
        close_time = datetime.datetime.combine(now.date(), datetime.time(15, 0))
        return fetched_at < close_time
    return fetched_at.date() < now.date()


def _kline_refresh_window(now):
    phase = _market_phase(now)
    if phase == "before_open":
        phase = "intraday"
    return f"{now.date()}:{phase}"


def _write_ma120_cache(db, code, ma120_row, now):
    db.execute(f"""
        INSERT INTO `{_MA120_CACHE_TABLE}`
            (`code`, `trade_date`, `close_price`, `ma120`, `ma120_position`, `fetched_at`)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `trade_date` = VALUES(`trade_date`),
            `close_price` = VALUES(`close_price`),
            `ma120` = VALUES(`ma120`),
            `ma120_position` = VALUES(`ma120_position`),
            `fetched_at` = VALUES(`fetched_at`)
    """,
               code,
               ma120_row.get("trade_date"),
               ma120_row.get("close_price"),
               ma120_row.get("ma120"),
               ma120_row.get("ma120_position"),
               now.strftime("%Y-%m-%d %H:%M:%S"))


def _expected_kline_date(now):
    """K线缓存应包含的最晚已完成交易日（YYYY-MM-DD 字符串）。

    工作日收盘后：当天K线已完成，期望今天；
    盘中/盘前/周末：期望最近一个交易日（跨周末回到周五）。
    """
    phase = _market_phase(now)
    if phase == "after_close":
        return now.date().isoformat()
    return _previous_trading_day(now.date()).isoformat()


# 批量读取单批股票数：每股最多125根K线，分批控制单条 SQL 与结果集大小
_KLINE_QUERY_BATCH_SIZE = 250


def _read_kline_cache_batch(db, codes):
    """批量读取每股最近125根日K，返回 {code: 与接口一致的升序 [(trade_date, close, high, low), ...]}；
    分批 IN 查询 + ROW_NUMBER 截断，替代刷新线程逐股查询。"""
    if not codes:
        return {}
    result = {}
    for i in range(0, len(codes), _KLINE_QUERY_BATCH_SIZE):
        batch = codes[i:i + _KLINE_QUERY_BATCH_SIZE]
        placeholders = ",".join(["%s"] * len(batch))
        rows = db.query(f"""
            SELECT `code`, rn, `trade_date`, `close_price`, `high_price`, `low_price` FROM (
                SELECT `code`, `trade_date`, `close_price`, `high_price`, `low_price`,
                       ROW_NUMBER() OVER (PARTITION BY `code` ORDER BY `trade_date` DESC) AS rn
                FROM `{_KLINE_CACHE_TABLE}`
                WHERE `code` IN ({placeholders})
            ) t WHERE rn <= 125 ORDER BY `code`, rn
        """, *batch)
        for row in rows:
            result.setdefault(row["code"], []).append(
                (str(row["trade_date"])[:10],
                 _to_float(row.get("close_price")),
                 _to_float(row.get("high_price")),
                 _to_float(row.get("low_price"))))
    # SQL 内按 rn 升序（新→旧），反转成与 _read_kline_cache 一致的升序（旧→新）
    return {code: items[::-1] for code, items in result.items()}


def _write_kline_cache(db, code, rows):
    """覆盖写入该股票K线缓存，最多125根，多余的直接删除。"""
    db.execute(f"DELETE FROM `{_KLINE_CACHE_TABLE}` WHERE `code` = %s", code)
    if not rows:
        return
    placeholders = ",".join(["(%s, %s, %s, %s, %s)"] * len(rows))
    values = [v for row in rows for v in (code, row[0], row[1], row[2], row[3])]
    db.execute(f"""
        INSERT INTO `{_KLINE_CACHE_TABLE}`
            (`code`, `trade_date`, `close_price`, `high_price`, `low_price`)
        VALUES {placeholders}
    """, *values)


def _read_recent_kline_closes(db, stock_codes):
    """读取每股K线缓存最近两根的收盘价（前复权），返回 {code: [(日期, 收盘), (日期, 收盘)]}（新→旧）。

    盘中最新一根为上一交易日收盘；
    收盘后当天K线已入库时，最新一根为当天、倒数第二根代表昨日，供买卖点信号盘后使用。
    """
    if not stock_codes:
        return {}
    placeholders = ",".join(["%s"] * len(stock_codes))
    rows = db.query(f"""
        SELECT code, rn, trade_date, close_price FROM (
            SELECT `code`, `trade_date`, `close_price`,
                   ROW_NUMBER() OVER (PARTITION BY `code` ORDER BY `trade_date` DESC) AS rn
            FROM `{_KLINE_CACHE_TABLE}`
            WHERE `code` IN ({placeholders})
        ) t WHERE rn <= 2 ORDER BY `code`, rn
    """, *stock_codes)
    result = {}
    for row in rows:
        result.setdefault(row["code"], []).append(
            (str(row["trade_date"])[:10], _to_float(row.get("close_price"))))
    return result


def _has_pending_ex_dividend(history, cached_max_date):
    """缓存最新一根是除息日时，前复权价可能抓取于除息调整生效前，需要重新请求。

    前复权（qfq）价格在除息日会整体调整，日期判断无法发现，
    借助派息缓存的除息日（EX_DIVIDEND_DATE）强制更新：
    除息日 >= 缓存最新交易日 即重新请求125根。
    （除息检测纯计算：history 由调用方批量读取传入，不查库。）
    """
    for item in history or []:
        ex_date = _date_text(item.get("EX_DIVIDEND_DATE"))
        if ex_date and ex_date >= cached_max_date:
            return True
    return False


def _refresh_kline_metrics(stock_codes):
    """刷新 MA120：K线缓存已含最新交易日则直接用缓存计算，否则重新请求覆盖。

    K线缓存为空或缺少最新交易日K线（如容器停机未更新）时请求一次125根并覆盖；
    缓存已含最新交易日时不再请求外部接口，仅用缓存重算并写入过期的指标缓存。
    """
    global _KLINE_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        now = _now()
        phase = _market_phase(now)
        # 下午3点前：日期朝前一交易日挪，排除当日未完成K线
        effective_today = now.date() if phase in ("intraday", "before_open") else None
        expected_date = _expected_kline_date(now)
        # 读取实时价格，优先用于位置计算
        price_rows = _read_price_cache(db, stock_codes)
        price_by_code = {row["code"]: row for row in price_rows}
        ma120_by_code = _read_ma120_cache(db, stock_codes)
        stale_codes = [
            code for code in stock_codes
            if _is_ma120_cache_stale(ma120_by_code.get(code), now)
        ]
        # 只对过期股票批量读取K线与派息历史（除息日判断用），替代逐股查询
        kline_by_code = _read_kline_cache_batch(db, stale_codes)
        dividend_cache_by_code = dividend._read_dividend_history_cache_batch(db, stale_codes)
        for code in stale_codes:
            try:
                ma120_row = ma120_by_code.get(code, {})

                price_row = price_by_code.get(code)
                current_price = _to_float(price_row.get("current_price")) if price_row else None

                rows = kline_by_code.get(code)
                if not rows or rows[-1][0] < expected_date or _has_pending_ex_dividend(
                        dividend_cache_by_code.get(code, (None, []))[1], rows[-1][0]):
                    rows = stocklist.fetch_daily_kline_rows(code, today=effective_today)
                    if rows is None or not rows:
                        continue
                    _write_kline_cache(db, code, rows)

                metrics = stocklist.compute_kline_metrics(rows, current_price)
                if metrics.get("ma120") is not None and _is_ma120_cache_stale(ma120_row, now):
                    _write_ma120_cache(db, code, metrics["ma120"], now)
            except Exception as error:
                # 单只股票请求失败只跳过该股，不影响其余股票
                print(f"market_quotes K线刷新跳过 {code}：{error}")
    except Exception as error:
        print(f"market_quotes._refresh_kline_metrics处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _KLINE_REFRESH_LOCK:
            _KLINE_REFRESH_RUNNING = False


def _schedule_kline_refresh(stock_codes):
    global _KLINE_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    window = _kline_refresh_window(_now())
    if window is None:
        return
    with _KLINE_REFRESH_LOCK:
        if _KLINE_REFRESH_RUNNING:
            return
        stock_codes = tuple(
            code for code in stock_codes
            if _KLINE_REFRESH_ATTEMPTS.get(code) != window
        )
        if not stock_codes:
            return
        for code in stock_codes:
            _KLINE_REFRESH_ATTEMPTS[code] = window
        _KLINE_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_kline_metrics, args=(stock_codes,), daemon=True)
    thread.start()
    return thread




