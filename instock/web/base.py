#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import tornado.web

__author__ = 'myh '
__date__ = '2026/5/12 '


# 基础handler，主要负责检查mysql的数据库链接。
class BaseHandler(tornado.web.RequestHandler):
    @property
    def db(self):
        try:
            # check every time。
            self.application.db.query("SELECT 1 ")
        except Exception as e:
            print(e)
            self.application.db.reconnect()
        return self.application.db
