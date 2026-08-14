#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import logging
import os.path
import sys

import tornado.httpserver
import tornado.ioloop
import tornado.options

# 在项目运行时，临时将项目路径添加到环境变量
cpath_current = os.path.dirname(os.path.dirname(__file__))
cpath = os.path.abspath(os.path.join(cpath_current, os.pardir))
sys.path.append(cpath)
log_path = os.path.join(cpath_current, 'log')
if not os.path.exists(log_path):
    os.makedirs(log_path)
logging.basicConfig(format='%(asctime)s %(message)s', filename=os.path.join(log_path, 'stock_web.log'))
logging.getLogger().setLevel(logging.ERROR)
import instock.core.signal_notify as signal_notify
import instock.core.high_attention as high_attention
import instock.lib.database as mdb
import instock.lib.mysql as mysql
import instock.web.highDividendHandler as highDividendHandler
import instock.web.base as webBase
import instock.web.scheduler as scheduler

__author__ = 'myh '
__date__ = '2026/5/12 '

class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            # 设置路由
            (r"/", HomeHandler),
            (r"/instock/", HomeHandler),
            (r"/instock/high_dividend", highDividendHandler.HighDividendPageHandler),
            (r"/instock/high_dividend/api", highDividendHandler.HighDividendDataHandler),
            (r"/instock/high_dividend/followlist", highDividendHandler.FollowListHandler),
        ]
        settings = dict(  # 配置
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            static_path=os.path.join(os.path.dirname(__file__), "static"),
            debug=True,
        )
        super(Application, self).__init__(handlers, **settings)
        # Have one global connection to the blog DB across all handlers
        self.db = mysql.Connection(**mdb.MYSQL_CONN)


# 首页handler。
class HomeHandler(webBase.BaseHandler):
    def get(self):
        self.render("high_dividend.html")


def main():
    # tornado.options.parse_command_line()
    tornado.options.options.logging = None

    mdb.ensure_database_exists()

    # 配置文件不存在时自动创建，方便提前配置（qq_push.conf、signal_filter.txt、high_attention_daily.txt、scheduler.conf）
    signal_notify.ensure_push_config_file()
    signal_notify.ensure_filter_file()
    high_attention.ensure_high_attention_file()
    scheduler.ensure_scheduler_config_file()

    http_server = tornado.httpserver.HTTPServer(Application())
    port = 9988
    http_server.listen(port)

    print(f"服务已启动，web地址 : http://localhost:{port}/")
    logging.error(f"服务已启动，web地址 : http://localhost:{port}/")

    # 后台定时刷新（无浏览器时数据刷新/信号写入的驱动，见 scheduler.py）
    scheduler.start_scheduler()

    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
