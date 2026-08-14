#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import os

import pymysql

__author__ = 'myh '
__date__ = '2026/5/12 '

db_host = os.environ.get("db_host", "localhost")
db_user = os.environ.get("db_user", "root")
db_password = os.environ.get("db_password", "root")
db_database = os.environ.get("db_database", "instockdb")
db_port = int(os.environ.get("db_port", "3306"))
db_charset = os.environ.get("db_charset", "utf8mb4")

MYSQL_CONN = {
    "host": f"{db_host}:{db_port}",
    "user": db_user,
    "password": db_password,
    "database": db_database,
    "charset": db_charset,
    "max_idle_time": 3600,
    "connect_timeout": 1000,
}


def ensure_database_exists():
    """检查并创建数据库，不存在时自动创建。

    带重试机制：首次失败后等待逐次递增的时间重试，最多重试5次。
    防止 MariaDB 容器启动较慢导致连接被拒绝而永久失败。
    """
    import time as _time
    host_parts = db_host.split(":")
    host = host_parts[0]
    port = int(host_parts[1]) if len(host_parts) == 2 else 3306

    conn_args = {
        "host": host,
        "port": port,
        "user": db_user,
        "password": db_password,
        "charset": db_charset,
        "connect_timeout": 10,
    }

    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            conn = pymysql.connect(**conn_args)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{db_database}`"
                    f" CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"
                )
            conn.close()
            logging.info(f"数据库 {db_database} 已就绪。")
            return
        except Exception as e:
            logging.error(
                f"数据库 {db_database} 检查/创建失败 (第{attempt}/{max_retries}次)：{e}"
            )
            if attempt < max_retries:
                _time.sleep(attempt * 2)
