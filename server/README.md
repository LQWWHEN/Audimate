# Audimate 房间服务

Windows 客户端与网页共用一套账号和一间音乐房。注册成功后直接入房，也可使用已有账号从另一端登录。空席时可直接主持，有人时由现任主持接受或拒绝申请。下台、换人和断线超时会使旧音源失效。

## 本机运行

在仓库根目录运行以下命令，开发服务只监听回环地址。

```powershell
New-Item -ItemType Directory -Force build | Out-Null
python -m pip install wasmtime==39.0.0
python -m server.dev --db build/audimate-local.sqlite3
```

打开 <http://127.0.0.1:17881/>。在仓库根目录另开终端运行 `.\client\start.cmd` 启动 Windows 客户端，在登录页使用同一个服务地址即可联调。

- 桌面端入房后点击「开始主持」或「申请主持」，选择进程、采集音质和上传格式，再确认上台或提交申请。
- 网页主持在桌面 Chrome 或 Edge 中打开 HTTPS 房间页（本机可用回环地址），选择音质后点击「主持 · 共享音频」。选播放器窗口或音乐标签页，然后点击「共享」。网页请求预选音频，浏览器若未预选，可手动勾选。选择器只提供系统混音或没有音频选项时，Windows 客户端可按进程共享。
- 房间里的「下台」会停止采音和上传。浏览器单独管理共享浮窗，如有「隐藏」按钮可手动收起；选择浏览器的「停止共享」也会结束本轮主持。[W3C 屏幕采集规范](https://w3c.github.io/mediacapture-screen-share/)
- 已最小化或收进托盘的播放器可能不会出现在浏览器的「窗口」列表。先还原播放器再选择，共享开始后可将播放器最小化。下台会结束采集，下次上台需再次选择。需要在窗口始终隐藏时开始主持，可使用 Windows 客户端的「共享进程」。[W3C 屏幕采集规范，第 5.2 节](https://www.w3.org/TR/screen-capture/)
- 听众点击“收听”。主持更换后界面显示新音源，会自动接入下一轮主持。

## PCM 的 FLAC 传输

FLAC 传输复用原有 HTTPS 分块。主持设置随申请保留到获准上台，再随主持轮次发布。PCM 收听端和回退通道按本轮格式拉取音频，每个 FLAC 包都是可单独解码的完整流。服务端校验解码后的采样率、位深、声道和样本数，在同一个缓存条目保留压缩原包和供共享 Opus 桥接使用的 PCM。按实际帧数推进时间线，按线上字节数统计流量。旧版本客户端请求压缩轮次的 PCM 数据会收到 HTTP 415，可更新客户端或改用 Opus。构建编解码模块的说明见[FLAC 编解码模块](../codec/README.md)。

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
| 在线连接、正在拉流 | 在线按房间会话计数，超过当前「成员离线超时」未联系即释放席位，默认 20 秒。近 7 秒请求缓存或上报播放样本的听众计入正在拉流。可结合成员列表区分空闲、等待音频和主持申请。 |
| 音频总入站、总出站 | 每个方向合计已传输的 PCM HTTP 音频字节和 LiveKit RTP 字节，包含同机转码桥接。PCM 重复拉取也计入出站。成员表仍按会话统计 PCM。公网网卡和 HTTP/TLS 开销属于不同的计量口径。 |
| 速率与趋势 | 顶部为同一时间窗内的 10 秒均速。图中每点覆盖 5 秒，可查看最近 1、5、10 分钟。方向和格式可交叉筛选四条线，例如只看 Opus 出站。筛选仅影响趋势。 |
| 音源与缓冲 | 可以查看主持、采样率、位深、当前流的最新序号和缓冲占用。有主持且超过 3 秒未收到音频时提示上行无新包。 |
| 空读与游标跳过 | 空读表示有效的拉流请求暂时没有新块。游标跳过块数按有序号的读取请求累计，常见原因是听众读取较慢，旧块已离开服务器缓冲。 |
| 排障事件 | 最近 100 条连接、超时、上下台、申请、交接和房间接口错误。相邻的同类异常在 5 秒内合并并保留次数。 |

累计值从当前工作进程启动时开始，趋势和事件保存在有界内存中，重启后重新统计。后台只保留调试所需的统计与房间状态。端到端播放成功率、音量及云厂商计费流量需要在对应端另行测量。

服务进程在后台约每 2 秒读取一次 SFU 计数，首次成功读取作为本次累计的基线，相邻读数的增量按时间分摊。SFU 指标中断或重启时，Opus 曲线保留缺口，缺测期间总量栏提示可用的 PCM 计数。未启用 LiveKit 的服务按纯 PCM 展示。

### 控制与诊断

在「房间服务」点击「应用配置」即可在线修改历史秒数、音频字节预算、块数上限、校准档位、同步默认阈值和上限、成员离线超时。全局值与本房阈值覆盖写入 SQLite；清空本房输入可恢复继承。全站上限仍约束房间设置。保存按版本校验，审计记录作用域、版本与差异；重复保存同值不会重置播放器。维护会清场并撤销普通登录，重启和开放房间是独立操作。

成员超时范围 5–300 秒，默认 20 秒，收紧时在线成员至少还有一个新周期上报。轮询、拉取和上传均刷新活动时间。服务器在请求或约每 2 秒的巡检时释放过期席位。

房间响应包含稳定的 `room_id` 和 `sync_policy`。主持申请的可选 `sync_request_ms` 为毫秒整数，空值跟随房间；当轮主持可向 `POST /api/stage/sync` 提交当前流 ID 和新值。建议值初始 1000 ms，不参与阈值裁剪。响应中的 `effective_ms` 由主持选择或房间默认值与各级上限取小得到。调整只影响自动校准。缓存缩小时立即淘汰最旧块；监控显示实际样本时长、负载字节与最近淘汰原因。

诊断级别可随时切换为完整、仅异常或关闭。SQLite 中保留最近 10 分钟内最多 600 条客户端样本，工作进程重启后可继续排查。后台页面显示最近 60 条。PCM 客户端使用三次服务器对时中 RTT 最短的一次，估计从采集块起点到提交播放的时间。RTCP 时差估计仅在有发送时钟映射时给出。网络 RTT、抖动与接收缓冲单独显示，缺测显示空值。声卡、扬声器的实际出声时间需要外部回录。SFU 通过回环 Prometheus 端点读取 RTP 入出字节（包含桥接），PCM HTTP 流量单独计量。关闭诊断保留基础计数和运维审计。

### 开发版链路计时

本地 `python -m server.dev --db build/audimate-local.sqlite3` 和测试站的 Gunicorn 工厂 `app:create_dev_app()` 提供链路计时。网页随所连接服务选择版本。Windows 使用 `.\client\start-dev.cmd` 接入开发服务。正式部署使用仓库自带的 systemd 单元及 `app:create_app()`，桌面使用 `.\client\start.cmd`。日志级别只控制当前版本的输出范围。

测试站可在维护期间把 `deploy/development-timing.conf` 安装到 `/etc/systemd/system/audimate.service.d/development-timing.conf`，执行 `systemctl daemon-reload` 和 `systemctl restart audimate` 后生效。正式站使用基础单元。

在开发服务登录 `/ops/`，进入「传输时间线 → 开发版链路计时」。展开耗时分布可查看当前主持轮次的样本数、P50、P95 和最大值。空席时显示最近有样本的一轮。输入源包号或缓存包号可对照主持、入库、收听三处的记录。源包号在主持端组包时分配，缓存包号由服务端实际入库后分配，两者可能不同。

「播放延迟构成」使用当前主持轮次的采集至预计播放时间作为 100%。默认汇总同一路径最近 30 秒，窗口可选 10、30、60 秒。运维浏览器每 5 秒只计算当前选中的视图，切换立即重算。路径汇总先取每位听众的平均值，再给每位听众相同权重。指定听众按连接区分，包括同一账号的多台设备。最慢听众按每位听众当前路径的最新有效端到端延迟选出，再展示该听众在窗口内同一路径的构成。

组包和待发队列按同一轮次、源包号和缓存包号关联。上传估算扣除服务入库耗时，ACK 往返和缓存位置等重叠指标保留在明细中。饼图单列缓存驻留、下行、解码、排播等待及未归因时间。缺少可靠端到端样本时显示提示及可用的 RTT、抖动、接收缓冲；超过 2 ms 取整容差的总量冲突单独计数。正式服务不提供饼图模块。

| 路径 | 记录内容 |
| --- | --- |
| PCM 主持 | Windows 末帧采集读取、组包跨度、待发队列、连接复用、建连（含 TLS）、写请求、等响应头、读响应体及上传总耗时。网页记录音频块时长、音频线程交付、排队、Fetch 发起至收头及读响应体。 |
| PCM 服务 | 从应用收到请求至入库、读取请求体、等待房间锁、追加缓存、包在缓存中的驻留、处理拉取请求。Nginx 和网络套接字在 WSGI 计时边界之外。 |
| PCM 收听 | 拉取、连续空读等待、网页解码、待排播时长、错过播放时刻的时长或桌面写播放设备耗时。跨端的采集至入库、上传开始至入库、响应就绪至收齐和采集至预计播放均依赖对时。 |
| LiveKit | 凭据、加入房间、发布或订阅、可见的格式转换与 SDK 交帧、设备写入，以及 SDK 提供的 RTP 排队、RTT、抖动、掩蔽样本和接收缓冲。跨格式桥接按缓存包号关联。SFU 内部转发和终端扬声器出声时间需要独立测量。 |

排查桌面 `timed out` 时，先看 `error` 的「阶段」「超时」「当前阶段耗时」。例如「等响应头」表示客户端已经写完请求。再用源包号查入库；即使该包的确认丢失，服务端也会尝试在最近 30 秒、最多 128 个包的接收记录中补上缓存包号。查不到入库记录时，仍可能是采样、保留期限或网络中断造成。客户端能在下台后短时间内补报本轮故障。

完整模式按约每 10 个 PCM 包取一个常规样本，慢上传、欠载、同步与错误另外限频上报。仅异常模式保留故障，关闭模式停止传输样本。开发明细在内存中即时可见，每约 2 秒异步批量写入独立的 SQLite 表 `dev_transport_log`，保留最近 10 分钟、最多 600 条。拥塞及异常退出可能造成样本缺失。分位数以实际保留的样本计算。阶段存在重叠，例如等响应头可以包含服务端入库时间，不能把各列或各自的 P95 相加作为端到端延迟。

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

密码使用带独立随机盐的 PBKDF2-HMAC-SHA256。登录有效期为 7 天，数据库保存令牌摘要。最多 19 个活跃房间会话，按运维配置的离线超时释放席位。音频按约 200 毫秒分块，服务器按样本数、包龄、字节预算及块数限制缓存，新库默认 15 秒。首次或手动同步用 `sync=1` 获取距最新样本约 600 ms 的块，历史越界会返回 `X-Reset`，停流时旧包按时失效。48 kHz / 16-bit 双声道 PCM 每名听众约占 1.54 Mbps 出口，96 kHz / 24-bit 约占 4.61 Mbps，另有协议开销。

### 歌曲识别

上台时勾选「歌曲识别」后，服务端从该轮主持的 PCM、FLAC 或 Opus 解码历史取样，在后台用 ShazamIO 生成并提交指纹。历史上限至少为 12 秒才提供开关。样本实际可用长度同时受字节和块数预算限制。运维可在线调整 `recognition_interval_s`（默认 10 秒），识别任务按新值安排下一轮。缓存降至门槛以下会暂停本轮识别，调回后从新样本重新开始。

播放器有标题时先取 5 秒音频并在末尾补静音至 12 秒，曲名与歌手匹配即可补充资料。无标题时使用完整窗口，同一录音的再次确认至少包含 10 秒新音频。检查超时保留现有曲目和候选。一次请求上限为 8 秒，每房最多一个在途请求，开发运维事件记录查询类型、结果和耗时。

开发版运维页的「排障事件 → 识别」显示 Shazam 实际返回的歌名、歌手、录音 ID，以及查询返回的专辑和发行时间。每次命中标明候选、已确认或与播放器标题不符。超时和失败也可在「异常」筛选中查看，包含异常类型、最后观测到的阶段及该阶段耗时、HTTP 尝试次数与各次响应状态。「未收到响应头」表示截至查询结束仍未观测到响应头。日志级别「仅异常」只新增失败和超时事件，「关闭」停止记录并清空事件。

真实识别验收使用 ShazamIO 仓库 `examples/data/Gloria.ogg`，将其前 28 秒转换为双声道 PCM WAV，设置 `AUDIMATE_RECOGNITION_WAV` 为文件绝对路径后运行 `python -m unittest client.test_recognition_live -v`。用例需要 Windows、Chrome、Playwright 和服务端识别依赖。可在隔离的本地 LiveKit 环境设置 `AUDIMATE_RECOGNITION_RTC=1` 验证 Opus 主持。

## 验证

在仓库根目录运行。桌面用例需要 Windows、Tk 和已构建的 x64 采音 DLL。浏览器用例需要 Node.js、Playwright 和 Chromium，可用 `CHROME_BIN` 和 `AUDIMATE_PYTHON` 指定可执行文件。

```powershell
python -m unittest server.test_app server.test_runtime server.test_timing server.test_traffic client.test_online client.test_client -v
node server/test_latency.cjs
node server/test_browser.cjs
node server/test_browser.cjs --release
node server/test_ops_browser.cjs
# 指定已配置媒体的空闲测试服务 AUDIMATE_TEST_URL，或指定本机 SFU 可执行文件 LIVEKIT_BIN
node server/test_browser.cjs --livekit
```

在已配置 LiveKit 的测试机加载测试 SFU 环境后，可运行 `python -m server.test_bridge_timing`。该用例创建两个临时媒体房间，验证双向桥接的实际音频和分段计时，独立于业务房间的维护状态。

网页用例默认用合成音轨模拟浏览器来源选择，实际执行上传、解码和播放。Windows 开发机可运行 `node server/test_browser.cjs --native-window`，需要桌面会话、支持应用音频的 Chrome、Python Tk 和可用的音频输出。目标窗口在左声道播放 700 Hz、右声道播放 14 kHz，另一个进程播放 1800 Hz 干扰音。用例在 PCM 听众端检查左右电平、串音、干扰和目标暂停后的静音，再切换到标签页主持。加上 `--livekit` 还会在 Opus 解码后检查声道分离和电平。

设置 `AUDIMATE_BROWSER_PEER=1` 时，桌面测试还会启动 Chrome，用桌面端刚注册的账号登录网页，并核对实际送入浏览器播放的左右声道。设置 `AUDIMATE_TEST_URL` 可在指定的空闲测试房间运行端到端用例，每次生成独立的随机测试账号与密码。在 Windows 开发机上也可针对已部署的 Linux SFU 运行。此机使用 Windows SFU 与原生媒体桥接同机启动时出现 ICE 超时，公网 Linux 测试路径已完成互通。真实音乐网站选源、跨网络声学延迟和满房容量需单独验收。
