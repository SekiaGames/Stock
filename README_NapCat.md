# NapCat 部署：买卖点信号推送 QQ 群

记录买卖点信号（instock/config/signal_notify_daily.txt）通过 NapCat 推送到 QQ 群的完整部署流程。

## 链路总览

```
页面盘中触发信号 → 写入 signal_notify_daily.txt（文件留档）
   → 同一请求内直接 POST http://NapCat:3000/send_group_msg（OneBot 11 HTTP API）
   → NapCat（QQ小号）→ QQ群
```

InStock 容器在写入信号文件的同时直接调用 NapCat 的 HTTP API 推送，无需宿主机转发脚本；
NapCat 只负责登录 QQ 并把消息发到群。推送失败不影响信号文件写入。

信号文件说明见 [README.md](README.md) 中 `instock/config/signal_notify_daily.txt` 一节：
- 第一行为当天日期，之后每行一条信号，标签带写入时间，如：
  `买入信号·上午9点11分：000680｜山推股份、现价10.84、涨跌幅-4.91%、MA120位置-4.2%、股息率3.6%、扣非10.0%、息增年1、FCF/股息110%、市值100、电力`
- 每股每天只写一次；跨天文件重置（只保留当天）

## 1. 准备 QQ 小号

- 用**备用 QQ 号**（不要用主号）：NapCat 登录后，小号在电脑/手机端会被顶下线
- 小号必须**已加入目标群**（或建一个测试群）
- 建议先在手机上正常登录使用几天再接入，降低新号风控概率

## 2. Docker 部署 NapCat

```bash
docker run -d --name NapCat \
  --network InStockService \
  -p 3000:3000 -p 3001:3001 -p 6099:6099 \
  -e NAPCAT_UID=0 -e NAPCAT_GID=0 \
  -v "$HOME/napcat-qq:/app/.config/QQ" \
  -v "$HOME/napcat-config:/app/napcat/config" \
  --restart=always \
  mlikiowa/napcat-docker:latest
```

**`--network InStockService` 是重点**：与 InStock 容器同网络，InStock 内才能用容器名
`http://NapCat:3000` 访问。如果 NapCat 已经创建但不在该网络：
`docker network connect InStockService NapCat`

端口说明：

| 端口 | 用途 |
|---|---|
| 3000 | OneBot HTTP API（InStock 推送消息用） |
| 3001 | OneBot WebSocket（本项目不需要，可不开） |
| 6099 | NapCat WebUI 管理面板 |

挂载目录持久化登录态，QQ 掉线后自动重连，登录 token 过期才需重新扫码。

## 3. 扫码登录

```bash
docker logs -f NapCat        # 实时日志，顶部出现二维码
docker logs NapCat 2>&1 | tail   # 或查看尾部
```

- 手机 QQ 扫二维码（小号）
- 日志中会打印带 Token 的 WebUI 地址，浏览器打开 `http://localhost:6099`
- 输入 Token 进入管理界面

## 4. 开启 OneBot HTTP

WebUI → 网络配置 → 添加网络 → **HTTP 服务器（正向）**：

- 端口：3000
- Access Token：可设可不设（建议设置，对应填入 qq_push.conf 的 token）
- 本项目只发送、不接收消息，无需配置事件上报

## 5. 验证发送

```bash
curl -X POST http://127.0.0.1:3000/send_group_msg \
  -H "Content-Type: application/json" \
  -d '{"group_id": 123456789, "message": "测试：NapCat 已上线"}'
```

把 `group_id` 换成目标群号（群号可在 QQ 客户端群资料里查看）。群里收到即成功。
如果设置了 Access Token，加头：`-H "Authorization: Bearer <token>"`。

## 6. 开启应用内推送

信号写入时由 InStock 直接调用 NapCat 推送（已实现），无需额外脚本。配置文件
`instock/config/qq_push.conf`（容器启动时自动创建，无需先触发信号）：

```
enabled=1                       # 1 开启推送，0 关闭（默认关闭）
api_url=http://NapCat:3000      # InStock 与 NapCat 同网络时用容器名
group_id=123456789              # 目标 QQ 群号
token=                          # NapCat 设置的 Access Token，未设置留空
```

配置完成后重启 InStock：`docker restart InStock`。

行为说明：
- 信号写入 signal_notify_daily.txt 后立即推送，消息为信号行原文（纯文本，无需 CQ 码）
- 推送失败自动重试一次，仍失败只记日志，**不影响文件写入**
- 推送跟随写入侧去重：每股每天最多推一次，跨天文件重置
- 未开启（enabled=0）时零网络请求，不影响页面

## 7. 注意事项

- **风控**：NapCat 是第三方 QQ 协议实现，登录有账号风控风险，务必使用小号；本场景每天仅几条消息，触发频率低
- **掉线重登**：QQ 登录态过期需重新扫码；`restart: always` + 挂载目录可自动重连
- **只发不监听**：无需接收群消息，OneBot 配置面最小化
- **信号文件由后台定时刷新自动写入**（默认每5分钟一次，与页面轮询互斥执行），无需打开前端页面；间隔与开关见 instock/config/scheduler.conf（enabled=0 关闭，docker restart InStock 生效）

## 8. 常见问题

| 问题 | 处理 |
|---|---|
| 收不到群消息 | 确认小号已入群、group_id 正确、HTTP 端口已开启 |
| 提示无权限 | 群成员发言限制，本场景消息量小通常无影响 |
| 容器日志无二维码 | `docker restart NapCat` 重新生成；确认挂载目录权限 |
| HTTP 401 | 请求缺少或写错 Access Token |
| HTTP 000 / 连接被重置(RST) | 通常是 OneBot HTTP 服务器未创建：WebUI 网络配置新建 HTTP 服务器，或直接编辑挂载目录 `onebot11_<QQ号>.json` 的 `network.httpServers` 后 `docker restart NapCat` |
| token 填错不生效 | 日志里 `WebUi Token` 是**管理面板登录 token**，不是 OneBot API token；两者同名不同物，注意区分 |
| 重启后需重新扫码 | QQ 登录态过期属正常；扫码一次后观察是否持久化，频繁掉线再排查挂载目录权限 |
