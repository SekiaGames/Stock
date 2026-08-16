# 高股息列表-腾讯云

将本应用部署到 Rocky Linux 9.4（x86_64）服务器的完整流程。
与 [README.md](README.md)（macOS 本机部署）的区别：

- 包管理用 dnf 代替 brew
- 安装 Docker Engine（systemd 常驻服务），无 Docker Desktop
- 仓库通过 git clone 获取，不再挂载本机目录，本机不编辑上传。
- 安装Docker容器时，多了`:Z`，用于应对Rocky独有的SELinux机制。

## 0. 连接服务器

## 1. 环境准备
腾讯云-轻量服务器-防火墙
- Instock：9988/tcp
- NapCat：6099/tcp
- 80/tcp（HTTP）
- 443/tcp（HTTPS）

设置时区（可选，容器内已固定为 Asia/Shanghai）：
```bash
sudo timedatectl set-timezone Asia/Shanghai
```

## 2. 下载安装Docker、下载容器、下载Git仓库

```bash
sudo dnf install -y dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
docker version

# 拉取Github仓库
sudo dnf install -y git
git clone https://github.com/SekiaGames/Stock $HOME/Stock
#注：仓库中 run_web.sh 应已带可执行权限。仓库实际位置：/root/Stock
```

## 3. 创建Docker网络和3个容器

```bash
# 创建本地常驻目录
mkdir -p "$HOME/instock-mariadb-data"
docker network create InStockService

docker run -d --name InStockDbService \
  --network InStockService \
  -v "$HOME/instock-mariadb-data:/var/lib/mysql:Z" \
  -e MARIADB_ROOT_PASSWORD=root \
  --restart=always \
  mariadb:latest

docker run -dit --name InStock \
  --network InStockService \
  -p 9988:9988 \
  -v $HOME/Stock:/data/InStock:Z \
  -e db_host=InStockDbService \
  --restart=always \
  mayanghua/instock:latest

docker run -d --name NapCat \
  --network InStockService \
  -p 3000:3000 -p 3001:3001 -p 6099:6099 \
  -e NAPCAT_UID=0 -e NAPCAT_GID=0 \
  -v "$HOME/napcat-qq:/app/.config/QQ:Z" \
  -v "$HOME/napcat-config:/app/napcat/config:Z" \
  --restart=always \
  mlikiowa/napcat-docker:latest
```
参数解释：
- `$HOME/Stock` 仓库地址，按实际情况修改
- `/data/InStock` 为容器内固定路径，映射到仓库地址，不要修改
- `$HOME/instock-mariadb-data` mariadb数据存储目录

## 4. NapCat使用
```bash
# 获取NapCat`登录token`
docker logs -f NapCat
docker logs --tail 200 NapCat
#退出日志模式：Ctrl+C
```

远程访问NapCat：
http://<服务器IP>:6099
输入`登录token`后QQ登录

添加HTTP服务器（默认设置就行）：
WebUI → 网络配置 → 添加网络 → **HTTP 服务器**：
- 启动：勾选
- 名称：随便填
- Gost：0.0.0.0
- 端口：3000
- 消息格式：Array
- AccessToken：直接用默认值

开启QQ推送：
```bash
# 先确认文件已生成
ls -l $HOME/Stock/instock/config/qq_push.conf

sudo sed -i 's/^enabled=.*/enabled=1/' $HOME/Stock/instock/config/qq_push.conf
# 替换QQ群号
sudo sed -i 's/^group_id=.*/group_id=123456789/' $HOME/Stock/instock/config/qq_push.conf
# 替换AccessToken
sudo sed -i 's/^token=.*/token=这里填AccessToken/' $HOME/Stock/instock/config/qq_push.conf

# 3. 生效
docker restart InStock
```

## 5. 日常维护

```bash
# 查看日志
docker logs -f InStock
docker logs -f InStockDbService
docker logs --tail 200 InStock

# 重启
docker restart InStock

# 版本更新
rm -rf $HOME/Stock
git clone https://github.com/SekiaGames/Stock $HOME/Stock
docker stop InStockDbService NapCat InStock
git -C $HOME/Stock fetch origin && git -C $HOME/Stock reset --hard origin/main
docker restart InStockDbService NapCat InStock
```

## 6. 绑定域名 + nginx 反向代理（海外服务器无需备案）

```bash
# Swap分区：小内存服务器建议开启（4G内存也保留1G作OOM保险，防MariaDB等容器被OOM-kill）
# 自动按内存大小分配：<2G→2G，≥2G→1G
TOTAL_MEM=$(free -m | awk '/^Mem:/{print $2}')
if [ "$TOTAL_MEM" -lt 2048 ]; then SWAP_SIZE=2G; else SWAP_SIZE=1G; fi
# 已存在旧Swap时先关闭删除（如2G缩小到1G），全新服务器这两行会直接跳过
sudo swapoff /swapfile 2>/dev/null || true
sudo rm -f /swapfile 2>/dev/null || true
sudo fallocate -l $SWAP_SIZE /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# 降低swap使用倾向（默认60→10）：内存充足时优先用内存，swap仅作保险
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf
sudo sysctl -p /etc/sysctl.d/99-swap.conf
free -h   # 确认 Swap 生效

#安装Nginx
sudo dnf install -y nginx
sudo systemctl enable --now nginx

sudo tee /etc/nginx/conf.d/instock.conf > /dev/null <<'EOF'
server {
    listen 80;
    server_name stock.sekia.games;

    location / {
        proxy_pass http://127.0.0.1:9988;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo nginx -t
# 之后访问http://stock.sekia.games
```

### 6.4 申请 HTTPS 证书

```bash
# 1. 安装 acme.sh（装到 /root/.acme.sh）
curl https://get.acme.sh | sh -s email=hesitia@qq.com

# 2. 签发证书（自动用 nginx 的 80 端口校验，自动重载 nginx）
~/.acme.sh/acme.sh --issue -d stock.sekia.games --nginx --server letsencrypt

# 3. 安装证书到固定路径（续期后自动更新，并自动重载 nginx）
sudo mkdir -p /etc/nginx/ssl

~/.acme.sh/acme.sh --install-cert -d stock.sekia.games \
  --key-file /etc/nginx/ssl/stock.sekia.games.key \
  --fullchain-file /etc/nginx/ssl/stock.sekia.games.pem \
  --reloadcmd "systemctl reload nginx"
```

改 nginx 配置：替换原配置，加 443 server 块、80 端口跳转 HTTPS：

```bash
sudo tee /etc/nginx/conf.d/instock.conf > /dev/null <<'EOF'
server {
    listen 80;
    server_name stock.sekia.games;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name stock.sekia.games;

    ssl_certificate     /etc/nginx/ssl/stock.sekia.games.pem;
    ssl_certificate_key /etc/nginx/ssl/stock.sekia.games.key;

    location / {
        proxy_pass http://127.0.0.1:9988;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo nginx -t && sudo systemctl reload nginx
```

续期全自动（acme.sh 安装时会自动加 crontab 定时任务），手动续期：`~/.acme.sh/acme.sh --renew-all`。

完成后访问 `https://stock.sekia.games` 即可。
