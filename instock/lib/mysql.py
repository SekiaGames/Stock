#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import time

import pymysql

__author__ = 'myh '
__date__ = '2026/5/12 '


class Row(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class Connection:
    def __init__(self, host, database, user=None, password=None, charset="utf8mb4",
                 max_idle_time=3600, connect_timeout=10, time_zone="+0:00",
                 sql_mode="TRADITIONAL"):
        self.host = host
        self.database = database
        self.max_idle_time = float(max_idle_time)
        self._db = None
        self._last_use_time = 0
        self._db_args = {
            "db": database,
            "charset": charset,
            "connect_timeout": connect_timeout,
            "init_command": f'SET time_zone = "{time_zone}"',
            "sql_mode": sql_mode,
        }

        if user is not None:
            self._db_args["user"] = user
        if password is not None:
            self._db_args["passwd"] = password

        if "/" in host:
            self._db_args["unix_socket"] = host
        else:
            host_parts = host.split(":")
            self._db_args["host"] = host_parts[0]
            self._db_args["port"] = int(host_parts[1]) if len(host_parts) == 2 else 3306

        try:
            self.reconnect()
        except Exception:
            logging.error(f"Cannot connect to MySQL on {self.host}", exc_info=True)

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None

    def reconnect(self):
        self.close()
        self._db = pymysql.connect(**self._db_args)
        self._db.autocommit(True)
        self._last_use_time = time.time()

    def query(self, query, *parameters, **kwparameters):
        with self._cursor() as cursor:
            self._execute(cursor, query, parameters, kwparameters)
            return [Row(row) for row in cursor.fetchall()]

    def get(self, query, *parameters, **kwparameters):
        rows = self.query(query, *parameters, **kwparameters)
        if not rows:
            return None
        if len(rows) > 1:
            raise Exception("Multiple rows returned for Connection.get() query")
        return rows[0]

    def execute(self, query, *parameters, **kwparameters):
        with self._cursor() as cursor:
            self._execute(cursor, query, parameters, kwparameters)
            return cursor.lastrowid

    def _ensure_connected(self):
        if self._db is None or time.time() - self._last_use_time > self.max_idle_time:
            self.reconnect()
        self._last_use_time = time.time()

    def _cursor(self):
        self._ensure_connected()
        return self._db.cursor(pymysql.cursors.DictCursor)

    def _execute(self, cursor, query, parameters, kwparameters):
        try:
            return cursor.execute(query, kwparameters or parameters)
        except pymysql.OperationalError:
            logging.error(f"Error connecting to MySQL on {self.host}", exc_info=True)
            self.close()
            raise
