#!/usr/local/bin/python3
# -*- coding: utf-8 -*-
"""后台定时刷新调度器：服务进程按 instock/config/scheduler.conf 的间隔执行
数据抓取+信号写入流水线（见 highDividendHandler.run_pipeline），
前端页面轮询为纯只读（不触发抓取/写文件），无浏览器时也能刷新数据并写入信号文件。"""

import os
import threading
import time

import instock.lib.database as mdb
import instock.lib.mysql as mysql
from instock.core.common import _now
from instock.web import highDividendHandler

_CONFIG_FILE_NAME = "scheduler.conf"
_CONFIG_DEFAULT_TEXT = """\
# 后台定时刷新调度配置：服务进程按固定间隔执行数据抓取+信号写入流水线
# enabled：1 开启（默认），0 关闭（数据不再定时刷新，前端仅显示缓存）
# interval_minutes：执行间隔（分钟），支持小数（如 0.5 用于测试），非法值回退默认5
enabled=1
interval_minutes=5
# frontend_refresh_minutes：前端页面刷新间隔（分钟），支持小数；
# 纯页面刷新只读缓存，不触发后端抓取/写文件，不影响后台调度，非法值回退默认1
frontend_refresh_minutes=1
"""
_DEFAULT_INTERVAL_MINUTES = 5.0
_MIN_INTERVAL_MINUTES = 0.1
_DEFAULT_FRONTEND_REFRESH_MINUTES = 1.0


def _config_path():
    """instock/config/scheduler.conf，与 core 模块配置路径同规则。"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", _CONFIG_FILE_NAME)


def ensure_scheduler_config_file():
    """配置文件不存在时创建默认模板（模式与 signal_notify.ensure_push_config_file 一致）。"""
    path = _config_path()
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_CONFIG_DEFAULT_TEXT)
        except OSError as error:
            print(f"scheduler 配置文件创建失败：{error}")


def _read_scheduler_config():
    """读取配置，返回 (enabled: bool, interval_seconds: float, frontend_interval_seconds: float)。
    解析失败回退默认（开启、5分钟调度、前端1分钟），保证无浏览器场景下刷新不中断。"""
    enabled = True
    interval_minutes = _DEFAULT_INTERVAL_MINUTES
    frontend_refresh_minutes = _DEFAULT_FRONTEND_REFRESH_MINUTES
    path = _config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if key == "enabled":
                    enabled = value != "0"
                elif key == "interval_minutes":
                    try:
                        interval_minutes = max(_MIN_INTERVAL_MINUTES, float(value))
                    except ValueError:
                        pass
                elif key == "frontend_refresh_minutes":
                    try:
                        frontend_refresh_minutes = max(_MIN_INTERVAL_MINUTES, float(value))
                    except ValueError:
                        pass
    except OSError as error:
        print(f"scheduler 配置读取失败，使用默认值：{error}")
    return enabled, interval_minutes * 60, frontend_refresh_minutes * 60


def get_frontend_refresh_interval_ms():
    """前端页面轮询刷新间隔（毫秒），供 /high_dividend/api 接口返回、页面定时刷新使用。
    每轮请求读取配置，改配置无需重启即生效。"""
    _, _, frontend_seconds = _read_scheduler_config()
    return int(frontend_seconds * 1000)


def _run_refresh_tick():
    """执行一次完整流水线。异常只记日志，不影响调度循环。"""
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        result = highDividendHandler.run_pipeline(db)
        print(f"scheduler 刷新完成：{_now().strftime('%Y-%m-%d %H:%M:%S')}，"
              f"股票 {len(result['rows'])} 只，错误 {len(result['errors'])} 条")
    except Exception as error:
        print(f"scheduler 刷新流水线异常：{error}")
    finally:
        if db is not None:
            db.close()


def _scheduler_loop():
    # 启动立即执行一次（headless 冷启动即完成首次抓取），
    # 之后每轮重读配置再执行并 sleep（改配置无需重启生效）
    while True:
        enabled, interval_seconds, _ = _read_scheduler_config()
        if enabled:
            _run_refresh_tick()
        time.sleep(interval_seconds)


def start_scheduler():
    """启动后台调度线程（daemon，随进程退出）。由 web_service.main() 调用。"""
    ensure_scheduler_config_file()
    threading.Thread(target=_scheduler_loop, daemon=True).start()
