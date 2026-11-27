# Audimate Windows 客户端

新版播放器使用 PySide6、Qt Quick/QML 和 NumPy，已在 Windows x64 Python 3.13 验证。启动脚本会创建或复用项目的 `build/rtc-env`。双击 `client/start.cmd` 或在仓库根目录运行下方命令。

```powershell
.\client\start.cmd
```

在登录页点击 **「试听可视化」**，或运行 `.\client\start.cmd --demo`，可播放内置的立体声合成乐段，直接比较三种预设。试听在本机运行。初版 Tk 界面可通过 `--legacy` 打开。

Qt 播放器内置 Noto Sans CJK SC 2.004 的常规和粗体字重，供简繁中文、日文、韩文和拉丁文字混排。其他文字由 Qt 选择可用的系统字体。横屏两栏围绕唱片中心分成上下两排，竖屏沿用相同分组和向内对齐方式。[字体发行版](https://github.com/notofonts/noto-cjk/releases/tag/Sans2.004) · [随附许可](fonts/OFL.txt)

**窗口与贴边。** 标题栏右侧的小窗口按钮切换横屏和竖屏，旁边是最小化、最大化／还原、关闭。拖动标题栏可移动窗口，拖边或角可调整尺寸。横屏基础下限为 960×560，竖屏为 420×600，单位为逻辑像素。字号跟随系统显示缩放；改变窗口大小时先调整圆环和留白，歌名最多两行，截断文本可悬停查看。提示与主持审批同时出现时，布局会按内容提高最小高度。主界面无滚动条。

竖屏可吸附到当前显示器工作区的上、左、右边缘。窗口失焦且鼠标移出约 1.4 秒后收为 6 像素边条；悬停边条可展开并继续使用原来有焦点的应用。拖离边缘即可恢复自由放置。在房间主持名称下方点预设按钮，直接选择跟随主持或指定风格。

默认使用 `build/Release/audimate_capture.dll`，其他构建可用 `.\client\start.cmd --dll 路径` 指定。使用自选 Python 或 VS Code 时，选择 `build/rtc-env/Scripts/python.exe`；也可以在自选解释器中执行 `python -m pip install -r client/requirements.txt`，再用同一解释器运行 `python -m client`。

启动后，在登录页填写房间服务地址并登录，也可以注册后直接进入房间。桌面端和网页端共用账号。服务地址默认取自环境变量 `AUDIMATE_SERVER`，其次是本机的 `client/server.local.txt`，未配置时使用 `http://127.0.0.1:17881`。[房间服务与网页使用说明](../server/README.md)

- 入房自动收听，空房会等候下一位主持。主动停止后可点「开始收听」恢复。音质信息下方的「立即同步」可重新接入当前直播。
- 标题栏窗口按钮旁的「本地设置」在登录前、试听及房间内均可打开。音画页可拖动滑杆试听 0–200 ms 的本机播放延迟，默认 50 ms，取消会恢复原值。校准用于 Audimate 播放的声音，主持听到的外部播放器原声由播放器控制。收听页设置优先接收格式、自动同步和间隔。设置保存在 `%LOCALAPPDATA%/Audimate/desktop.json`，首次使用沿用已有的收听偏好。
- 在圆环旁的预设菜单选择重型音乐、流行摇滚或轻音乐。默认跟随主持，听众的手动覆盖持续到离房。主持在菜单内切换会更新房间预设，需配套服务端支持。旧版主持默认使用流行摇滚。
- 点击 **「开始主持」** 或 **「申请接替」**，按进程名、窗口标题或 PID 找到播放器，再点该行的「共享」或「申请接替」。选择列表包含最小化与后台进程。采集格式、识曲、频谱风格和同步阈值从本地主持默认值带入，展开「本次参数」可单独调整这一轮。
- 空席时确认后开始采音，房间显示音源、格式和左右声道电平。已有主持时等待对方接受，期间可取消申请。主持可以接受或拒绝申请，也可点击 **「下台」** 结束采音。再次主持时可沿用上次设置或换一个进程。
- 共享网易云音乐时，选择窗口标题带有曲目的 `cloudmusic.exe`。其他播放器可识别「《歌曲名》 - 歌手名」标题。开启识曲后可补充专辑和封面。无标题时，「耳朵一动」显示首次候选；确认后主卡更新。圆环按封面取色，未取得封面时显示冰蓝色唱片图标。
- 上传格式中的 **「无损 PCM · FLAC 压缩」** 会沿用所选采样率和位深。无损听众按每轮主持公布的格式自动解压。选择 Opus 的听众共用一条转码音轨。
- 在同一轮 PCM 主持中，上传线程复用专用 HTTPS 连接，每包约 200 ms。如果某次传输中断，当前包的结果标记为未确认，下一包重新连接。重新连接也失败时，客户端会显示错误并结束本轮共享。

主持设置和主持中均可输入本轮同步阈值（秒），清空即跟随房间；1.0 秒仅为建议。自动模式观察持续超限和同步效果，手动模式可随时刷新。

播放偏好和基础传输日志 `transport.log` 位于 `%LOCALAPPDATA%/Audimate/`。排查开发版音频问题时，使用 `.\client\start-dev.cmd`（或 `python -m client.dev`）连接开发服务。开发样本写入同目录下的 `transport-dev.log`，并异步上报运维端。每类日志轮转保留最多三份 256 KiB 文件。常规 `start.cmd` 始终使用基础日志。运维调整级别后，客户端在下一次房间轮询时采用新值。[分段口径和排障方法](../server/README.md#开发版链路计时)

## 测试

### Windows x64 单文件测试版

测试者可以直接双击 `Audimate-Windows-x64.exe`，无需单独安装 Python、Qt 或采音 DLL。首次打开会将运行库解包到当前用户的临时目录。登录页的「试听可视化」可离线检查出声、频谱、窗口缩放和竖屏贴边。在「服务器地址」可查看或更换联机测试地址。反馈兼容性问题时，请附 Windows 版本与内部版本号（`winver`）、显示缩放比例、音频输出设备，以及出现问题的操作步骤。

开发者在 Windows x64 上先按[采音模块构建说明](../host/capture/README.md)生成 `build/Release/audimate_capture.dll`，然后在仓库根目录使用独立环境打包。以下示例使用 Python 3.12 x64。

```powershell
py -3.12 -m venv build/win-pack-env
.\build\win-pack-env\Scripts\python.exe -m pip install -r client/requirements.txt PySide6-Essentials==6.12.0 PyInstaller==6.22.3
.\build\win-pack-env\Scripts\python.exe client/package_windows.py
```

输出为 `build/distribution/Audimate-Windows-x64.exe`。指定默认测试站时，在打包命令末尾添加 `--server https://你的测试域名`。省略该参数时默认连接本机服务。

### 源码回归

以下命令覆盖 Qt 原型、旧版 Tk 控件与服务端预设授权，在本机临时房间短暂播放合成测试音。实机用例使用默认构建目录的 DLL。

```powershell
.\build\rtc-env\Scripts\python.exe -m unittest client.test_player client.test_client client.test_online server.test_app -v
```

测试覆盖搜索、刷新、PID 复用、音质校验、取消启动、失败重试和进程切源。在线用例操作同一个主窗口注册、登录、返回选源和收听，捕获真实播放进程，在另一个会话核对左右声道样本，并验证申请取消、拒绝、接受以及自定义音质在交接后生效。Windows 10 的隔离验收仍需在对应系统上运行。[采音模块与隔离测试](../host/capture/README.md)
