#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LVHI 模拟组合：账本式持仓管理与净值回溯。

数据模型：
- 调仓账本 cn_high_dividend_lvhi_trades 为唯一事实来源（只增不改），
  当前持仓与现金由账本折叠推导，单条 INSERT 天然原子；
- 组合净值由 账本 + 扩展K线缓存（cn_high_dividend_lvhi_kline_cache，640根/股）
  按需回溯计算，不存快照，永远与账本一致；
- 状态（初始资金/是否建仓/建仓日期）存 settings 表（cn_high_dividend_settings）。
"""

import datetime
import os
import threading

import instock.lib.database as mdb
import instock.lib.mysql as mysql
import instock.core.stocklist as stocklist
from instock.core import market_quotes
from instock.core.common import (
    _now,
    _to_float,
    _ensure_lvhi_tables,
    _SETTINGS_TABLE,
    _LVHI_TRADES_TABLE,
    _LVHI_KLINE_CACHE_TABLE,
    _LVHI_INITIAL_CAPITAL_KEY,
    _LVHI_BUILD_STATUS_KEY,
    _LVHI_BUILD_DATE_KEY,
    _LVHI_KLINE_COUNT_KEY,
    _LVHI_DEFAULT_CAPITAL,
    _LVHI_DEFAULT_KLINE_COUNT,
    _LVHI_BUILD_STOCK_CODE,
)

__author__ = 'sekia '
__date__ = '2026/8/15 '

_LOT_SIZE = 100  # A股一手
_LVHI_BENCHMARK_CODE = "563020"  # 默认对比目标：红利低波ETF易方达
_LVHI_COMPARE_FILE_NAME = "lvhi_compare_code.txt"  # 对比目标配置（每行一个代码，可手动添加多个）
_LVHI_HISTORY_FILE_NAME = "lvhi_trade_history.txt"  # 交易记录镜像（容器重部署后据此恢复组合）
_COMPARE_FILE_LOCK = threading.Lock()
_HISTORY_FILE_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()
_KLINE_REFRESH_LOCK = threading.Lock()
_KLINE_REFRESH_RUNNING = False

_LVHI_COMPARE_TEMPLATE = """# 股息率组合对比目标（每行一个，格式 代码|显示名，显示名可省略，# 开头为注释，可手动添加多个）
# 默认：红利低波ETF易方达
563020|红利低波ETF
"""

_LVHI_HISTORY_TEMPLATE = """# 股息率组合交易记录（组合账本镜像，容器重部署后据此恢复组合）
# 行格式（| 分隔，行首 # 为注释）：
# 元数据行：初始资金|建仓日期
# 交易行：日期|时分秒|方向(买入/卖出)|代码|名称|价格|股数|金额|现金余额|备注
1000000|
"""


def _compare_file_path():
    """对比目标配置文件路径：instock/config/lvhi_compare_code.txt。"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "config", _LVHI_COMPARE_FILE_NAME)


def ensure_lvhi_compare_file():
    """对比目标配置文件不存在时创建默认模板（默认红利低波ETF）。"""
    path = _compare_file_path()
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_LVHI_COMPARE_TEMPLATE)
        except OSError as error:
            print(f"lvhi 对比目标配置文件创建失败：{error}")


def get_compare_items():
    """读取对比目标 [(code, name), ...]（去注释/去空/去重，保序）。

    行格式：代码|显示名（显示名可省略），如 563020|红利低波ETF。
    """
    result = []
    try:
        with open(_compare_file_path(), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                line = line.split("#")[0].strip()
                parts = line.split("|")
                code = parts[0].strip()
                if len(code) == 6 and code.isdigit() and not any(c == code for c, _ in result):
                    name = parts[1].strip() if len(parts) > 1 else ""
                    result.append((code, name))
    except OSError:
        pass
    return result


def get_compare_codes():
    """读取对比目标代码列表（保序）。"""
    return [code for code, _ in get_compare_items()]


def get_compare_names():
    """读取对比目标显示名 {code: name}，未配置显示名的回退到股票名称表。"""
    names = stocklist.get_stock_names()
    result = {}
    for code, name in get_compare_items():
        result[code] = name or names.get(code, "") or code
    return result


def _write_compare_items(items):
    """原子重写对比目标配置文件（进程内加锁防并发写坏），行格式 代码|显示名。"""
    with _COMPARE_FILE_LOCK:
        path = _compare_file_path()
        tmp_path = path + ".tmp"
        lines = [
            "# 股息率组合对比目标（每行一个，格式 代码|显示名，显示名可省略，# 开头为注释，可手动添加多个）",
            "# 默认：红利低波ETF易方达",
        ]
        for code, name in items:
            lines.append(f"{code}|{name}" if name else code)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp_path, path)


def _compare_result(ok, message, items):
    return {
        "ok": ok,
        "message": message,
        "compare_codes": [code for code, _ in items],
        "compare_names": {code: (name or stocklist.get_stock_names().get(code, "") or code) for code, name in items},
    }


def add_compare_code(db, text):
    """添加对比目标。text 格式：代码 或 代码|显示名。返回 {ok, message, compare_codes, compare_names}。"""
    text = str(text or "").strip()
    parts = text.split("|")
    code = parts[0].strip()
    name = parts[1].strip() if len(parts) > 1 else ""
    if len(code) != 6 or not code.isdigit() or not stocklist.is_a_stock_code(code):
        return _compare_result(False, "股票代码无效", get_compare_items())
    items = get_compare_items()
    if any(c == code for c, _ in items):
        return _compare_result(False, f"{code} 已在对比目标中", items)
    if not name:
        name = stocklist.get_stock_names().get(code, "")
    items.append((code, name))
    _write_compare_items(items)
    return _compare_result(True, f"已添加对比目标 {code}", items)


def remove_compare_code(db, code):
    """移除对比目标。返回 {ok, message, compare_codes, compare_names}。"""
    code = str(code or "").strip()
    items = get_compare_items()
    if not any(c == code for c, _ in items):
        return _compare_result(False, f"{code} 不在对比目标中", items)
    items = [(c, n) for c, n in items if c != code]
    _write_compare_items(items)
    return _compare_result(True, f"已移除对比目标 {code}", items)


def _history_file_path():
    """交易记录文件路径：instock/config/lvhi_trade_history.txt。"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "config", _LVHI_HISTORY_FILE_NAME)


def ensure_lvhi_history_file():
    """配置文件不存在时创建默认模板（模式与 scheduler.ensure_scheduler_config_file 一致）。"""
    path = _history_file_path()
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_LVHI_HISTORY_TEMPLATE)
        except OSError as error:
            print(f"lvhi 交易记录文件创建失败：{error}")


def sync_lvhi_history_file(db):
    """把组合账本+设置全量镜像写入交易记录文件（每次调仓成功后调用）。

    文件与数据库账本保持一致：元数据行（初始资金|建仓日期）+ 逐笔交易行；
    进程内加锁防并发写坏。
    """
    try:
        settings = get_lvhi_settings(db)
        rows = _read_all_trades(db)
        with _HISTORY_FILE_LOCK:
            lines = [
                "# 股息率组合交易记录（组合账本镜像，容器重部署后据此恢复组合）",
                "# 行格式（| 分隔，行首 # 为注释）：",
                "# 元数据行：初始资金|建仓日期",
                "# 交易行：日期|时分秒|方向(买入/卖出)|代码|名称|价格|股数|金额|现金余额|备注",
                f"{int(settings['initial_capital'])}|{settings['build_date'] or ''}",
            ]
            for row in rows:
                direction_text = "买入" if row.get("direction") == "BUY" else "卖出"
                trade_time = str(row.get("trade_time") or "")
                time_part = trade_time[11:19] if len(trade_time) > 11 else trade_time  # 只保留时分秒
                lines.append("|".join([
                    str(row.get("trade_date"))[:10],
                    time_part,
                    direction_text,
                    str(row.get("code") or ""),
                    str(row.get("name") or ""),
                    str(row.get("price") or ""),
                    str(row.get("shares") or ""),
                    str(row.get("amount") or ""),
                    str(row.get("cash_after") or ""),
                    str(row.get("note") or ""),
                ]))
            path = _history_file_path()
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            os.replace(tmp_path, path)  # 原子替换，避免写一半留下损坏文件
    except Exception as error:
        print(f"lvhi 交易记录文件写入失败：{error}")


def restore_lvhi_from_history(db, now=None):
    """从交易记录文件恢复组合（容器重部署/数据库为空时调用）。

    仅当账本为空且文件含有效元数据/交易行时执行；恢复成功后重写文件归一化。
    返回 {ok, message}。
    """
    now = now or _now()
    # 新部署数据库可能还没有 lvhi 表，先建表再查账本，否则启动即崩（1146 table doesn't exist）
    _ensure_lvhi_tables(db)
    if _has_any_trade(db):
        return {"ok": False, "message": "账本非空，跳过恢复"}
    path = _history_file_path()
    if not os.path.exists(path):
        return {"ok": False, "message": "交易记录文件不存在"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except OSError as error:
        return {"ok": False, "message": f"交易记录文件读取失败：{error}"}

    capital = _LVHI_DEFAULT_CAPITAL
    build_date = None
    trades = []
    for line in lines:
        fields = line.split("|")
        if fields[0].isdigit() and (len(fields) == 1 or (len(fields) == 2 and not fields[1])):
            # 元数据行：初始资金（兼容 1000000 与 1000000| 两种写法，建仓日期可省略）
            capital = int(fields[0])
            continue
        if len(fields) >= 9 and fields[0][:1].isdigit():
            try:
                trades.append({
                    "trade_date": fields[0],
                    # 时间列只存时分秒，拼日期还原完整时间（兼容旧文件完整时间格式）
                    "trade_time": fields[1] if fields[1] and " " in fields[1] else (fields[0] + " " + (fields[1] or "00:00:00")),
                    "direction": "BUY" if fields[2] in ("BUY", "买入") else "SELL",
                    "code": fields[3],
                    "name": fields[4],
                    "price": _to_float(fields[5]),
                    "shares": int(fields[6]),
                    "amount": _to_float(fields[7]),
                    "cash_after": _to_float(fields[8]),
                    "note": fields[9] if len(fields) > 9 else "",
                })
                if not build_date and fields[0] >= "2020-01-01":
                    build_date = fields[0]
            except (ValueError, TypeError) as error:
                print(f"lvhi 交易记录行解析跳过：{line}（{error}）")

    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    statements = []
    for t in trades:
        statements.append((
            f"""
            INSERT INTO `{_LVHI_TRADES_TABLE}`
                (`trade_date`, `trade_time`, `direction`, `code`, `name`,
                 `price`, `shares`, `amount`, `cash_after`, `note`, `created_at`)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (t["trade_date"], t["trade_time"], t["direction"], t["code"], t["name"],
             t["price"], t["shares"], t["amount"], t["cash_after"], t["note"], timestamp),
        ))
    for key, value in [
        (_LVHI_BUILD_STATUS_KEY, "1"),
        (_LVHI_BUILD_DATE_KEY, build_date or ""),
        (_LVHI_INITIAL_CAPITAL_KEY, str(capital)),
    ]:
        statements.append((
            f"""
            INSERT INTO `{_SETTINGS_TABLE}` (`setting_key`, `setting_value`, `updated_at`)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE `setting_value` = VALUES(`setting_value`), `updated_at` = VALUES(`updated_at`)
            """,
            (key, value, timestamp),
        ))
    try:
        _run_in_transaction(db, statements)
    except Exception as error:
        return {"ok": False, "message": f"恢复写库失败：{error}"}
    sync_lvhi_history_file(db)
    return {"ok": True, "message": f"已从交易记录文件恢复组合（{len(trades)}笔交易）"}


def _set_setting(db, key, value, now):
    """写入 settings 表（key 不存在则插入），与 _bump_price_poll_count 同模式。"""
    db.execute(f"""
        INSERT INTO `{_SETTINGS_TABLE}` (`setting_key`, `setting_value`, `updated_at`)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `setting_value` = VALUES(`setting_value`),
            `updated_at` = VALUES(`updated_at`)
    """, key, str(value), now.strftime("%Y-%m-%d %H:%M:%S"))


def get_lvhi_settings(db):
    """读取 LVHI 组合设置，缺失时返回默认值（不写库）。

    返回 {initial_capital: float, build_status: bool, build_date: str|None, kline_count: int}。
    """
    result = {
        "initial_capital": _LVHI_DEFAULT_CAPITAL,
        "build_status": False,
        "build_date": None,
        "kline_count": _LVHI_DEFAULT_KLINE_COUNT,
    }
    rows = db.query(f"""
        SELECT `setting_key`, `setting_value` FROM `{_SETTINGS_TABLE}`
        WHERE `setting_key` IN (%s, %s, %s, %s)
    """, _LVHI_INITIAL_CAPITAL_KEY, _LVHI_BUILD_STATUS_KEY, _LVHI_BUILD_DATE_KEY, _LVHI_KLINE_COUNT_KEY)
    for row in rows:
        key = row.get("setting_key")
        value = row.get("setting_value")
        if key == _LVHI_INITIAL_CAPITAL_KEY:
            capital = _to_float(value)
            if capital and capital > 0:
                result["initial_capital"] = capital
        elif key == _LVHI_BUILD_STATUS_KEY:
            result["build_status"] = value == "1"
        elif key == _LVHI_BUILD_DATE_KEY:
            result["build_date"] = str(value)[:10] if value else None
        elif key == _LVHI_KLINE_COUNT_KEY:
            count = _to_float(value)
            if count and 125 <= count <= 640:
                result["kline_count"] = int(count)
    return result


def _read_all_trades(db):
    """读取全部账本（id 升序，即成交时间序）。"""
    return db.query(f"""
        SELECT `id`, `trade_date`, `trade_time`, `direction`, `code`, `name`,
               `price`, `shares`, `amount`, `cash_after`, `note`
        FROM `{_LVHI_TRADES_TABLE}`
        ORDER BY `id` ASC
    """)


def _has_any_trade(db):
    row = db.get(f"SELECT COUNT(*) AS c FROM `{_LVHI_TRADES_TABLE}`")
    return bool(row and row.get("c"))


def _fold_holdings(trades_rows):
    """折叠账本 → 当前持仓 [{code, name, shares, cost_total, avg_cost}]（纯函数）。

    买入：shares+=、cost_total+=amount；
    卖出：shares-=、cost_total-=avg_cost*shares（摊薄成本法）。
    返回时剔除已清仓（shares<=0）的股票。
    """
    holdings = {}
    for row in trades_rows:
        code = row.get("code")
        direction = row.get("direction")
        shares = int(row.get("shares") or 0)
        amount = float(row.get("amount") or 0)
        h = holdings.setdefault(code, {
            "code": code,
            "name": row.get("name") or "",
            "shares": 0,
            "cost_total": 0.0,
            "avg_cost": 0.0,
        })
        if direction == "BUY":
            h["shares"] += shares
            h["cost_total"] += amount
        else:  # SELL：摊薄成本法，卖出冲减成本
            avg = h["cost_total"] / h["shares"] if h["shares"] > 0 else 0.0
            h["shares"] -= shares
            h["cost_total"] -= avg * shares
            if h["shares"] <= 0:
                h["shares"] = 0
                h["cost_total"] = 0.0
        h["avg_cost"] = h["cost_total"] / h["shares"] if h["shares"] > 0 else 0.0
    return [h for h in holdings.values() if h["shares"] > 0]


def _current_cash(initial_capital, trades_rows):
    """当前现金 = 账本最后一笔 cash_after（无则初始资金）。"""
    if not trades_rows:
        return float(initial_capital)
    return float(trades_rows[-1].get("cash_after") or initial_capital)


def _read_realtime_prices(db, codes, now):
    """实时现价（优先）+ 价格缓存兜底，返回 {code: {new_price, pre_close_price, change_rate, name}}。

    实时抓取成功即写回现有价格缓存表，失败回落缓存，均失败该码返回空。
    """
    result = {}
    codes = [code for code in codes if code]
    if not codes:
        return result
    try:
        rows = stocklist.make_selected_stock_rows(now.date(), codes=codes)
        if rows:
            market_quotes._write_price_cache(db, rows, now)
            for r in rows:
                result[r["code"]] = {
                    "new_price": _to_float(r.get("new_price")),
                    "pre_close_price": _to_float(r.get("pre_close_price")),
                    "change_rate": _to_float(r.get("change_rate")),
                    "name": r.get("name") or "",
                }
    except Exception:
        pass
    if not result:
        cached = market_quotes._read_price_cache(db, codes)
        for row in cached:
            result[row["code"]] = {
                "new_price": _to_float(row.get("current_price")),
                "pre_close_price": _to_float(row.get("pre_close_price")),
                "change_rate": _to_float(row.get("change_rate")),
                "name": row.get("name") or "",
            }
    return result


def _run_in_transaction(db, statements):
    """显式事务执行多条 SQL（会话 autocommit=True，需显式开启事务）。

    statements 为 [(sql, params_tuple), ...]；任一失败 ROLLBACK 后 re-raise。
    """
    db.execute("START TRANSACTION")
    try:
        for sql, params in statements:
            db.execute(sql, *params)
        db.execute("COMMIT")
    except Exception:
        try:
            db.execute("ROLLBACK")
        except Exception:
            pass
        raise


def build_portfolio(db, now=None):
    """首次自动建仓：按初始资金 100% 以现价买入长江电力（整手）。幂等，可重试。

    进程锁 + 事务内校验（build_status 与账本为空）双重防重复建仓；
    行情获取失败时整次不建仓，返回提示稍后重试。
    """
    now = now or _now()
    with _BUILD_LOCK:
        settings = get_lvhi_settings(db)
        overview = get_portfolio_overview(db, now)
        if settings["build_status"]:
            return {"ok": False, "message": "已建仓", "overview": overview}
        if _has_any_trade(db):
            return {"ok": False, "message": "账本非空，无法建仓", "overview": overview}

        prices = _read_realtime_prices(db, [_LVHI_BUILD_STOCK_CODE], now)
        info = prices.get(_LVHI_BUILD_STOCK_CODE, {})
        price = _to_float(info.get("new_price"))
        if not price or price <= 0:
            return {"ok": False, "message": "行情获取失败，暂无法建仓，稍后自动重试", "overview": overview}

        capital = settings["initial_capital"]
        price = round(price, 2)
        shares = int(capital // (price * _LOT_SIZE)) * _LOT_SIZE
        if shares < _LOT_SIZE:
            return {"ok": False, "message": f"资金不足以买入1手{_LVHI_BUILD_STOCK_CODE}", "overview": overview}
        amount = round(shares * price, 2)
        cash_after = round(capital - amount, 2)
        name = info.get("name") or stocklist.get_stock_names().get(_LVHI_BUILD_STOCK_CODE, "")
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        _run_in_transaction(db, [
            (f"""
                INSERT INTO `{_LVHI_TRADES_TABLE}`
                    (`trade_date`, `trade_time`, `direction`, `code`, `name`,
                     `price`, `shares`, `amount`, `cash_after`, `note`, `created_at`)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (now.date(), timestamp, "BUY", _LVHI_BUILD_STOCK_CODE, name,
                  price, shares, amount, cash_after, "首次自动建仓", timestamp)),
            (f"""
                INSERT INTO `{_SETTINGS_TABLE}` (`setting_key`, `setting_value`, `updated_at`)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE `setting_value` = VALUES(`setting_value`), `updated_at` = VALUES(`updated_at`)
            """, (_LVHI_BUILD_STATUS_KEY, "1", timestamp)),
            (f"""
                INSERT INTO `{_SETTINGS_TABLE}` (`setting_key`, `setting_value`, `updated_at`)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE `setting_value` = VALUES(`setting_value`), `updated_at` = VALUES(`updated_at`)
            """, (_LVHI_BUILD_DATE_KEY, now.date().isoformat(), timestamp)),
            (f"""
                INSERT INTO `{_SETTINGS_TABLE}` (`setting_key`, `setting_value`, `updated_at`)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE `setting_value` = VALUES(`setting_value`), `updated_at` = VALUES(`updated_at`)
            """, (_LVHI_INITIAL_CAPITAL_KEY, str(int(capital)), timestamp)),
        ])
        sync_lvhi_history_file(db)  # 建仓成功后镜像交易记录文件
        return {"ok": True, "message": f"建仓成功：买入{name} {shares}股 @ {price:.2f}",
                "overview": get_portfolio_overview(db, now)}


def execute_trade(db, direction, code, price, shares, now=None, name=None, note="", amount_mode=False):
    """手动调仓（纯手动，无策略目标）。成交价以实时行情为准，忽略调用方传入的 price。

    买入校验资金，卖出校验持仓；单条 INSERT 原子写账本并返回最新总览。
    amount_mode=True 时 shares 参数表示买入金额（元），按实时价换算整手股数。
    """
    now = now or _now()
    code = str(code or "").strip()
    if len(code) != 6 or not code.isdigit() or not stocklist.is_a_stock_code(code):
        return {"ok": False, "message": "股票代码无效", "overview": get_portfolio_overview(db, now)}
    prices = _read_realtime_prices(db, [code], now)
    price = _to_float(prices.get(code, {}).get("new_price"))
    if not price or price <= 0:
        return {"ok": False, "message": "行情获取失败，无法按实时价成交", "overview": get_portfolio_overview(db, now)}
    price = round(price, 2)
    try:
        shares_value = float(shares)
    except (TypeError, ValueError):
        return {"ok": False, "message": "数量无效", "overview": get_portfolio_overview(db, now)}
    if amount_mode:
        # 金额模式（仅买入，单位元）：按输入价格换算整手股数
        if direction != "BUY":
            return {"ok": False, "message": "金额模式仅支持买入", "overview": get_portfolio_overview(db, now)}
        shares = int(shares_value // (price * _LOT_SIZE)) * _LOT_SIZE
        if shares < _LOT_SIZE:
            return {"ok": False, "message": f"金额不足以买入1手（价格 {price:.2f}）",
                    "overview": get_portfolio_overview(db, now)}
    elif shares_value == int(shares_value):
        # 股数模式：须为整手（买卖通用）
        shares = int(shares_value)
        if shares < _LOT_SIZE or shares % _LOT_SIZE != 0:
            return {"ok": False, "message": f"数量须为{_LOT_SIZE}股的整数倍且不少于1手",
                    "overview": get_portfolio_overview(db, now)}
    else:
        return {"ok": False, "message": "数量无效（股数须为整手）", "overview": get_portfolio_overview(db, now)}
    amount = round(shares * price, 2)

    settings = get_lvhi_settings(db)
    if not settings["build_status"]:
        return {"ok": False, "message": "尚未建仓", "overview": get_portfolio_overview(db, now)}
    trades_rows = _read_all_trades(db)
    holdings = {h["code"]: h for h in _fold_holdings(trades_rows)}
    cash = _current_cash(settings["initial_capital"], trades_rows)

    if direction == "BUY":
        if amount > cash + 1e-6:
            return {"ok": False, "message": f"资金不足（当前现金 {cash:.2f}，需 {amount:.2f}）",
                    "overview": get_portfolio_overview(db, now)}
    elif direction == "SELL":
        holding = holdings.get(code)
        if not holding or holding["shares"] <= 0:
            return {"ok": False, "message": "该股票不在持仓中", "overview": get_portfolio_overview(db, now)}
        if shares > holding["shares"]:
            return {"ok": False, "message": f"卖出数量超过持仓（持仓 {holding['shares']} 股）",
                    "overview": get_portfolio_overview(db, now)}
    else:
        return {"ok": False, "message": "方向无效", "overview": get_portfolio_overview(db, now)}

    if not name:
        name = stocklist.get_stock_names().get(code) or prices.get(code, {}).get("name") or ""
    cash_after = round(cash - amount, 2) if direction == "BUY" else round(cash + amount, 2)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    db.execute(f"""
        INSERT INTO `{_LVHI_TRADES_TABLE}`
            (`trade_date`, `trade_time`, `direction`, `code`, `name`,
             `price`, `shares`, `amount`, `cash_after`, `note`, `created_at`)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, now.date(), timestamp, direction, code, name, price, shares, amount,
        cash_after, note or "", timestamp)
    sync_lvhi_history_file(db)  # 调仓成功后镜像交易记录文件
    action_text = "买入" if direction == "BUY" else "卖出"
    return {"ok": True, "message": f"{action_text}{name or code} {shares}股 @ {price:.2f}",
            "overview": get_portfolio_overview(db, now)}


def set_initial_capital(db, capital, now=None):
    """修改初始资金，仅未建仓时可改；已建仓返回错误。"""
    now = now or _now()
    settings = get_lvhi_settings(db)
    if settings["build_status"]:
        return {"ok": False, "message": "已建仓，初始资金不可修改", "overview": get_portfolio_overview(db, now)}
    capital = _to_float(capital)
    if not capital or capital <= 0:
        return {"ok": False, "message": "初始资金无效", "overview": get_portfolio_overview(db, now)}
    _set_setting(db, _LVHI_INITIAL_CAPITAL_KEY, str(int(capital)), now)
    sync_lvhi_history_file(db)  # 初始资金变更后同步元数据行
    return {"ok": True, "message": f"初始资金已设置为 {int(capital):,}".replace(",", ""),
            "overview": get_portfolio_overview(db, now)}


def get_portfolio_overview(db, now=None):
    """组合总览：总资产/现金/持仓市值/累计收益率/持仓盈亏/仓位构成。

    未建仓返回 {status: "pending"}；已建仓折叠账本 + 实时现价装配持仓明细。
    """
    now = now or _now()
    settings = get_lvhi_settings(db)
    if not settings["build_status"]:
        return {
            "status": "pending",
            "initial_capital": settings["initial_capital"],
            "build_date": None,
            "cash": settings["initial_capital"],
            "total_assets": settings["initial_capital"],
            "total_profit": 0.0,
            "cum_return": 0.0,
            "holdings": [],
            "errors": [],
        }

    trades_rows = _read_all_trades(db)
    holdings = _fold_holdings(trades_rows)
    cash = _current_cash(settings["initial_capital"], trades_rows)
    codes = [h["code"] for h in holdings]
    prices = _read_realtime_prices(db, codes, now)
    errors = []

    enriched = []
    for h in holdings:
        info = prices.get(h["code"], {})
        price = _to_float(info.get("new_price"))
        if not price or price <= 0:
            price = h["avg_cost"]  # 行情失败兜底用成本价，并提示
            errors.append(f"{h['code']} 行情获取失败，暂用成本价估值")
        market_value = round(h["shares"] * price, 2)
        profit = round(market_value - h["cost_total"], 2)
        enriched.append({
            "code": h["code"],
            "name": h["name"],
            "shares": h["shares"],
            "avg_cost": round(h["avg_cost"], 4),
            "cost_total": round(h["cost_total"], 2),
            "current_price": round(price, 4),
            "change_rate": info.get("change_rate"),
            "market_value": market_value,
            "profit": profit,
            "profit_rate": round(profit / h["cost_total"] * 100, 2) if h["cost_total"] else 0.0,
        })

    total_market_value = round(sum(h["market_value"] for h in enriched), 2)
    total_assets = round(cash + total_market_value, 2)
    total_profit = round(total_assets - settings["initial_capital"], 2)
    cum_return = round(total_profit / settings["initial_capital"] * 100, 2) if settings["initial_capital"] else 0.0
    for h in enriched:
        h["weight"] = round(h["market_value"] / total_assets * 100, 2) if total_assets else 0.0

    return {
        "status": "ok",
        "initial_capital": settings["initial_capital"],
        "build_date": settings["build_date"],
        "cash": round(cash, 2),
        "total_assets": total_assets,
        "total_market_value": total_market_value,
        "total_profit": total_profit,
        "cum_return": cum_return,
        "holdings": enriched,
        "errors": errors,
    }


def list_trades(db, limit=200):
    """调仓记录（id 倒序，最新在前）。"""
    return db.query(f"""
        SELECT `id`, `trade_date`, `trade_time`, `direction`, `code`, `name`,
               `price`, `shares`, `amount`, `cash_after`, `note`
        FROM `{_LVHI_TRADES_TABLE}`
        ORDER BY `id` DESC
        LIMIT %s
    """, int(limit))


def _write_lvhi_kline_cache(db, code, rows):
    """覆盖写入该股票扩展K线缓存（最多640根，多余的删除）。"""
    db.execute(f"DELETE FROM `{_LVHI_KLINE_CACHE_TABLE}` WHERE `code` = %s", code)
    if not rows:
        return
    placeholders = ",".join(["(%s, %s, %s)"] * len(rows))
    values = [v for row in rows for v in (code, row[0], row[1])]
    db.execute(f"""
        INSERT INTO `{_LVHI_KLINE_CACHE_TABLE}` (`code`, `trade_date`, `close_price`)
        VALUES {placeholders}
    """, *values)


def _read_lvhi_kline_cache(db, codes):
    """读取扩展K线缓存，返回 {code: {date: close_price}}（全量，按 code 分组）。"""
    result = {}
    if not codes:
        return result
    placeholders = ",".join(["%s"] * len(codes))
    rows = db.query(f"""
        SELECT `code`, `trade_date`, `close_price`
        FROM `{_LVHI_KLINE_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *codes)
    for row in rows:
        result.setdefault(row["code"], {})[str(row["trade_date"])[:10]] = _to_float(row.get("close_price"))
    return result


def _refresh_lvhi_kline(codes, kline_count):
    """刷新扩展K线（daemon 线程内运行，自建独立数据库连接，模式同 market_quotes._refresh_kline_metrics）。"""
    global _KLINE_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        for code in codes:
            try:
                rows = stocklist.fetch_daily_kline_rows(code, count=kline_count)
                if rows:
                    _write_lvhi_kline_cache(db, code, rows)
            except Exception as error:
                print(f"lvhi_portfolio K线刷新跳过 {code}：{error}")
    except Exception as error:
        print(f"lvhi_portfolio._refresh_lvhi_kline处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _KLINE_REFRESH_LOCK:
            _KLINE_REFRESH_RUNNING = False


def _refresh_lvhi_kline_if_needed(db, codes, settings):
    """扩展K线懒刷新：无缓存/覆盖不足/缺最新交易日时刷新。

    首屏无缓存时同步拉取（保证首图有数据）；其余情况起 daemon 线程异步刷新，
    本轮返回旧数据，下轮轮询即为新数据。
    """
    global _KLINE_REFRESH_RUNNING
    codes = [code for code in codes if code]
    if not codes:
        return
    now = _now()
    expected = market_quotes._expected_kline_date(now)
    build_date = str(settings["build_date"])[:10] if settings["build_date"] else None
    placeholders = ",".join(["%s"] * len(codes))
    rows = db.query(f"""
        SELECT `code`, MIN(`trade_date`) AS min_d, MAX(`trade_date`) AS max_d
        FROM `{_LVHI_KLINE_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
        GROUP BY `code`
    """, *codes)
    by_code = {row["code"]: row for row in rows}

    need = []
    no_cache = []
    for code in codes:
        cached = by_code.get(code)
        if cached is None or cached.get("max_d") is None:
            no_cache.append(code)
            need.append(code)
            continue
        max_d = str(cached["max_d"])[:10]
        if max_d < expected:
            need.append(code)
        elif build_date and str(cached["min_d"])[:10] > build_date:
            need.append(code)
    if not need:
        return

    with _KLINE_REFRESH_LOCK:
        if _KLINE_REFRESH_RUNNING:
            return
        _KLINE_REFRESH_RUNNING = True

    if no_cache:
        # 完全无缓存的代码（新增对比目标/首建仓/新持仓）：同步拉取，保证本轮立即可见
        try:
            _refresh_lvhi_kline(no_cache, settings["kline_count"])
        except Exception as error:
            print(f"lvhi_portfolio 无缓存K线同步拉取失败：{error}")
        need = [code for code in need if code not in no_cache]

    if need:
        thread = threading.Thread(
            target=_refresh_lvhi_kline, args=(need, settings["kline_count"]), daemon=True)
        thread.start()


def compute_nav_series(db, now=None):
    """组合净值序列（账本+扩展K线回溯），时间轴=第一个对比目标（配置）交易日。

    全程展示：建仓日前净值=初始资金（100万现金不动），建仓日后按持仓收盘价估值
    （该股无K线数据时用成本价前向填充）。对比目标来自 lvhi_compare_code.txt
    （可手动添加多个，默认红利低波ETF 563020）。返回：
    {dates, nav, normalized_nav, compares: [{code, cg_close, normalized}]}（各序列同长、升序）。
    """
    now = now or _now()
    empty = {"dates": [], "nav": [], "normalized_nav": [], "compares": []}
    settings = get_lvhi_settings(db)
    if not settings["build_status"]:
        return empty

    trades_rows = _read_all_trades(db)
    holdings = _fold_holdings(trades_rows)
    compare_codes = get_compare_codes()
    codes = [h["code"] for h in holdings]
    for cc in compare_codes:
        if cc not in codes:
            codes.append(cc)  # 对比目标的K线随组合一起缓存
    # 惰性刷新扩展K线（首屏同步拉取，之后异步）
    _refresh_lvhi_kline_if_needed(db, codes, settings)
    klines = _read_lvhi_kline_cache(db, codes)
    # 时间轴：第一个有K线数据的对比目标
    axis_code = None
    for cc in compare_codes:
        if klines.get(cc):
            axis_code = cc
            break
    if not axis_code:
        return empty
    dates = sorted(klines[axis_code].keys())
    if not dates:
        return empty

    # 账本事件：现金（每日期末最后一笔）与股数增量（同日顺序无关）
    cash_by_date = {}
    code_events = {}
    for row in trades_rows:
        d = str(row["trade_date"])[:10]
        cash_by_date[d] = float(row.get("cash_after") or 0)
        delta = int(row.get("shares") or 0) if row.get("direction") == "BUY" else -int(row.get("shares") or 0)
        code_events.setdefault(row["code"], []).append((d, delta))
    for evs in code_events.values():
        evs.sort()
    cash_dates = sorted(cash_by_date.keys())
    idx_cash = 0

    avg_cost_by_code = {h["code"]: h["avg_cost"] for h in holdings}
    share_counts = {code: 0 for code in code_events}

    cash = float(settings["initial_capital"])  # 建仓日前无账本事件，现金=初始资金
    nav_list = []
    compare_series = {cc: [] for cc in compare_codes}
    prev_compare_close = {cc: None for cc in compare_codes}
    prev_close_by_code = {code: avg_cost_by_code[code] for code in avg_cost_by_code}
    for d in dates:
        while idx_cash < len(cash_dates) and cash_dates[idx_cash] <= d:
            cash = cash_by_date[cash_dates[idx_cash]]
            idx_cash += 1
        for code, evs in code_events.items():
            while evs and evs[0][0] <= d:
                share_counts[code] += evs[0][1]
                evs.pop(0)

        total_value = cash
        for code in avg_cost_by_code:  # 仅持仓股参与估值（对比目标只作对比）
            close = klines.get(code, {}).get(d)
            if close is None:
                close = prev_close_by_code.get(code)  # 前向填充；仍无则用成本价兜底
            else:
                prev_close_by_code[code] = close
            total_value += share_counts.get(code, 0) * close
        nav_list.append(total_value)

        for cc in compare_codes:
            close = klines.get(cc, {}).get(d)
            if close is None:
                close = prev_compare_close[cc]  # 前向填充（K线未覆盖到的日期沿用前值）
            else:
                prev_compare_close[cc] = close
            compare_series[cc].append(close)

    if not nav_list:
        return empty
    base_nav = nav_list[0]
    compare_names = get_compare_names()
    compares = []
    for cc in compare_codes:
        series = compare_series[cc]
        # 无任何K线数据（刚添加尚未拉取）的对比目标跳过
        base_cg = next((v for v in series if v is not None), None)
        if base_cg is None:
            continue
        compares.append({
            "code": cc,
            "name": compare_names.get(cc, cc),
            "cg_close": [round(v, 4) if v is not None else None for v in series],
            "normalized": [round(v / base_cg, 6) if v is not None else None for v in series],
        })
    return {
        "dates": dates[:len(nav_list)],
        "nav": [round(v, 2) for v in nav_list],
        "normalized_nav": [round(v / base_nav, 6) if base_nav else 1.0 for v in nav_list],
        "compares": compares,
    }
