#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import random
import re
import time
import threading

import requests

__author__ = 'myh '
__date__ = '2026/5/12 '

_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_REQUEST_INTERVAL_SECONDS = 0.4


def _throttle_request():
    # 间隔加 ±20% 抖动，避免固定节律被风控识别
    global _LAST_REQUEST_AT
    with _REQUEST_LOCK:
        elapsed = time.time() - _LAST_REQUEST_AT
        target = _REQUEST_INTERVAL_SECONDS * random.uniform(0.8, 1.2)
        if elapsed < target:
            time.sleep(target - elapsed)
        _LAST_REQUEST_AT = time.time()

DEFAULT_STOCK_CODES = ("600900",)
_CODE_PATTERN = re.compile(r"(?<!\d)(?:sh|sz|bj)?(\d{6})(?!\d)", re.IGNORECASE)


def _candidate_paths():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return (
        os.environ.get("INSTOCK_STOCKLIST_PATH"),
        os.environ.get("STOCKLIST_PATH"),
        os.path.join(base_dir, "config", "stocklist.txt"),
    )


def _read_codes_from_file(path):
    codes = []
    if not path or not os.path.isfile(path):
        return codes

    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.split("#", 1)[0].split("//", 1)[0].strip()
            if not line:
                continue
            if line in ("*", "ALL", "all"):
                return ["*"]
            match = _CODE_PATTERN.search(line)
            if match:
                codes.append(match.group(1))
    return codes


def get_stock_names():
    """从 stocklist.txt 读取股票代码到名称的映射。"""
    names = {}
    for path in _candidate_paths():
        if not path or not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as file:
            for line in file:
                line = line.split("#", 1)[0].split("//", 1)[0].strip()
                if not line:
                    continue
                match = _CODE_PATTERN.search(line)
                if match:
                    code = match.group(1)
                    name_start = match.end()
                    name = line[name_start:].strip()
                    if code not in names:
                        names[code] = name
        if names:
            return names
    return names


def get_stock_codes():
    for path in _candidate_paths():
        codes = _read_codes_from_file(path)
        if codes:
            if codes == ["*"]:
                return DEFAULT_STOCK_CODES
            return tuple(dict.fromkeys(codes))
    return DEFAULT_STOCK_CODES


def _blocklist_candidate_paths():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return (
        os.environ.get("INSTOCK_BLOCKLIST_PATH"),
        os.environ.get("BLOCKLIST_PATH"),
        os.path.join(base_dir, "config", "blocklist_industry.txt"),
    )


def get_blocked_industries():
    """从 blocklist_industry.txt 读取需要屏蔽的申万二级行业列表。"""
    blocked = set()
    for path in _blocklist_candidate_paths():
        if not path or not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as file:
            for line in file:
                line = line.split("#", 1)[0].split("//", 1)[0].strip()
                if not line:
                    continue
                blocked.add(line)
        if blocked:
            return blocked
    return blocked


def is_a_stock_code(code):
    return str(code).startswith(('600', '601', '603', '605', '000', '001', '002', '003', '300', '301'))


_BATCH_SIZE = 100


def _batch_codes(codes):
    """按100只一批拆分请求。"""
    codes = list(codes)
    for i in range(0, len(codes), _BATCH_SIZE):
        yield codes[i:i + _BATCH_SIZE]


def fetch_market_cap_data(codes):
    """从腾讯行情接口分批获取流通市值（亿），每批100只一次请求，返回 {code: market_cap}。

    市值随行情变化，需要定期刷新，与行业分开独立请求。
    """
    result = {}
    if not codes:
        return result
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://gu.qq.com/",
    }
    for batch in _batch_codes(codes):
        symbols = ",".join(f"{'sh' if code.startswith('6') else 'sz'}{code}" for code in batch)
        url = f"https://qt.gtimg.cn/q={symbols}"
        try:
            _throttle_request()
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            for line in resp.text.strip().split(";"):
                if '="' not in line:
                    continue
                symbol_part, data = line.split('="', 1)
                code = symbol_part.split("_")[-1][-6:]
                fields = data.strip('"').split("~")
                if len(fields) > 45:
                    market_cap = _to_float(fields[45])
                    if market_cap is not None:
                        result[code] = market_cap
        except Exception:
            pass
    return result


def fetch_industry_data(codes):
    """从东方财富 F10 分批获取申万二级行业，每批100只一次请求，返回 {code: industry_name}。

    行业基本不变，只在无缓存时请求一次，不需要定期刷新。
    全量一次请求 filter 过长会被拒绝，必须分批。
    """
    result = {}
    if not codes:
        return result
    codes = list(codes)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/",
    }
    for batch in _batch_codes(codes):
        code_list = '","'.join(batch)
        url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
        params = {
            "reportName": "RPT_F10_ORG_BASICINFO",
            "columns": "SECURITY_CODE,BOARD_NAME_2LEVEL",
            "filter": f'(SECURITY_CODE in ("{code_list}"))',
            "pageNumber": "1",
            "pageSize": str(len(batch) + 10),
            "source": "HSF10",
        }
        try:
            _throttle_request()
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("success") and payload.get("result", {}).get("data"):
                for row in payload["result"]["data"]:
                    code = row.get("SECURITY_CODE", "")
                    industry_name = row.get("BOARD_NAME_2LEVEL") or ""
                    if code in codes and industry_name:
                        result[code] = industry_name
        except Exception:
            pass
    return result


def make_selected_stock_rows(date, codes=None):
    """从腾讯行情接口分批获取实时现价数据，每批100只一次请求。

    codes 为空时获取全部股票（原行为），传入 codes 时只获取指定股票（行情按需刷新）。
    原使用新浪 hq.sinajs.cn，该域名已不可达（403）。
    单批失败跳过，不影响其余批次。
    """
    if codes is None:
        codes = [code for code in get_stock_codes() if is_a_stock_code(code)]
    else:
        codes = [code for code in codes if is_a_stock_code(code)]
    if not codes:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://gu.qq.com/",
    }
    rows = []
    for batch in _batch_codes(codes):
        symbols = ",".join(f"{'sh' if code.startswith('6') else 'sz'}{code}" for code in batch)
        url = f"https://qt.gtimg.cn/q={symbols}"
        try:
            _throttle_request()
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            rows.extend(_parse_quote_lines(response.text, date))
        except Exception:
            # 单批失败跳过，不影响其余批次
            continue
    return rows or None


def _parse_quote_lines(text, date):
    """解析腾讯行情接口返回文本为现价行列表。"""
    rows = []
    for line in text.strip().split(";"):
        if '="' not in line:
            continue
        symbol_part, data = line.split('="', 1)
        symbol = symbol_part.split("_")[-1]
        code = symbol[-6:]
        fields = data.strip('"').split("~")
        if len(fields) < 38 or not fields[1]:
            continue

        # Tencent qt 字段（0-indexed）:
        # 1=名称 3=现价 4=昨收 5=今开 6=成交量(手)
        # 30=日期时间(YYYYMMDDHHMMSS) 31=涨跌额 32=涨跌幅%
        # 33=最高 34=最低 35=现价/成交量/成交额(元)
        # 37=成交额(万元)
        name = fields[1]
        open_price = _to_float(fields[5])
        pre_close = _to_float(fields[4])
        new_price = _to_float(fields[3])
        high_price = _to_float(fields[33])
        low_price = _to_float(fields[34])
        volume = _to_float(fields[6])

        # 成交额：优先从 field 35 解析（格式 现价/成交量/成交额），否则用 field 37 (万元)
        deal_amount = None
        if len(fields) > 35 and fields[35]:
            parts = fields[35].split("/")
            if len(parts) >= 3:
                deal_amount = _to_float(parts[2])
        if deal_amount is None and len(fields) > 37:
            deal_amount_wan = _to_float(fields[37])
            if deal_amount_wan is not None:
                deal_amount = deal_amount_wan * 10000

        change = None if new_price is None or pre_close in (None, 0) else new_price - pre_close
        change_rate = _to_float(fields[32])
        amplitude = None if high_price is None or low_price is None or pre_close in (None, 0) else (
            high_price - low_price) / pre_close * 100

        rows.append({
            "date": date.strftime("%Y-%m-%d") if date is not None else fields[30][:10],
            "code": code,
            "name": name,
            "new_price": new_price,
            "change_rate": change_rate,
            "ups_downs": change,
            "volume": volume,
            "deal_amount": deal_amount,
            "amplitude": amplitude,
            "open_price": open_price,
            "high_price": high_price,
            "low_price": low_price,
            "pre_close_price": pre_close,
        })

    return rows


def fetch_daily_kline_rows(code, today=None, count=125):
    """请求前复权日K，返回升序 [(trade_date, open, close, high, low, volume), ...]。

    腾讯K线API返回前复权（qfq）日线数据，
    确保MA120计算时历史价格已就除权除息进行调整，
    与主流股票APP的MA120数值一致。
    volume 为成交量（手），供换手率类指标计算。
    count 为请求根数：默认125（= 120根MA120 + 盘中排除当日未完成K线1根 + 少量容错余量），
    扩展根数（320/640/1024）用于组合净值回溯等需要更长历史的场景；
    扩展根数请求失败（超时/被拒）时自动降级为默认125根重试一次。
    若 today 传入日期，则排除该日期及之后的K线（用于盘中排除当日未完成K线）。
    上市不足 count 根时返回实际可用的K线；请求或解析失败返回 None。
    """
    market = "sh" if code.startswith(("6", "5")) else "sz"  # 6xx 沪市A股，5xx 沪市ETF/基金
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://gu.qq.com/",
    }

    def _request(cnt):
        _throttle_request()
        params = {"param": f"{market}{code},day,,,{cnt},qfq"}
        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        return _parse_kline_payload(response.json(), market, code, today)

    try:
        return _request(count)
    except Exception:
        if count > 125:
            try:
                return _request(125)
            except Exception:
                return None
        return None


def _parse_kline_payload(payload, market, code, today):
    """解析腾讯K线接口返回为升序 [(trade_date, open, close, high, low, volume), ...]。"""
    stock_data = payload.get("data", {}).get(f"{market}{code}")
    if not stock_data:
        return None
    klines = stock_data.get("qfqday") or stock_data.get("day")
    if not klines:
        return None

    if today is not None:
        today_text = today.strftime("%Y-%m-%d") if hasattr(today, "strftime") else str(today)[:10]
        klines = [item for item in klines if str(item[0])[:10] < today_text]

    rows = []
    for item in klines:
        if len(item) < 6:
            continue
        # item format: [date, open, close, high, low, volume, ...]
        open_price = _to_float(item[1])
        close_price = _to_float(item[2])
        if close_price is None or close_price <= 0:
            continue
        trade_date = str(item[0])[:10]
        if not trade_date:
            continue
        rows.append((trade_date, open_price, close_price,
                     _to_float(item[3]), _to_float(item[4]), _to_float(item[5])))

    return rows or None


def _liq_base_volume(rows):
    """基准交易量：250根K线中前120根的日均成交量（换手基准）；不足120根返回 None。

    rows 为升序 [(trade_date, open, close, high, low, volume), ...]。
    """
    if not rows or len(rows) < 120:
        return None
    volumes = [row[5] for row in rows[:120]]
    if any(v is None or v <= 0 for v in volumes):
        return None
    return sum(volumes) / len(volumes)


def compute_liq_daily_series(rows, total_share=None):
    """流动性当日序列（积分前的每日原始信号）：当日 = 当日涨跌幅 × (基准交易量/当日交易量) × 100。

    基准交易量 = 250根K线前120根的日均成交量；涨跌幅 = 当日收盘/前一日收盘 − 1。
    下跌且缩量（基准量/当日量 > 1）→ 负值被放大（抛压被流动性放大，超卖/黄金坑方向）；
    上涨且放量 → 正值被缩小（超买方向）。首根无涨跌幅或当日无量返回 None。
    """
    base = _liq_base_volume(rows)
    if base is None:
        return [None] * len(rows)
    series = [None] * len(rows)
    prev_close = None
    for i, row in enumerate(rows):
        close = row[2]
        volume = row[5]
        if prev_close is not None and prev_close > 0 and close is not None and volume and volume > 0:
            ret = close / prev_close - 1
            series[i] = ret * (base / volume) * 100
        prev_close = close
    return series


def compute_liq_series(rows, total_share=None):
    """流动性积分序列（累加法，0分起算），供个股分析页K线辅助指标绘制。

    rows 为升序 [(trade_date, open, close, high, low, volume), ...]（250根）：
    前120根只用于计算基准交易量；第121根（索引120）积分 = 0 + 当日，
    之后逐日累加：积分 = 前日积分 + 当日（流动性当日 = 涨跌幅 × 基准交易量/当日交易量）。
    返回与 rows 等长的列表，基线不足的日期为 None。
    """
    daily = compute_liq_daily_series(rows, total_share)
    series = [None] * len(rows)
    acc = 0.0
    for i in range(len(rows)):
        if i < 120:
            continue
        if daily[i] is None:
            continue
        acc += daily[i]
        series[i] = acc
    return series


def _liq_vol20(rows):
    """近20日收益率标准差（%），供流动性指标tooltip显示波动环境。"""
    if len(rows) < 20:
        return None
    daily_rets = []
    prev_close = None
    for row in rows:
        close = row[2]
        if prev_close is not None and prev_close > 0 and close is not None:
            daily_rets.append(close / prev_close - 1)
        prev_close = close
    if len(daily_rets) < 20:
        return None
    recent = daily_rets[-20:]
    mean = sum(recent) / len(recent)
    return (sum((r - mean) ** 2 for r in recent) / len(recent)) ** 0.5 * 100


def compute_liq_oversold(rows, total_share=None):
    """计算最新一日流动性积分与当日值，返回 dict（含分项）或 None。

    与 compute_liq_series 同口径：前120根基准交易量 + 累加积分；
    分项返回当日涨跌幅 price_pos 与量能放大比 turnover_pct（基准交易量/当日交易量），
    供前端tooltip展示。
    """
    if not rows or len(rows) < 121:
        return None
    daily = compute_liq_daily_series(rows, total_share)
    series = compute_liq_series(rows, total_share)
    last_score = series[-1] if series else None
    if last_score is None:
        return None
    ret = None
    if rows[-2][2] and rows[-2][2] > 0 and rows[-1][2] is not None:
        ret = rows[-1][2] / rows[-2][2] - 1
    vol_ratio = None
    if rows[-1][5] and rows[-1][5] > 0:
        base = _liq_base_volume(rows)
        vol_ratio = base / rows[-1][5]
    turnover = None
    if total_share:
        turnover = rows[-1][5] * 100 / total_share * 100
    return {
        "trade_date": rows[-1][0],
        "liq_score": last_score,
        "liq_daily": daily[-1],
        "base_volume": _liq_base_volume(rows),
        "price_pos": ret,
        "turnover_pct": vol_ratio,
        "pressure_pct": None,
        "vol20": _liq_vol20(rows[-120:]),
        "turnover": turnover,
    }


def compute_kline_metrics(rows, current_price=None):
    """从升序日K行计算 MA120，同份数据复用。

    MA120取后120根收盘价；current_price 若传入正数，则优先用作当前价格计算位置（盘中实时价格）。
    返回 {"ma120": {...}|None}，数据不足时 ma120 为 None。
    """
    result = {"ma120": None}
    if not rows:
        return result

    # MA120：后120根收盘价
    if len(rows) >= 120:
        trade_date, _, close_price, _, _, _ = rows[-1]
        ma120 = sum(row[2] for row in rows[-120:]) / 120
        if ma120 > 0:
            effective_close = current_price if current_price is not None and current_price > 0 else close_price
            result["ma120"] = {
                "trade_date": trade_date,
                "close_price": effective_close,
                "ma120": ma120,
                "ma120_position": (effective_close / ma120 - 1) * 100,
            }

    return result


def fetch_daily_kline_metrics(code, today=None, current_price=None):
    """请求一次并计算 MA120（请求+计算的便捷组合）。"""
    rows = fetch_daily_kline_rows(code, today)
    if rows is None:
        return None
    return compute_kline_metrics(rows, current_price)


def _to_float(value):
    try:
        return float(value)
    except Exception:
        return None
