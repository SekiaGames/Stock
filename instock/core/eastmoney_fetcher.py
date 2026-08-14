#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
import random
import threading

__author__ = 'myh '
__date__ = '2025/12/31 '

class eastmoney_fetcher:
    """
    东方财富网数据获取器
    封装了会话管理和请求发送功能
    """

    _request_interval = 1
    _last_request_time = 0
    _request_lock = threading.Lock()

    def __init__(self):
        """初始化获取器"""
        self.session = self._create_session()

    def _create_session(self):
        """创建并配置会话"""
        session = requests.Session()

        # 配置连接池
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "OPTIONS"]
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=50,  # 增加连接池大小
            pool_maxsize=50  # 增加连接池最大大小
        )

        # 为http和https请求添加适配器
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # 设置请求头
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://quote.eastmoney.com/',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        session.headers.update(headers)
        return session

    def _normalize_eastmoney_url(self, url):
        """Use the stable Eastmoney host and HTTPS where supported."""
        push2_hosts = (
            "80.push2.eastmoney.com",
            "82.push2.eastmoney.com",
            "88.push2.eastmoney.com",
        )
        for host in push2_hosts:
            url = url.replace(f"://{host}", "://push2.eastmoney.com")

        if url.startswith("http://") and "eastmoney.com" in url:
            url = "https://" + url[len("http://"):]
        return url

    def _throttle_request(self):
        # 间隔加 ±20% 抖动，避免固定节律被风控识别
        with self._request_lock:
            elapsed = time.time() - self._last_request_time
            target = self._request_interval * random.uniform(0.8, 1.2)
            if elapsed < target:
                time.sleep(target - elapsed)
            self.__class__._last_request_time = time.time()

    def make_request(self, url, params=None, retry=3, timeout=10):
        """
        发送请求
        :param url: 请求URL
        :param params: 请求参数
        :param retry: 重试次数
        :param timeout: 超时时间
        :return: 响应对象
        """

        url = self._normalize_eastmoney_url(url)

        for i in range(retry):
            try:
                self._throttle_request()
                response = self.session.get(
                    url,
                    params=params,
                    timeout=timeout
                )
                response.raise_for_status()  # 检查HTTP错误
                return response
            except requests.exceptions.RequestException as e:
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                print(f"请求错误: {e}, 第 {i + 1}/{retry} 次重试")
                if i < retry - 1:
                    if status_code in (403, 429):
                        # 被限流/拦截时大幅退避，避免继续高频撞击导致封IP
                        print("检测到限流/拦截，退避 60 秒后重试")
                        time.sleep(60)
                    else:
                        # 随机延迟后重试
                        time.sleep(random.uniform(1, 3))
                else:
                    raise
