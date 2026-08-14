FROM docker.io/python:3.11-slim-bullseye

ENV LANG=zh_CN.UTF-8
ENV LC_CTYPE=zh_CN.UTF-8
ENV LC_ALL=C
ENV PYTHONPATH=/data/InStock

EXPOSE 9988

RUN sed -i "s@http://\\(deb\\|security\\).debian.org@https://mirrors.aliyun.com@g" /etc/apt/sources.list && \
    echo "[global]\nindex-url = https://mirrors.aliyun.com/pypi/simple\ntrusted-host = mirrors.aliyun.com" > /etc/pip.conf && \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    apt-get update && \
    apt-get install -y --no-install-recommends supervisor

WORKDIR /data
COPY stock/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm -rf /var/lib/apt/lists/* && apt-get clean

COPY stock /data/InStock

RUN chmod 755 /data/InStock/instock/bin/run_*.sh

ENTRYPOINT ["supervisord", "-n", "-c", "/data/InStock/supervisor/supervisord.conf"]
