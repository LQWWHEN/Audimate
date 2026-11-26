# Audimate 房间服务

Windows 客户端与网页共用一套账号和一间音乐房。注册成功后直接入房，也可使用已有账号从另一端登录。空席时可直接主持，有人时由现任主持接受或拒绝申请。下台、换人和断线超时会使旧音源失效。

## 本机运行

在仓库根目录运行以下命令，开发服务只监听回环地址。

```powershell
New-Item -ItemType Directory -Force build | Out-Null
python -m server.dev --db build/audimate-local.sqlite3
```

打开 <http://127.0.0.1:17881/>。另开终端运行 `python -m client`，点击“连接在线房间”。两端使用同一个服务地址即可联调。

- 桌面主持在主窗口选择进程和音质，然后在在线房间点击主持。
- 网页主持在桌面 Chrome 或 Edge 的房间页选择音质，点击主持，选音乐标签页并勾选共享音频。房间只传输音轨。浏览器采集需要 HTTPS 或本机回环地址。
- 听众点击“收听”。主持更换后界面显示新音源，可重新点击收听。

## Ubuntu 测试机部署

在 Ubuntu 24.04 测试机安装 `gunicorn`、`nginx`、`python3-venv`。将本目录中的 `app.py`、`monitor.py`、`ops_admin.py`、`web/` 和 `deploy/` 放到 `/opt/audimate/`。以下命令以 root 在服务器执行。

```sh
install -m 0644 /opt/audimate/deploy/audimate.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now audimate
curl -fsS http://127.0.0.1:17881/api/health
```

健康接口应返回 `{"service":"audimate","ok":true}`。业务服务只监听回环地址，使用一个多线程 Gunicorn worker，使所有请求访问同一份房间状态。账号和会话数据库为 `/var/lib/audimate/accounts.sqlite3`，可用 SQLite 在线备份。房间、申请和音频块存放在内存中。

公网 HTTPS 使用 Nginx 和 Let's Encrypt 的 IP 证书。将 `AUDIMATE_IP` 设为测试机的真实公网 IPv4，确保 TCP 80、443 可从公网到达，并让 Nginx 通过 80 端口提供 `/var/www/html/.well-known/acme-challenge/`。下列步骤适用于已同意 CA 服务条款的运维者。[Let's Encrypt IP 证书说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)

```sh
python3 -m venv /opt/audimate-certbot
/opt/audimate-certbot/bin/pip install 'certbot>=5.4'
/opt/audimate-certbot/bin/certbot certonly --webroot -w /var/www/html \
  --cert-name audimate --preferred-profile shortlived --ip-address "$AUDIMATE_IP"
sed "s/PUBLIC_IP/$AUDIMATE_IP/g" /opt/audimate/deploy/nginx.conf > /etc/nginx/sites-available/audimate
ln -sfn /etc/nginx/sites-available/audimate /etc/nginx/sites-enabled/audimate
nginx -t && systemctl reload nginx
install -m 0644 /opt/audimate/deploy/audimate-certificate.* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now audimate-certificate.timer
curl -fsS "https://$AUDIMATE_IP/api/health"
```

两端的公网地址均为 `https://<公网IP>`。运维联调也可用 `ssh -N -L 17882:127.0.0.1:17881 <测试机别名>`，然后在本机打开 <http://127.0.0.1:17882/>。此地址经 SSH 访问测试机上的房间与数据库。

## 运维监控

打开 `https://<公网IP>/ops/`，用已授权的账号登录。后台每 2 秒更新一次。运维会话独立于房间会话，有效期 8 小时，打开后台不会占用席位。监控 API 为 `GET /ops/api/overview`，每次读取均检查运维权限。

在服务器的 `/opt/audimate` 目录可创建有随机密码的专用账号，或授予、撤销已有账号的运维权限。以下命令中的用户名按需替换。`create` 仅创建新账号并一次性输出凭据，`revoke` 会立即撤销该账号的全部运维会话。

```sh
python3 ops_admin.py --db /var/lib/audimate/accounts.sqlite3 create ops-alice
python3 ops_admin.py --db /var/lib/audimate/accounts.sqlite3 grant existing-user
python3 ops_admin.py --db /var/lib/audimate/accounts.sqlite3 revoke existing-user
```

| 后台指标 | 统计口径及排障用途 |
| --- | --- |
| 在线连接、正在拉流 | 在线按会话计数，超过 20 秒未联系即过期。近 3 秒向当前音源请求音频的听众计入正在拉流。可结合成员列表区分空闲、等待音频和主持申请。 |
| 入站、出站 | 入站是服务端已接受的 PCM 字节，出站是服务端为各次成功读取返回的 PCM 字节；重复读取再次计入出站。HTTP/TLS 开销和网卡其他流量不计入。每个连接显示其入房后的上传与下载。 |
| 速率与趋势 | 速率取最近 10 个完整秒的平均值。图中每个点覆盖 5 秒，可选 1、5、10 分钟。空房时曲线自然回落。 |
| 音源与缓冲 | 可以查看主持、采样率、位深、当前流的最新序号和缓冲占用。有主持且超过 3 秒未收到音频时提示上行无新包。 |
| 空读与游标跳过 | 空读表示有效的拉流请求暂时没有新块。游标跳过块数按有序号的读取请求累计，常见原因是听众读取较慢，旧块已离开服务器缓冲。 |
| 排障事件 | 最近 100 条连接、超时、上下台、申请、交接和房间接口错误。相邻的同类异常在 5 秒内合并并保留次数。 |

累计值从当前工作进程启动时开始，趋势和事件保存在有界内存中，重启后重新统计。后台只保留调试所需的统计与房间状态。端到端播放成功率、音量及云厂商计费流量需要在对应端另行测量。

## 接口与传输

| 接口 | 用途 |
| --- | --- |
| `POST /api/register`、`POST /api/login` | 发送 `name`、`password`，成功后设置会话 cookie |
| `GET /api/me`、`POST /api/logout` | 查询身份、撤销当前会话 |
| `GET /api/room` | 读主持、成员数、自己的申请状态及现任主持可处理的申请 |
| `POST /api/stage/request` | 发送 `source`、`rate`、`bits`，空席时立即获准 |
| `POST /api/stage/{accept,reject,leave}` | 审批携带申请 ID 与当前流 ID，下台携带流 ID |
| `POST /api/audio?stream=…` | 当前主持发送有符号小端交错立体声 PCM |
| `GET /api/audio?stream=…&after=…` | 按序号取音频块，序号见 `X-Sequence`。初次使用 `-1` 取最新块，暂时无新块返回 204 |

密码使用带独立随机盐的 PBKDF2-HMAC-SHA256。会话有效期为 7 天，数据库保存令牌摘要。最多 19 个活跃会话，20 秒未联系的席位在下次房间请求时释放。音频按约 200 毫秒分块，服务器缓存当前流最近 12 块。48 kHz / 16-bit 双声道 PCM 每名听众约占 1.54 Mbps 出口，96 kHz / 24-bit 约占 4.61 Mbps，另有协议开销。

## 验证

在仓库根目录运行。桌面用例需要 Windows、Tk 和已构建的 x64 采音 DLL。浏览器用例需要 Node.js、Playwright 和 Chromium，可用 `CHROME_BIN` 和 `AUDIMATE_PYTHON` 指定可执行文件。

```powershell
python -m unittest server.test_app client.test_online client.test_client -v
node server/test_browser.cjs
node server/test_ops_browser.cjs
```

网页用例用合成音轨代替系统标签页选择框，实际执行 AudioWorklet、HTTP 上传、另一个浏览器会话的解码和播放调度。设置 `AUDIMATE_BROWSER_PEER=1` 时，桌面测试还会启动 Chrome，用桌面端刚注册的账号登录网页，并核对实际送入浏览器播放的左右声道。设置 `AUDIMATE_TEST_URL` 可在指定的空闲测试房间运行这两个端到端用例，每次生成独立的随机测试账号与密码。系统选源、跨公网延迟和满房容量需单独验收。
