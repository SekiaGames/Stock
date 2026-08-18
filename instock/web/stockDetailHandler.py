#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import json

import instock.core.stocklist as stocklist
import instock.web.base as webBase
from instock.core.common import (
    _to_float,
    _now,
    _date_text,
    _json_default,
    _get_or_sync_fiscal_year_base,
    _ensure_cache_tables,
)
from instock.core.market_quotes import (
    _read_price_cache,
    _read_ma120_cache,
    _write_ma120_cache,
    _read_liq_cache_batch,
    _write_liq_cache,
    _read_detail_kline_cache,
    _write_detail_kline_cache,
    _expected_kline_date,
)
from instock.core.profile import (
    _read_profile_cache,
    _write_market_cap_cache,
)
from instock.core.dividend import (
    _read_dividend_history_cache_batch,
    _build_cached_dividend_history,
    _sum_fiscal_year_dividend,
    _consecutive_non_decline_years,
)
from instock.core.financial import (
    _read_finance_report_cache_batch,
    _build_latest_finance_report,
    _read_cashflow_cache_batch,
    _build_cached_narrow_fcf,
)

__author__ = 'myh '
__date__ = '2026/8/18 '


def _report_quarter(report_date):
    """由报告期生成季度标签（03-31→Q1，06-30→Q2，09-30→Q3，12-31→Q4）；未知返回空串。"""
    if not report_date or len(report_date) < 7:
        return ""
    month = int(report_date[5:7])
    if month == 3:
        return "Q1"
    if month == 6:
        return "Q2"
    if month == 9:
        return "Q3"
    if month == 12:
        return "Q4"
    return ""


def _build_stock_detail(db, code, now):
    """装配单只股票分析页数据（个股K线图页面）。

    数据来源按需补齐（个股页打开时一次性计算/抓取并写缓存，之后定时调度维护）：
    - K线缓存缺失、缺最新交易日、或缺开盘价/成交量时同步抓取125根；
    - MA120、流动性指标缓存缺失时用K线缓存现算（不再请求外部接口）；
    - 市值缓存缺失时按需抓取一次（腾讯行情接口）。
    """
    if not stocklist.is_a_stock_code(code):
        return {"error": "无效的股票代码"}
    stock_names = stocklist.get_stock_names()
    name = stock_names.get(code, "")
    fiscal_year_base = _get_or_sync_fiscal_year_base(db)

    price_rows = _read_price_cache(db, [code])
    price_row = price_rows[0] if price_rows else None
    current_price = None if price_row is None else _to_float(price_row.get("current_price"))
    change_rate = None if price_row is None else _to_float(price_row.get("change_rate"))

    finance_cache_row, finance_history = _read_finance_report_cache_batch(db, [code]).get(code, (None, []))
    finance_report, _ = _build_latest_finance_report(
        finance_cache_row, finance_history, now, fiscal_year_base)
    dividend_cache_row, dividend_history = _read_dividend_history_cache_batch(db, [code]).get(code, (None, []))
    try:
        history, _, _ = _build_cached_dividend_history(dividend_cache_row, dividend_history, now)
    except Exception:
        history = []
    cashflow_cache_row, cashflow_history = _read_cashflow_cache_batch(db, [code]).get(code, (None, []))
    fcf_data, _ = _build_cached_narrow_fcf(finance_history, cashflow_cache_row, cashflow_history, now)
    total_share = fcf_data.get("total_share")

    # K线：独立详情缓存（最多250根，覆盖最近约1年）。缓存缺失、未含最新交易日
    # （盘前/盘中为最近交易日，盘后为当天）、或旧缓存缺开盘价/成交量时，
    # 按需抓取250根补齐（不足1年的次新股返回实际可用根数）
    kline_rows = _read_detail_kline_cache(db, code)
    if not kline_rows or kline_rows[-1][0] < _expected_kline_date(now) \
            or kline_rows[-1][1] is None or kline_rows[-1][5] is None:
        try:
            rows = stocklist.fetch_daily_kline_rows(code, count=250)
            if rows:
                _write_detail_kline_cache(db, code, rows)
                kline_rows = rows
        except Exception:
            pass

    # MA120：缓存缺失时用K线缓存现算（与 _refresh_kline_metrics 同口径），并写缓存
    ma120_row = _read_ma120_cache(db, [code]).get(code)
    if not ma120_row:
        metrics = stocklist.compute_kline_metrics(kline_rows, current_price)
        if metrics.get("ma120") is not None:
            ma120_row = metrics["ma120"]
            try:
                _write_ma120_cache(db, code, ma120_row, now)
            except Exception:
                pass

    # 流动性指标：始终用250根K线现算（连续积分式，无需总股本即可计算），
    # 保证与逐日序列末点一致；缓存缺失或数据日期不一致时同步写缓存
    liq_metrics = None
    try:
        liq_metrics = stocklist.compute_liq_oversold(kline_rows, total_share)
    except Exception:
        pass
    liq_row = _read_liq_cache_batch(db, [code]).get(code)
    if liq_metrics is not None and (
            not liq_row or _date_text(liq_row.get("trade_date")) != liq_metrics.get("trade_date")):
        try:
            _write_liq_cache(db, code, liq_metrics, now)
            liq_row = liq_metrics
        except Exception:
            pass
    if liq_metrics is not None:
        liq_row = liq_metrics

    # 市值：缓存缺失时按需抓取一次（腾讯行情，流通市值单位亿）
    profile_row = _read_profile_cache(db, [code]).get(code)
    if (profile_row is None or profile_row.get("market_cap") is None) and current_price:
        try:
            market_cap_data = stocklist.fetch_market_cap_data([code])
            market_cap = market_cap_data.get(code)
            if market_cap is not None:
                _write_market_cap_cache(db, code, market_cap, now)
                profile_row = _read_profile_cache(db, [code]).get(code)
        except Exception:
            pass

    # 股息率/息增年：与高股息列表同口径（最新已完结财年）
    if finance_report.get("finance_fetched"):
        latest_fiscal_year = fiscal_year_base - 1 if finance_report.get("new_year_eps_report_date") else fiscal_year_base - 2
    else:
        latest_fiscal_year = None
    dividend_per_10 = None
    dividend_per_share = None
    dividend_yield = None
    dividend_growth_years = None
    if latest_fiscal_year is not None:
        dividend_per_10, _ = _sum_fiscal_year_dividend(history, latest_fiscal_year)
        dividend_growth_years = _consecutive_non_decline_years(history, latest_fiscal_year)
        dividend_per_share = dividend_per_10 / 10
        if dividend_per_share > 0 and current_price and current_price > 0:
            dividend_yield = dividend_per_share / current_price * 100
        elif dividend_per_share <= 0:
            dividend_yield = 0.0

    narrow_fcf = fcf_data.get("narrow_fcf")
    fcf_dividend = None
    if narrow_fcf is not None:
        if dividend_per_share and dividend_per_share > 0:
            fcf_dividend = narrow_fcf / dividend_per_share
    elif fcf_data.get("narrow_fcf_skipped"):
        eps = finance_report.get("diluted_eps")
        if eps is not None and eps > 0 and dividend_per_share and dividend_per_share > 0:
            fcf_dividend = eps / dividend_per_share

    kline = [{
        "date": row[0],
        "open": row[1],
        "close": row[2],
        "high": row[3],
        "low": row[4],
        "volume": row[5],
    } for row in kline_rows]

    # 每日流动性序列（K线辅助指标）：积分值（EMA平滑）与当日值（积分前原始信号），与顶部流动性积分同口径
    liq_series = stocklist.compute_liq_series(kline_rows, total_share)
    liq_daily_series = stocklist.compute_liq_daily_series(kline_rows)

    return {
        "code": code,
        "name": name,
        "current_price": current_price,
        "change_rate": change_rate,
        "price_date": "" if price_row is None else _date_text(price_row.get("price_date")),
        "price_time": "" if price_row is None else (
            price_row["fetched_at"].strftime("%m-%d %H:%M:%S")
            if hasattr(price_row.get("fetched_at"), "strftime") else str(price_row.get("fetched_at") or "")[5:19]),
        "dividend_year": latest_fiscal_year,
        "dividend_per_share": None if dividend_per_share is None else round(dividend_per_share, 4),
        "dividend_yield": dividend_yield,
        "dividend_growth_years": dividend_growth_years,
        "deducted_profit_growth": finance_report.get("deducted_profit_growth"),
        "report_name": finance_report.get("report_name", ""),
        "report_date": finance_report.get("report_date", ""),
        "report_quarter": _report_quarter(finance_report.get("report_date")),
        "fcf_dividend": fcf_dividend,
        "narrow_fcf": narrow_fcf,
        "narrow_fcf_report_name": fcf_data.get("narrow_fcf_report_name", ""),
        "total_share": total_share,
        "market_cap": None if profile_row is None else _to_float(profile_row.get("market_cap")),
        "industry_name": "" if profile_row is None else (profile_row.get("industry_name") or ""),
        "ma120": None if not ma120_row else _to_float(ma120_row.get("ma120")),
        "ma120_position": None if not ma120_row else _to_float(ma120_row.get("ma120_position")),
        "ma120_trade_date": "" if not ma120_row else _date_text(ma120_row.get("trade_date")),
        "liq_score": None if not liq_row else _to_float(liq_row.get("liq_score")),
        "liq_daily": None if not liq_row else _to_float(liq_row.get("liq_daily")),
        "liq_price_pos": None if not liq_row else _to_float(liq_row.get("price_pos")),
        "liq_turnover_pct": None if not liq_row else _to_float(liq_row.get("turnover_pct")),
        "liq_pressure_pct": None if not liq_row else _to_float(liq_row.get("pressure_pct")),
        "liq_vol20": None if not liq_row else _to_float(liq_row.get("vol20")),
        "liq_turnover": None if not liq_row else _to_float(liq_row.get("turnover")),
        "liq_trade_date": "" if not liq_row else _date_text(liq_row.get("trade_date")),
        "liq_series": liq_series,
        "liq_daily_series": liq_daily_series,
        "kline": kline,
    }


class StockDetailPageHandler(webBase.BaseHandler):
    def get(self):
        # 页面模板迭代频繁，禁止浏览器缓存，避免加载到旧版模板
        self.set_header("Cache-Control", "no-store")
        _ensure_cache_tables(self.db)
        code = self.get_argument("code", "", True)
        if not stocklist.is_a_stock_code(code):
            self.redirect("/instock/high_dividend")
            return
        self.render("stock_detail.html", code=code, lvhi_active=False)


class StockDetailDataHandler(webBase.BaseHandler):
    def get(self):
        self.set_header("Content-Type", "application/json;charset=UTF-8")
        self.set_header("Cache-Control", "no-store")
        code = self.get_argument("code", "", True)
        result = _build_stock_detail(self.db, code, _now())
        self.write(json.dumps(result, ensure_ascii=False, default=_json_default))
