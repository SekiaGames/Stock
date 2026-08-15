#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import json
import threading

import instock.core.stocklist as stocklist
import instock.core.followlist as followlist
import instock.core.blocklist as blocklist
import instock.core.signal_notify as signal_notify
import instock.core.high_attention as high_attention
import instock.web.base as webBase
import instock.web.scheduler as scheduler
from instock.core.common import (
    _to_float,
    _now,
    _date_text,
    _json_default,
    _market_phase,
    _previous_trading_day,
    _ensure_cache_tables,
    _get_or_sync_fiscal_year_base,
)
from instock.core.market_quotes import (
    _get_cached_price_rows,
    _read_price_cache,
    _read_ma120_cache,
    _read_low20_cache,
    _read_high20_cache,
    _read_recent_kline_closes,
    _is_ma120_cache_stale,
    _is_low20_cache_stale,
    _is_high20_cache_stale,
    _recent_pre_close,
    _ma120_trade_signal,
    _schedule_kline_refresh,
)
from instock.core.profile import (
    _get_cached_profile_rows,
    _is_industry_cache_stale,
    _is_market_cap_cache_stale,
    _schedule_industry_refresh,
    _schedule_market_cap_refresh,
)
from instock.core.dividend import (
    _build_cached_dividend_history,
    _read_dividend_history_cache_batch,
    _sum_fiscal_year_dividend,
    _consecutive_non_decline_years,
    _dividend_amounts_by_year,
    _schedule_dividend_history_refresh,
)
from instock.core.financial import (
    _build_cached_narrow_fcf,
    _build_latest_finance_report,
    _read_cashflow_cache_batch,
    _read_finance_report_cache_batch,
    _report_season_period,
    _schedule_finance_report_refresh,
    _schedule_cashflow_refresh,
)

__author__ = 'myh '
__date__ = '2026/5/12 '

# 刷新流水线单飞锁：后台定时调度调用时，已在执行则并发方走只读快照，
# 不重复请求外部接口；前端页面轮询固定走只读路径（refresh=False），不触碰锁
_PIPELINE_LOCK = threading.Lock()
_PIPELINE_RUNNING = False

# 进程内只读快照：数据只随后台调度变化，两次调度之间页面轮询可直接复用快照，
# 不查库不装配（页面每 1 分钟轮询一次，调度 5 分钟一次，缓存命中率约 80%）；
# 调度刷新（refresh=True）与只读装配完成时更新，按（交易日, 市场阶段）区分，跨日/跨阶段自动重建
_SNAPSHOT_LOCK = threading.Lock()
_READONLY_SNAPSHOT = None
_READONLY_SNAPSHOT_KEY = None


def _snapshot_key(now):
    return (now.date().isoformat(), _market_phase(now))


def _filter_rows_for_frontend(rows, get_argument):
    """按前端过滤设置过滤行数据（与前端 shouldHideByFilter 语义一致，见
    instock/web/templates/high_dividend.html）：后端只返回符合过滤条件的行，
    缩小轮询响应体积。过滤在只读快照之上按请求参数执行，不重建快照，
    不同过滤组合之间互不影响快照复用。"""
    def _parse_positive_float(name):
        value = get_argument(name, "", True)
        if value is None or value == "":
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        # 与前端一致：<=0 视为未设置
        return number if number > 0 else None

    def _parse_bool(name):
        return str(get_argument(name, "", True)).strip().lower() in ("1", "true", "yes", "on")

    min_dividend_yield = _parse_positive_float("min_dividend_yield")
    min_market_cap = _parse_positive_float("min_market_cap")
    min_dividend_growth_years = _parse_positive_float("min_dividend_growth_years")
    deducted_profit_filter = _parse_bool("deducted_profit_filter")
    watch_filter = _parse_bool("watch_filter")
    if not any((min_dividend_yield, min_market_cap, min_dividend_growth_years,
                deducted_profit_filter, watch_filter)):
        return rows
    follow_codes = set(followlist.get_follow_codes()) if watch_filter else None
    filtered = []
    for row in rows:
        # 过滤扣非：扣非增速已知且低于-10%时隐藏（未知不隐藏，与前端一致）
        if deducted_profit_filter and row.get("deducted_profit_growth") is not None \
                and row["deducted_profit_growth"] < -10:
            continue
        # 关注：仅显示已关注的股票
        if watch_filter and row.get("code") not in follow_codes:
            continue
        # 最低股息率%：股息率未知（派息历史未抓取）时不隐藏，与前端 hasKnownValue 一致
        if min_dividend_yield is not None and row.get("dividend_yield") is not None \
                and row["dividend_yield"] < min_dividend_yield:
            continue
        # 最低市值（亿元）：市值未知（未抓取）或低于阈值均隐藏
        if min_market_cap is not None and (row.get("market_cap") is None
                                           or row["market_cap"] < min_market_cap):
            continue
        # 最低息增年：息增年未知或低于阈值均隐藏
        if min_dividend_growth_years is not None and (row.get("dividend_growth_years") is None
                                                      or row["dividend_growth_years"] < min_dividend_growth_years):
            continue
        filtered.append(row)
    return filtered


def _publish_readonly_snapshot(result, now):
    """发布只读快照：后台调度或只读装配完成后调用，页面轮询后续直接复用。"""
    global _READONLY_SNAPSHOT, _READONLY_SNAPSHOT_KEY
    with _SNAPSHOT_LOCK:
        _READONLY_SNAPSHOT = result
        _READONLY_SNAPSHOT_KEY = _snapshot_key(now)


def _get_readonly_result(db, now):
    """页面轮询只读路径：命中进程内快照直接返回；无快照（首次加载/跨日跨阶段）时
    执行一次只读装配并更新快照（装配完成时发布）。"""
    key = _snapshot_key(now)
    with _SNAPSHOT_LOCK:
        if _READONLY_SNAPSHOT is not None and _READONLY_SNAPSHOT_KEY == key:
            return _READONLY_SNAPSHOT
    return _refresh_pipeline(db, now, refresh=False)


def _refresh_pipeline(db, now, refresh):
    """执行完整刷新流水线（数据抓取、缓存写入、信号/高关注度文件写入）。

    refresh=False 为并发兜底路径：只读缓存装配行数据，跳过全部对外请求与
    文件写入副作用（由正在执行的刷新方完成）。
    返回 dict：rows（已排序）、errors、total_stock_count、report_season、fiscal_year_base。
    """
    _ensure_cache_tables(db)
    # 财年基准年份（settings 表）：跨年自动重置，旧/新财年收益随其平移
    fiscal_year_base = _get_or_sync_fiscal_year_base(db)
    # 当前财报披露季的目标报告期（读取 instock/config/report_season.txt，见 _report_season_period），
    # 非财报季或配置关闭时为 None，前端隐藏该列；单股判断共用同一结果，避免每股重复读配置
    report_season = _report_season_period(now)
    stock_codes = [code for code in stocklist.get_stock_codes() if stocklist.is_a_stock_code(code)]
    total_stock_count = len(stock_codes)
    rows = []
    errors = []
    stock_names = stocklist.get_stock_names()

    # 屏蔽 blocklist_industry.txt 中指定的申万二级行业，被屏蔽的股票不再读取缓存、不再刷新
    blocked_industries = stocklist.get_blocked_industries()
    blocked_industry_stock_codes = set()
    if blocked_industries:
        # 先从 blocklist_industryStocks.txt 缓存读取已屏蔽股票，避免重复判断行业
        blocked_industry_stock_codes = set(blocklist.get_blocked_codes(blocklist.INDUSTRY_STOCKS_FILE))
        if blocked_industry_stock_codes:
            stock_codes = [code for code in stock_codes if code not in blocked_industry_stock_codes]
    # 高关注度股票（股息率≥4%，见 instock/config/high_attention_daily.txt）：每次调度刷新，其余每6次调度刷新一次
    high_attention_codes = high_attention.get_high_attention_codes()
    if refresh:
        price_by_code = _get_cached_price_rows(db, stock_codes, errors, high_attention_codes)
    else:
        # 并发兜底：不请求外部行情、不bump轮询计数，直接读缓存（刷新方正在执行，快照至多旧几秒）
        price_by_code = {row["code"]: row for row in _read_price_cache(db, stock_codes)}
    profile_by_code = _get_cached_profile_rows(db, stock_codes)
    # 昨日收盘价：K线缓存最近两根收盘（前复权）——盘中最新一根即上一交易日，
    # 收盘后当天K线已入库时最新一根为当天、倒数第二根代表昨日（买卖点提示盘后依然有效）；
    # 盘前（0点至9点半开盘）与休市（周末）K线仍为上一交易日，同样用倒数第二根延续盘后提示
    kline_recent_by_code = _read_recent_kline_closes(db, stock_codes)
    today_text = now.date().isoformat()
    phase = _market_phase(now)
    pre_open_expected_kline_date = _previous_trading_day(now.date()).isoformat()
    if blocked_industries:
        # 未缓存的股票：命中屏蔽行业的自动记录到缓存文件并屏蔽
        for code in stock_codes:
            if code in blocked_industry_stock_codes:
                continue
            profile = profile_by_code.get(code)
            if profile is None:
                continue  # 暂无行业信息，本次不判断
            if (profile.get("industry_name") or "") in blocked_industries:
                blocklist.add_blocked(blocklist.INDUSTRY_STOCKS_FILE, code, stock_names.get(code, ""))
                blocked_industry_stock_codes.add(code)
        if blocked_industry_stock_codes:
            stock_codes = [code for code in stock_codes if code not in blocked_industry_stock_codes]
    # 屏蔽 blocklist_negativeEps.txt 中记录的收益（最新已完结财年年报稀释每股收益）为负或0的股票，被屏蔽的股票不再读取缓存、不再刷新
    blocked_negative_eps_codes = set(blocklist.get_blocked_codes(blocklist.NEGATIVE_EPS_FILE))
    if blocked_negative_eps_codes:
        stock_codes = [code for code in stock_codes if code not in blocked_negative_eps_codes]
    # 屏蔽 blocklist_dividendYieldBelowOne.txt 中记录的股息率低于1%的股票，被屏蔽的股票不再读取缓存、不再刷新
    blocked_yield_below_one_codes = set(blocklist.get_blocked_codes(blocklist.YIELD_BELOW_ONE_FILE))
    if blocked_yield_below_one_codes:
        stock_codes = [code for code in stock_codes if code not in blocked_yield_below_one_codes]
    # 屏蔽 blocklist_dividendGrowthYearZero.txt 中记录的息增年为0的股票，被屏蔽的股票不再读取缓存、不再刷新
    blocked_zero_growth_codes = set(blocklist.get_blocked_codes(blocklist.GROWTH_YEAR_ZERO_FILE))
    if blocked_zero_growth_codes:
        stock_codes = [code for code in stock_codes if code not in blocked_zero_growth_codes]
    ma120_by_code = _read_ma120_cache(db, stock_codes)
    low20_by_code = _read_low20_cache(db, stock_codes)
    high20_by_code = _read_high20_cache(db, stock_codes)
    stale_ma120_codes = [
        code for code in stock_codes
        if _is_ma120_cache_stale(ma120_by_code.get(code), now)
    ]
    stale_low20_codes = [
        code for code in stock_codes
        if _is_low20_cache_stale(low20_by_code.get(code), now)
    ]
    stale_high20_codes = [
        code for code in stock_codes
        if _is_high20_cache_stale(high20_by_code.get(code), now)
    ]
    # 行业只抓一次不刷新；市值每周刷新
    stale_industry_codes = [
        code for code in stock_codes
        if _is_industry_cache_stale(profile_by_code.get(code), now)
    ]
    stale_market_cap_codes = [
        code for code in stock_codes
        if _is_market_cap_cache_stale(profile_by_code.get(code), now)
    ]
    stale_dividend_codes = []
    stale_finance_codes = []
    stale_cashflow_codes = []
    blocked_this_run_codes = set()

    # 批量读取财报/派息/现金流缓存（各约 4 次分批 IN 查询），替代逐股查询：
    # 每只股票 4 次单行查询（财报读 2 次）→ 全量约 12 次，避免每次页面请求上千次查询
    finance_cache_by_code = _read_finance_report_cache_batch(db, stock_codes)
    dividend_cache_by_code = _read_dividend_history_cache_batch(db, stock_codes)
    cashflow_cache_by_code = _read_cashflow_cache_batch(db, stock_codes)

    for code in stock_codes:
        price_row = price_by_code.get(code)
        current_price = None if price_row is None else _to_float(price_row.get("current_price"))
        change_rate = None if price_row is None else _to_float(price_row.get("change_rate"))
        recent = kline_recent_by_code.get(code)
        # 昨日收盘价：K线缓存最近两根收盘（前复权）——盘中最新一根即上一交易日，
        # 收盘后当天K线已入库时最新一根为当天、倒数第二根代表昨日（买卖点提示盘后依然有效）；
        # 盘前（0点至9点半开盘）与休市（周末）K线仍为上一交易日，同样用倒数第二根延续盘后提示
        pre_close_price = _recent_pre_close(recent, phase, today_text, pre_open_expected_kline_date)
        finance_cache_row, finance_history = finance_cache_by_code.get(code, (None, []))
        try:
            finance_report, finance_report_stale = _build_latest_finance_report(
                finance_cache_row, finance_history, now, fiscal_year_base, report_season=report_season)
            if finance_report_stale:
                stale_finance_codes.append(code)
        except Exception as error:
            finance_report = {}
            errors.append(f"{code} 财报数据读取失败：{error}")

        # 最新已完结财年：检测到新财年（基准-1）年报 → 新财年，否则为旧财年（基准-2）；
        # 年报数据未抓取（finance_fetched=False）时无法判断年份，暂不计算股息率/息增年
        if finance_report.get("finance_fetched"):
            latest_fiscal_year = fiscal_year_base - 1 if finance_report.get("new_year_eps_report_date") else fiscal_year_base - 2
        else:
            latest_fiscal_year = None

        dividend_cache_row, dividend_history = dividend_cache_by_code.get(code, (None, []))
        try:
            history, dividend_changed, dividend_history_stale = _build_cached_dividend_history(
                dividend_cache_row, dividend_history, now)
            if dividend_history_stale:
                stale_dividend_codes.append(code)
            dividend_year = latest_fiscal_year
            if dividend_year is None:
                # 年报数据未抓取：无法判断财年，股息率/息增年暂不计算（显示--），等抓取完成后计算
                dividend_per_10 = None
                dividend_per_share = None
                dividend_yield = None
                dividend_growth_years = None
                dividend_amount_by_year = []
                details = []
            else:
                dividend_per_10, details = _sum_fiscal_year_dividend(history, dividend_year)
                dividend_growth_years = _consecutive_non_decline_years(history, dividend_year)
                # 息增年悬浮提示：连续增长段（growth+1 年）加中断对比年，共 growth+2 年，每股派息额
                year_totals = _dividend_amounts_by_year(history)
                first_year = min(year_totals) if year_totals else dividend_year
                window_start = max(first_year, dividend_year - dividend_growth_years - 1)
                dividend_amount_by_year = [
                    {"year": year, "per_share": round(year_totals.get(year, 0.0) / 10, 4)}
                    for year in range(dividend_year, window_start - 1, -1)
                ]
                dividend_per_share = dividend_per_10 / 10
                # 股息率未知（派息历史未抓取）时为 None 显示 --；
                # 派息历史已抓取但最近财年无派息时股息率真实为 0（由股息率<1%规则屏蔽）
                dividend_yield = None
                if dividend_per_share > 0 and current_price and current_price > 0:
                    dividend_yield = dividend_per_share / current_price * 100
                elif dividend_per_share <= 0 and (history or not dividend_history_stale):
                    dividend_yield = 0.0
        except Exception as error:
            # history/dividend_history_stale 供后续屏蔽判断使用，读取失败按未抓取处理
            history = []
            dividend_history_stale = True
            dividend_year = latest_fiscal_year
            dividend_per_10 = 0.0
            dividend_per_share = 0.0
            dividend_yield = None
            details = []
            dividend_changed = False
            dividend_growth_years = 0
            dividend_amount_by_year = []
            errors.append(f"{code} 派息数据读取失败：{error}")

        # 屏蔽优先级：行业（请求开始已处理）→ 收益 → 股息率 → 息增年
        # 收益（最新已完结财年年报稀释每股收益）为负或0：自动记录到 blocklist_negativeEps.txt 并屏蔽，不再读取缓存、不再刷新
        if finance_report.get("diluted_eps") is not None and finance_report.get("diluted_eps") <= 0:
            blocklist.add_blocked(blocklist.NEGATIVE_EPS_FILE, code, stock_names.get(code, ""))
            blocked_this_run_codes.add(code)
            continue
        # 股息率低于1%（只需派息历史与价格）：自动记录到 blocklist_dividendYieldBelowOne.txt 并屏蔽，不再读取缓存、不再刷新
        if dividend_yield is not None and dividend_yield < 1 and history:
            blocklist.add_blocked(blocklist.YIELD_BELOW_ONE_FILE, code, stock_names.get(code, ""))
            blocked_this_run_codes.add(code)
            continue
        # 息增年为0（只需派息历史）：自动记录到 blocklist_dividendGrowthYearZero.txt 并屏蔽，不再读取缓存、不再刷新
        # 新上市无分红公司派息历史凑不出2个财年，息增年同样为0：
        # 派息历史非空，或已确认检查过（缓存不旧）即为空，都视为息增年为0屏蔽；
        # 尚未抓取到派息历史（缓存过期待抓）的不屏蔽，避免误杀。
        if dividend_growth_years == 0 and (history or not dividend_history_stale):
            blocklist.add_blocked(blocklist.GROWTH_YEAR_ZERO_FILE, code, stock_names.get(code, ""))
            blocked_this_run_codes.add(code)
            continue

        cashflow_cache_row, cashflow_history = cashflow_cache_by_code.get(code, (None, []))
        try:
            fcf_data, cashflow_stale = _build_cached_narrow_fcf(
                finance_history, cashflow_cache_row, cashflow_history, now)
            if cashflow_stale:
                stale_cashflow_codes.append(code)
        except Exception as error:
            fcf_data = {}
            errors.append(f"{code} 现金流数据读取失败：{error}")

        ma120_row = ma120_by_code.get(code, {})
        ma120_position = None if not ma120_row else _to_float(ma120_row.get("ma120_position"))
        low20_row = low20_by_code.get(code, {})
        low20_bounce = None if not low20_row else _to_float(low20_row.get("bounce_position"))
        high20_row = high20_by_code.get(code, {})
        high20_decline = None if not high20_row else _to_float(high20_row.get("decline_position"))
        narrow_fcf = fcf_data.get("narrow_fcf")
        fcf_dividend = None
        fcf_price = None
        if narrow_fcf is not None:
            if dividend_per_share > 0:
                fcf_dividend = narrow_fcf / dividend_per_share
            if current_price and current_price > 0:
                fcf_price = narrow_fcf / current_price * 100
        elif fcf_data.get("narrow_fcf_skipped"):
            # 金融行业不适用窄口径FCF：用稀释每股收益替代
            # （收益/每股派息 = 派息覆盖率，收益/现价 = 盈利收益率）
            eps = finance_report.get("diluted_eps")
            if eps is not None and eps > 0:
                if dividend_per_share > 0:
                    fcf_dividend = eps / dividend_per_share
                if current_price and current_price > 0:
                    fcf_price = eps / current_price * 100
        name = stock_names.get(code, "")

        row = {
            "code": code,
            "name": name,
            "deducted_profit_growth": finance_report.get("deducted_profit_growth"),
            "deducted_profit_growth_report_date": finance_report.get("report_date", ""),
            "deducted_profit_growth_report_name": finance_report.get("report_name", ""),
            "deducted_profit_growth_notice_date": finance_report.get("notice_date", ""),
            "deducted_profit": finance_report.get("deducted_profit"),
            "diluted_eps": finance_report.get("diluted_eps"),
            "diluted_eps_field": finance_report.get("diluted_eps_field", ""),
            "diluted_eps_report_date": finance_report.get("diluted_eps_report_date", ""),
            "diluted_eps_report_name": finance_report.get("diluted_eps_report_name", ""),
            "old_year_eps": finance_report.get("old_year_eps"),
            "old_year_eps_field": finance_report.get("old_year_eps_field", ""),
            "old_year_eps_report_date": finance_report.get("old_year_eps_report_date", ""),
            "old_year_eps_report_name": finance_report.get("old_year_eps_report_name", ""),
            "new_year_eps": finance_report.get("new_year_eps"),
            "new_year_eps_field": finance_report.get("new_year_eps_field", ""),
            "new_year_eps_report_date": finance_report.get("new_year_eps_report_date", ""),
            "new_year_eps_report_name": finance_report.get("new_year_eps_report_name", ""),
            "finance_report_changed": finance_report.get("report_changed", False),
            "period_report_published": finance_report.get("period_report_published", False),
            "industry_name": "" if profile_by_code.get(code) is None else (profile_by_code[code].get("industry_name") or ""),
            "narrow_fcf": narrow_fcf,
            "narrow_fcf_report_date": fcf_data.get("narrow_fcf_report_date", ""),
            "narrow_fcf_report_name": fcf_data.get("narrow_fcf_report_name", ""),
            "narrow_fcf_total": fcf_data.get("narrow_fcf_total"),
            "narrow_fcf_metric": fcf_data.get("narrow_fcf_metric", "fcf"),
            "narrow_fcf_metric_name": fcf_data.get("narrow_fcf_metric_name", "窄口径FCF"),
            "netcash_operate": fcf_data.get("netcash_operate"),
            "construct_long_asset": fcf_data.get("construct_long_asset"),
            "total_share": fcf_data.get("total_share"),
            "eps_field": fcf_data.get("eps_field", ""),
            "narrow_fcf_skipped": fcf_data.get("narrow_fcf_skipped", False),
            "narrow_fcf_skip_reason": fcf_data.get("narrow_fcf_skip_reason", ""),
            "fcf_dividend": fcf_dividend,
            "fcf_price": fcf_price,
            "ma120_trade_date": "" if not ma120_row else _date_text(ma120_row.get("trade_date")),
            "ma120_time": "" if not ma120_row else (
                ma120_row["fetched_at"].strftime("%H:%M:%S")
                if hasattr(ma120_row.get("fetched_at"), "strftime") else str(ma120_row.get("fetched_at") or "")[11:19]),
            "ma120_close_price": None if not ma120_row else _to_float(ma120_row.get("close_price")),
            "ma120": None if not ma120_row else _to_float(ma120_row.get("ma120")),
            "ma120_position": ma120_position,
            "ma120_signal": _ma120_trade_signal(
                change_rate,
                pre_close_price,
                ma120_position,
                None if not ma120_row else _to_float(ma120_row.get("ma120"))),
            "low20_trade_date": "" if not low20_row else _date_text(low20_row.get("trade_date")),
            "low20_time": "" if not low20_row else (
                low20_row["fetched_at"].strftime("%H:%M:%S")
                if hasattr(low20_row.get("fetched_at"), "strftime") else str(low20_row.get("fetched_at") or "")[11:19]),
            "low20_close_price": None if not low20_row else _to_float(low20_row.get("close_price")),
            "low20_lowest_date": "" if not low20_row else _date_text(low20_row.get("lowest_date")),
            "low20_lowest_low": None if not low20_row else _to_float(low20_row.get("lowest_low")),
            "low20_bounce": low20_bounce,
            "high20_trade_date": "" if not high20_row else _date_text(high20_row.get("trade_date")),
            "high20_time": "" if not high20_row else (
                high20_row["fetched_at"].strftime("%H:%M:%S")
                if hasattr(high20_row.get("fetched_at"), "strftime") else str(high20_row.get("fetched_at") or "")[11:19]),
            "high20_close_price": None if not high20_row else _to_float(high20_row.get("close_price")),
            "high20_highest_date": "" if not high20_row else _date_text(high20_row.get("highest_date")),
            "high20_highest_high": None if not high20_row else _to_float(high20_row.get("highest_high")),
            "high20_decline": high20_decline,
            "price_date": "" if price_row is None else _date_text(price_row.get("price_date")),
            "price_time": "" if price_row is None else (
                price_row["fetched_at"].strftime("%m-%d %H:%M:%S")
                if hasattr(price_row.get("fetched_at"), "strftime") else str(price_row.get("fetched_at") or "")[5:19]),
            "current_price": current_price,
            "change_rate": change_rate,
            "market_cap": None if profile_by_code.get(code) is None else _to_float(profile_by_code[code].get("market_cap")),
            "dividend_year": dividend_year,
            "dividend_per_10": None if dividend_per_10 is None else round(dividend_per_10, 4),
            "dividend_per_share": None if dividend_per_share is None else round(dividend_per_share, 4),
            "dividend_yield": dividend_yield,
            "dividend_growth_years": dividend_growth_years,
            "dividend_amount_by_year": dividend_amount_by_year,
            "dividend_changed": dividend_changed,
            "details": details,
        }
        rows.append(row)
        # 盘中买卖点信号实时触发时写入通知文件（每股每天只写一次，去重在 signal_notify 内处理）
        # 写入前按 instock/config/signal_filter.txt 过滤（默认：扣非>-10%、股息率>3%、FCF/股息>50%）
        # 并发兜底路径跳过写入（刷新方正在执行，下一tick/轮询会补上）
        if refresh and phase == "intraday" and row["ma120_signal"] and signal_notify.passes_signal_filter(row):
            signal_notify.write_signal_notify(code, name, row["ma120_signal"], row, now)

    # 本次新屏蔽的股票不再安排任何缓存刷新
    if blocked_this_run_codes:
        for stale_list in (
            stale_ma120_codes, stale_low20_codes, stale_high20_codes,
            stale_industry_codes, stale_market_cap_codes,
            stale_finance_codes, stale_dividend_codes,
        ):
            stale_list[:] = [code for code in stale_list if code not in blocked_this_run_codes]

    if refresh:
        # 优先请求屏蔽相关数据（行业→收益→股息率/息增年），尽快完成屏蔽
        # 其余数据（市值、现金流、MA120、20日高低点）等屏蔽相关数据完成后才请求，屏蔽的股票不再请求
        priority_threads = [t for t in (
            _schedule_industry_refresh(stale_industry_codes),
            _schedule_finance_report_refresh(stale_finance_codes),
            _schedule_dividend_history_refresh(stale_dividend_codes),
        ) if t]

        def _schedule_tail_refreshes():
            _schedule_market_cap_refresh(stale_market_cap_codes)
            _schedule_cashflow_refresh(stale_cashflow_codes)
            # MA120/反弹/回落合并在一次K线请求中刷新
            stale_kline_codes = list(dict.fromkeys(
                stale_ma120_codes + stale_low20_codes + stale_high20_codes))
            _schedule_kline_refresh(stale_kline_codes)

        if priority_threads:
            def _run_priority_then_tail():
                for thread in priority_threads:
                    thread.join()
                _schedule_tail_refreshes()
            threading.Thread(target=_run_priority_then_tail, daemon=True).start()
        else:
            _schedule_tail_refreshes()
        # 高关注度（股息率≥4%）写入每日清空的高关注度文件（首行当天日期，跨天重写只保留当天）
        high_attention.update_high_attention(rows, now)
    rows.sort(key=lambda item: (item["dividend_yield"] is not None, item["dividend_yield"] or 0), reverse=True)
    result = {
        "rows": rows,
        "errors": errors,
        "total_stock_count": total_stock_count,
        "report_season": report_season,
        "fiscal_year_base": fiscal_year_base,
    }
    # 发布只读快照：调度刷新与只读装配都更新，页面轮询在两次调度之间直接复用
    _publish_readonly_snapshot(result, now)
    return result


def run_pipeline(db, now=None, refresh=None):
    """刷新流水线入口。

    refresh=None（默认）：单飞互斥，已在执行时并发调用方走只读快照——
    后台定时调度使用；
    refresh=True：强制执行全量刷新；
    refresh=False：纯只读（前端页面轮询专用），命中进程内快照直接返回，
    不请求外部接口、不递增轮询计数、不写信号/高关注度文件，与后台调度互不影响。"""
    global _PIPELINE_RUNNING
    if now is None:
        now = _now()
    if refresh is not None:
        if refresh:
            return _refresh_pipeline(db, now, refresh=True)
        return _get_readonly_result(db, now)
    with _PIPELINE_LOCK:
        if _PIPELINE_RUNNING:
            return _get_readonly_result(db, now)
        _PIPELINE_RUNNING = True
    try:
        return _refresh_pipeline(db, now, refresh=True)
    finally:
        with _PIPELINE_LOCK:
            _PIPELINE_RUNNING = False


class HighDividendPageHandler(webBase.BaseHandler):
    def get(self):
        _ensure_cache_tables(self.db)
        self.render("high_dividend.html")


class HighDividendDataHandler(webBase.BaseHandler):
    def get(self):
        self.set_header("Content-Type", "application/json;charset=UTF-8")
        now = _now()
        # 前端轮询为纯只读：只读缓存装配行数据，不触发抓取/写文件，数据刷新由后台调度完成
        result = run_pipeline(self.db, now, refresh=False)
        # 按前端过滤设置（查询参数）过滤行数据：后端只返回符合过滤条件的行，
        # 缩小轮询响应体积；过滤在只读快照之上执行，不重建快照
        rows = _filter_rows_for_frontend(result["rows"], self.get_argument)
        # report_season：当前财报披露季的目标报告期（label 供列名显示，period_date 供单股判断），非财报季为 None 隐藏该列
        payload = {
            "total_stock_count": result["total_stock_count"],
            "stock_count": len(rows),
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            # 前端页面轮询刷新间隔（毫秒），读取 instock/config/scheduler.conf 的 frontend_refresh_minutes
            "refresh_interval_ms": scheduler.get_frontend_refresh_interval_ms(),
            "report_season": result["report_season"],
            "fiscal_year_base": result["fiscal_year_base"],
            "fiscal_year_note": "财年基准年份存于数据库 settings 表，跨年自动重置；旧财年=基准-2，新财年=基准-1，检测到新财年年报后新财年收益不再为空",
            "cache_policy": {
                "price": "后台调度每次刷新高关注度（股息率≥4%），其余每6次调度刷新一次，盘后保持收盘价",
                "profile": "页面请求只读缓存；行业只抓一次不刷新，市值取每周最后一个交易日收盘数据、周五收盘后刷新，无缓存立即抓取",
                "ma120": "页面请求只读缓存；盘中可刷新（使用前一交易日收盘数据），下午3点后刷新当日收盘数据",
                "low20": "页面请求只读缓存；盘中可刷新（使用前一交易日收盘数据），下午3点后刷新当日收盘数据",
                "high20": "页面请求只读缓存；盘中可刷新（使用前一交易日收盘数据），下午3点后刷新当日收盘数据",
                "dividend_history": "页面请求只读缓存；交易日每天8点后检查一次，16点至23点最多每4小时复查一次",
                "finance_report": "页面请求只读缓存；交易日每天8点后检查一次，16点至23点最多每4小时复查一次",
                "cashflow": "页面请求只读缓存；窄口径FCF取最新季报（与扣非同报告期），金融行业不抓取；年报季交易日检查，非年报季最多7天一次",
            },
            "errors": result["errors"],
            "data": rows,
        }
        self.write(json.dumps(payload, ensure_ascii=False, default=_json_default))


class FollowListHandler(webBase.BaseHandler):
    def get(self):
        self.set_header("Content-Type", "application/json;charset=UTF-8")
        toggle_code = self.get_argument("toggle", "", True)
        if toggle_code:
            now_followed = followlist.toggle_follow(toggle_code)
            self.write(json.dumps({
                "code": toggle_code,
                "followed": now_followed,
            }, ensure_ascii=False))
            return

        codes = followlist.get_follow_codes()
        self.write(json.dumps({
            "follow_codes": codes,
        }, ensure_ascii=False))
