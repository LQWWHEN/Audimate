# Audimate 房间服务

Windows 客户端与网页共用一套账号和一间音乐房。注册成功后直接入房，也可使用已有账号从另一端登录。空席时可直接主持，有人时由现任主持接受或拒绝申请。下台、换人和断线超时会使旧音源失效。

## 本机运行

在仓库根目录运行以下命令，开发服务只监听回环地址。

```powershell
New-Item -ItemType Directory -Force build | Out-Null
python -m server.dev --db build/audimate-local.sqlite3
```

打开 <http://127.0.0.1:17881/>。在仓库根目录另开终端运行 `.\client\start.cmd` 启动 Windows 客户端，在登录页使用同一个服务地址即可联调。

- 桌面端入房后点击「开始主持」或「申请主持」，选择进程、采集音质和上传格式，再确认上台或提交申请。
- 网页主持在桌面 Chrome 或 Edge 中打开 HTTPS 房间页（本机可用回环地址），选择音质后点击「主持 · 共享音频」。桌面播放器选「窗口」并勾选共享应用音频，网页播放器选正在播放的标签页并勾选共享标签页音频。窗口共享取决于浏览器与系统能否提供应用独立音轨；选择器只提供系统音频或没有音频选项时，Windows 客户端可按进程共享。房间只传输音轨。
- 已最小化或收进托盘的播放器可能不会出现在浏览器的「窗口」列表。先还原播放器再选择，共享开始后可将播放器最小化。下台会结束采集，下次上台需再次选择。需要在窗口始终隐藏时开始主持，可使用 Windows 客户端的「共享进程」。[W3C 屏幕采集规范，第 5.2 节](https://www.w3.org/TR/screen-capture/)
- 听众点击“收听”。主持更换后界面显示新音源，会自动接入下一轮主持。

## Ubuntu 测试机部署

在 Ubuntu 24.04 测试机安装 `gunicorn`、`nginx`、`python3-venv`。将本目录的 Python 模块、`requirements.txt`、`web/` 和 `deploy/` 放到 `/opt/audimate/`。构建 SFU 可在独立开发机完成，Go 1.27.4 和 Python 3.12+ 已用于此版本。以下命令以 root 在服务器执行。

```sh
# 在构建机运行，将输出复制到测试机 /usr/local/bin/audimate-livekit-server 并 chmod 0755
python3 server/deploy/build_sfu.py --goos linux --output audimate-livekit-server
# 在服务器运行，AUDIMATE_IP 为本机公网地址
python3 -m venv /opt/audimate-rtc-env
/opt/audimate-rtc-env/bin/pip install -r /opt/audimate/requirements.txt
python3 /opt/audimate/deploy/configure_livekit.py "$AUDIMATE_IP"
install -m 0644 /opt/audimate/deploy/audimate.service /opt/audimate/deploy/audimate-livekit.service /opt/audimate/deploy/audimate-media-restart.* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now audimate-livekit audimate-media-restart.path audimate
curl -fsS http://127.0.0.1:17881/api/health
```

健康接口应返回 `{"service":"audimate","ok":true}`。业务服务只监听回环地址，使用一个多线程 Gunicorn worker，使所有请求访问同一份房间状态。账号和会话数据库为 `/var/lib/audimate/accounts.sqlite3`，可用 SQLite 在线备份。房间、申请和音频块存放在内存中。

公网 HTTPS 使用 Nginx 和 Let's Encrypt 的 IP 证书。将 `AUDIMATE_IP` 设为测试机的真实公网 IPv4，确保 TCP 80、443 可从公网到达，开放媒体 TCP 7881、UDP 7882，并让 Nginx 通过 80 端口提供 `/var/www/html/.well-known/acme-challenge/`。下列步骤适用于已同意 CA 服务条款的运维者。[Let's Encrypt IP 证书说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)

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
| 在线连接、正在拉流 | 在线按会话计数，超过 20 秒未联系即过期。近 7 秒请求缓存或上报播放样本的听众计入正在拉流。可结合成员列表区分空闲、等待音频和主持申请。 |
| 入站、出站 | 入站是服务端已接受的 PCM 字节，出站是服务端为各次成功读取返回的 PCM 字节；重复读取再次计入出站。HTTP/TLS 开销和网卡其他流量不计入。每个连接显示其入房后的上传与下载。 |
| 速率与趋势 | 速率取最近 10 个完整秒的平均值。图中每个点覆盖 5 秒，可选 1、5、10 分钟。空房时曲线自然回落。 |
| 音源与缓冲 | 可以查看主持、采样率、位深、当前流的最新序号和缓冲占用。有主持且超过 3 秒未收到音频时提示上行无新包。 |
| 空读与游标跳过 | 空读表示有效的拉流请求暂时没有新块。游标跳过块数按有序号的读取请求累计，常见原因是听众读取较慢，旧块已离开服务器缓冲。 |
| 排障事件 | 最近 100 条连接、超时、上下台、申请、交接和房间接口错误。相邻的同类异常在 5 秒内合并并保留次数。 |

累计值从当前工作进程启动时开始，趋势和事件保存在有界内存中，重启后重新统计。后台只保留调试所需的统计与房间状态。端到端播放成功率、音量及云厂商计费流量需要在对应端另行测量。

### 控制与诊断

在后台可搜索普通账号、停用或启用、重置密码、结束全部会话。进入维护会清除在场者并撤销普通登录，运维登录继续有效。在维护中暂存历史时长和同步档位，点击重启后再手动开放。房间状态重建，systemd 单独重启媒体进程。SQLite 保留维护状态、配置及最近 500 次运维操作。

诊断级别可随时切换为完整、仅异常或关闭。SQLite 中保留最近 10 分钟内最多 600 条客户端样本，工作进程重启后可继续排查。后台页面显示最近 60 条。PCM 客户端使用三次服务器对时中 RTT 最短的一次，估计从采集块起点到提交播放的时间。RTCP 时差估计仅在有发送时钟映射时给出。网络 RTT、抖动与接收缓冲单独显示，缺测显示空值。声卡、扬声器的实际出声时间需要外部回录。SFU 通过回环 Prometheus 端点读取 RTP 入出字节（包含桥接），PCM HTTP 流量单独计量。关闭诊断保留基础计数和运维审计。

## 接口与传输

浏览器 SDK 2.25.0 由同源静态资源提供，授权见 `web/vendor/livekit-LICENSE.txt`。服务器使用 LiveKit v1.13.11。原生 SDK 用轨道名 `audimate-stereo` 声明双声道。构建脚本对该轨道补齐上游 SFU 的立体声标记，确保只上传一条音轨。每轮主持拥有独立媒体房间。业务端签发有效期 45 秒的入房 JWT。Nginx 将公网信令转到当前会话授权检查，SFU 管理 API 和指标仅监听回环地址。

主持可上传 Opus 或原始 PCM。无损源保留选定精度，需要节省流量的听众共享一个服务器 Opus 编码器。有损源的缓存是解码后的 48 kHz/16-bit PCM，界面会标明来源。WebRTC 收听失败时可以从同轮服务端历史继续收听。无 LiveKit 配置的单机开发环境可使用 PCM 演练业务流程。

| 接口 | 用途 |
| --- | --- |
| `POST /api/register`、`POST /api/login` | 发送 `name`、`password`，成功后设置会话 cookie |
| `GET /api/me`、`POST /api/logout` | 查询身份、撤销当前会话 |
| `GET /api/room` | 读主持、成员数、自己的申请状态及现任主持可处理的申请 |
| `POST /api/stage/request` | 发送 `source`、`rate`、`bits`、`codec`，Opus 码率可选 64、128、256 kbps |
| `POST /api/stage/{accept,reject,leave}` | 审批携带申请 ID 与当前流 ID，下台携带流 ID |
| `POST /api/media` | 为当前流的发布或收听签发限权短期入场令牌 |
| `GET /api/clock`、`POST /api/diagnostics` | 服务端对时及有限字段的调试采样 |
| `POST /api/audio?stream=…` | 当前主持发送有符号小端交错立体声 PCM |
| `GET /api/audio?stream=…&after=…` | 按序号取音频块，序号见 `X-Sequence`。初次使用 `-1` 取最新块，暂时无新块返回 204 |

密码使用带独立随机盐的 PBKDF2-HMAC-SHA256。会话有效期为 7 天，数据库保存令牌摘要。最多 19 个活跃会话，20 秒未联系的席位在下次房间请求时释放。音频按约 200 毫秒分块，服务器按样本数和包龄同时限制缓存，默认 5 秒。首次或手动同步用 `sync=1` 获取距最新样本约 600 ms 的块，历史越界会返回 `X-Reset`，停流时旧包按时失效。48 kHz / 16-bit 双声道 PCM 每名听众约占 1.54 Mbps 出口，96 kHz / 24-bit 约占 4.61 Mbps，另有协议开销。

## 验证

在仓库根目录运行。桌面用例需要 Windows、Tk 和已构建的 x64 采音 DLL。浏览器用例需要 Node.js、Playwright 和 Chromium，可用 `CHROME_BIN` 和 `AUDIMATE_PYTHON` 指定可执行文件。

```powershell
python -m unittest server.test_app server.test_runtime client.test_online client.test_client -v
node server/test_browser.cjs
node server/test_ops_browser.cjs
# 指定已配置媒体的空闲测试服务 AUDIMATE_TEST_URL，或指定本机 SFU 可执行文件 LIVEKIT_BIN
node server/test_browser.cjs --livekit
```

网页用例默认用合成音轨模拟浏览器来源选择，实际执行上传、解码和播放。Windows 开发机可运行 `node server/test_browser.cjs --native-window`，需要桌面会话、支持应用音频的 Chrome、Python Tk 和可用的音频输出。该用例让两个独立进程播放 700 Hz 和 1800 Hz 测试音，通过原生选择器选择前者，在另一个浏览器会话中检查目标音、干扰音和暂停后的静音，再切换到标签页主持。

设置 `AUDIMATE_BROWSER_PEER=1` 时，桌面测试还会启动 Chrome，用桌面端刚注册的账号登录网页，并核对实际送入浏览器播放的左右声道。设置 `AUDIMATE_TEST_URL` 可在指定的空闲测试房间运行端到端用例，每次生成独立的随机测试账号与密码。在 Windows 开发机上也可针对已部署的 Linux SFU 运行。此机使用 Windows SFU 与原生媒体桥接同机启动时出现 ICE 超时，公网 Linux 测试路径已完成互通。真实音乐网站选源、跨网络声学延迟和满房容量需单独验收。
