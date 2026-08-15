# 高股息列表 — Rocky Linux 9.8 部署指南

将本应用部署到 Rocky Linux 9.8（x86_64）服务器的完整流程。
与 [README.md](README.md)（macOS 本机部署）的区别：

- 包管理用 dnf 代替 brew
- 安装 Docker Engine（systemd 常驻服务），无 Docker Desktop
- 仓库通过 git clone 获取，不再挂载本机目录，本机不编辑上传。
- 安装Docker容器时，多了`:Z`，用于应对Rocky独有的SELinux机制。

## 0. 连接阿里云ECS实例
阿里云-云服务器ECS-实例-远程连接-通过VNC连接-root账户登录

## 1. 环境准备

设置阿里云安全组端口：
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
sudo dnf config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/rhel/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
docker version

# 拉取容器 镜像站不可用时更换
docker pull docker.1panel.live/library/mariadb:latest
docker tag docker.1panel.live/library/mariadb:latest mariadb:latest

docker pull docker.1panel.live/mayanghua/instock:latest
docker tag docker.1panel.live/mayanghua/instock:latest mayanghua/instock:latest

docker pull docker.m.daocloud.io/mlikiowa/napcat-docker:latest
docker tag docker.m.daocloud.io/mlikiowa/napcat-docker:latest mlikiowa/napcat-docker:latest

# 拉取Github仓库
sudo dnf install -y git
git clone https://github.com/SekiaGames/Stock $HOME/Stock
# 注：仓库中 run_web.sh 应已带可执行权限。仓库实际位置：/root/Stock
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

## 6. 绑定域名 + nginx 反向代理

```bash
sudo dnf install -y nginx
sudo systemctl enable --now nginx
```

编辑 `/etc/nginx/conf.d/instock.conf`（把 `stock.sekia.games` 换成自己的域名）：

```bash
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
# 之后访问stock.sekia.games
```

### 6.4 申请 HTTPS 证书

```bash
# 国内服务器先切 dnf 源到阿里云镜像，否则默认源（dl.rockylinux.org）极慢/连不上
# 注：部分阿里云 Rocky 镜像的源文件不叫 Rocky-*.repo，故直接用 *.repo 匹配，不命中的文件自动跳过
sudo sed -i.bak \
  -e 's|^mirrorlist=|#mirrorlist=|g' \
  -e 's|^#baseurl=http://dl.rockylinux.org/$contentdir|baseurl=https://mirrors.aliyun.com/rockylinux|g' \
  /etc/yum.repos.d/*.repo
sudo dnf clean all && sudo dnf makecache

# certbot 不在 Rocky 基础源（AppStream/BaseOS）里，需先启用 EPEL 和 CRB
sudo dnf config-manager --set-enabled crb
sudo dnf install -y epel-release
# EPEL 默认源也在国外，同样切到阿里云镜像
sudo sed -e 's|^metalink=|#metalink=|g' \
         -e 's|^#baseurl=https://download.example/pub/epel|baseurl=https://mirrors.aliyun.com/epel|g' \
         -i.bak /etc/yum.repos.d/epel.repo
sudo dnf makecache
sudo dnf install -y certbot python3-certbot-nginx
sudo certbot --nginx -d stock.sekia.games --redirect \
  --register-unsafely-without-email --agree-tos --no-eff-email
sudo nginx -t && sudo systemctl reload nginx
```

certbot 会自动改写配置文件，完成后访问 `https://stock.sekia.games` 即可。
