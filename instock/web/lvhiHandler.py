#!/usr/local/bin/python3
# -*- coding: utf-8 -*-
"""LVHI 模拟组合页面与接口。

路由（见 web_service.py）：
- /instock/lvhi            页面
- /instock/lvhi/api        总览 JSON（未建仓时自动触发建仓）
- /instock/lvhi/trade      调仓执行（GET query，action=build/buy/sell/set_capital）
"""

import json

import instock.core.stocklist as stocklist
import instock.core.lvhi_portfolio as lvhi_portfolio
import instock.web.base as webBase
import instock.web.highDividendHandler as highDividendHandler
import instock.web.scheduler as scheduler
from instock.core.common import _now, _json_default, _ensure_lvhi_tables

__author__ = 'sekia '
__date__ = '2026/8/15 '


def _lvhi_stock_pool(db, now):
    """买入下拉选股池：高股息列表过滤后的股票（与高股息页默认过滤一致：
    股息率≥4% 且扣非增速>-10%），代码→名称映射。失败时回退全量股票池。"""
    try:
        result = highDividendHandler.run_pipeline(db, now, refresh=False)
        args = {"min_dividend_yield": "4", "deducted_profit_filter": "1"}
        rows = highDividendHandler._filter_rows_for_frontend(
            result["rows"], lambda name, default="", strip=True: args.get(name, ""))
        pool = {row["code"]: row["name"] for row in rows}
        if pool:
            return pool
    except Exception as error:
        print(f"lvhi 股票池读取失败，回退全量池：{error}")
    return stocklist.get_stock_names()


class LvhiPageHandler(webBase.BaseHandler):
    def get(self):
        _ensure_lvhi_tables(self.db)
        self.render("lvhi.html", lvhi_active=True)


class LvhiOverviewHandler(webBase.BaseHandler):
    def get(self):
        self.set_header("Content-Type", "application/json;charset=UTF-8")
        now = _now()
        _ensure_lvhi_tables(self.db)

        # 未建仓时自动建仓（进程内单飞锁 + 事务内二次校验防重复），
        # 行情失败返回 pending，前端显示提示与手动重试
        settings = lvhi_portfolio.get_lvhi_settings(self.db)
        if not settings["build_status"]:
            build_result = lvhi_portfolio.build_portfolio(self.db, now)

        overview = lvhi_portfolio.get_portfolio_overview(self.db, now)
        errors = list(overview.get("errors") or [])
        if not settings["build_status"] and not build_result.get("ok"):
            errors.append(build_result.get("message", "建仓失败"))

        payload = {
            "status": overview["status"],
            "initial_capital": overview["initial_capital"],
            "build_date": overview["build_date"],
            "build_message": None if settings["build_status"] else build_result.get("message"),
            "overview": overview,
            "holdings": overview.get("holdings", []),
            "trades": lvhi_portfolio.list_trades(self.db),
            "nav": lvhi_portfolio.compute_nav_series(self.db, now),
            "compare_codes": lvhi_portfolio.get_compare_codes(),
            "compare_names": lvhi_portfolio.get_compare_names(),
            "pool": _lvhi_stock_pool(self.db, now),
            "errors": errors,
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "refresh_interval_ms": scheduler.get_frontend_refresh_interval_ms(),
        }
        self.write(json.dumps(payload, ensure_ascii=False, default=_json_default))


class LvhiQuoteHandler(webBase.BaseHandler):
    """现价查询：买入表单输入代码后预填价格用（轻量实时接口）。"""

    def get(self):
        self.set_header("Content-Type", "application/json;charset=UTF-8")
        now = _now()
        code = self.get_argument("code", "", True)
        if len(code) != 6 or not code.isdigit() or not stocklist.is_a_stock_code(code):
            self.write(json.dumps({"ok": False, "message": "股票代码无效"}, ensure_ascii=False))
            return
        prices = lvhi_portfolio._read_realtime_prices(self.db, [code], now)
        info = prices.get(code, {})
        price = info.get("new_price")
        if not price or price <= 0:
            self.write(json.dumps({"ok": False, "message": "行情获取失败"}, ensure_ascii=False))
            return
        name = info.get("name") or stocklist.get_stock_names().get(code, "")
        self.write(json.dumps({
            "ok": True,
            "code": code,
            "name": name,
            "price": price,
            "change_rate": info.get("change_rate"),
        }, ensure_ascii=False))


class LvhiTradeHandler(webBase.BaseHandler):
    def get(self):
        self.set_header("Content-Type", "application/json;charset=UTF-8")
        now = _now()
        _ensure_lvhi_tables(self.db)
        action = self.get_argument("action", "", True)

        if action == "build":
            result = lvhi_portfolio.build_portfolio(self.db, now)
        elif action == "buy":
            amount_mode = self.get_argument("mode", "", True) == "amount"
            result = lvhi_portfolio.execute_trade(
                self.db, "BUY",
                self.get_argument("code", "", True),
                self.get_argument("price", "", True),
                self._resolve_shares(),
                now,
                name=self.get_argument("name", "", True),
                note=self.get_argument("note", "", True),
                amount_mode=amount_mode)
        elif action == "sell":
            result = lvhi_portfolio.execute_trade(
                self.db, "SELL",
                self.get_argument("code", "", True),
                self.get_argument("price", "", True),
                self.get_argument("shares", "", True),
                now,
                note=self.get_argument("note", "", True))
        elif action == "set_capital":
            result = lvhi_portfolio.set_initial_capital(
                self.db, self.get_argument("capital", "", True), now)
        elif action == "compare_add":
            result = lvhi_portfolio.add_compare_code(self.db, self.get_argument("code", "", True))
        elif action == "compare_remove":
            result = lvhi_portfolio.remove_compare_code(self.db, self.get_argument("code", "", True))
        else:
            result = {"ok": False, "message": "未知操作", "overview": lvhi_portfolio.get_portfolio_overview(self.db, now)}

        payload = {
            "ok": result.get("ok", False),
            "message": result.get("message", ""),
            "overview": result.get("overview"),
            "compare_codes": result.get("compare_codes"),
            "compare_names": result.get("compare_names"),
        }
        self.write(json.dumps(payload, ensure_ascii=False, default=_json_default))

    def _resolve_shares(self):
        """买入数量：优先 shares 参数（整手），否则按 amount 金额自动换算整手。"""
        shares = self.get_argument("shares", "", True)
        if shares:
            return shares
        amount = self.get_argument("amount", "", True)
        if amount:
            return amount  # 金额模式由 lvhi_portfolio 换算为整手股数
        return ""
