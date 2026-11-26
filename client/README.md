# Audimate Windows 客户端

需要 x64 Python 3（含 Tkinter）和位数一致的采音 DLL。启动脚本会创建或复用项目的 `build/rtc-env`，在这个环境中安装桌面依赖，再使用同一个解释器启动客户端。可在资源管理器中双击 `client/start.cmd`，也可在仓库根目录的 PowerShell 中运行以下命令。

```powershell
.\client\start.cmd
```

默认使用 `build/Release/audimate_capture.dll`，其他构建可用 `.\client\start.cmd --dll 路径` 指定。使用自选 Python 或 VS Code 时，选择 `build/rtc-env/Scripts/python.exe`；也可以在自选解释器中执行 `python -m pip install -r client/requirements.txt`，再用同一解释器运行 `python -m client`。

启动后，在登录页填写房间服务地址并登录，也可以注册后直接进入房间。桌面端和网页端共用账号。服务地址默认取自环境变量 `AUDIMATE_SERVER`，其次是本机的 `client/server.local.txt`，未配置时使用 `http://127.0.0.1:17881`。[房间服务与网页使用说明](../server/README.md)

- 点击 **「收听」** 即可播放。房间空席时可等候下一位主持。展开「收听设置」可选择 Opus 或优先无损、调整同步间隔、开启自动同步或立即同步。
- 点击 **「开始主持」** 或 **「申请主持」**，在同一窗口按进程名、窗口标题或 PID 选择播放器，包含最小化与后台进程。选择采集音质和上传格式后确认，或点「返回房间」放弃设置。音质支持标准、CD、录音室和自定义采样率、位深。
- 空席时确认后开始采音，房间显示音源、格式和左右声道电平。已有主持时等待对方接受，期间可取消申请。主持可以接受或拒绝申请，也可点击 **「下台」** 结束采音。再次主持时可沿用上次设置或换一个进程。
- 共享网易云音乐时，选择窗口标题带有曲目的 `cloudmusic.exe`。主持端、桌面听众和网页听众会显示「歌曲名 - 歌手名」，切歌后随房间轮询更新。其他播放器可识别「《歌曲名》 - 歌手名」标题。如果窗口没有完整曲目，或所选进程的多个窗口给出不同曲目，房间显示进程名。
- 在同一轮 PCM 主持中，上传线程复用专用 HTTPS 连接，每包约 200 ms。如果某次传输中断，当前包的结果标记为未确认，下一包重新连接。重新连接也失败时，客户端会显示错误并结束本轮共享。

播放偏好和基础传输日志 `transport.log` 位于 `%LOCALAPPDATA%/Audimate/`。排查开发版音频问题时，使用 `.\client\start-dev.cmd`（或 `python -m client.dev`）连接开发服务。开发样本写入同目录下的 `transport-dev.log`，并异步上报运维端。每类日志轮转保留最多三份 256 KiB 文件。常规 `start.cmd` 始终使用基础日志。运维调整级别后，客户端在下一次房间轮询时采用新值。[分段口径和排障方法](../server/README.md#开发版链路计时)

## 测试

以下命令使用隐藏的 Tk 窗口操作真实控件，并短暂播放合成测试音。实机用例使用默认构建目录的 DLL。

```powershell
python -m unittest client.test_client client.test_online -v
```

测试覆盖搜索、刷新、PID 复用、音质校验、取消启动、失败重试和进程切源。在线用例操作同一个主窗口注册、登录、返回选源和收听，捕获真实播放进程，在另一个会话核对左右声道样本，并验证申请取消、拒绝、接受以及自定义音质在交接后生效。Windows 10 的隔离验收仍需在对应系统上运行。[采音模块与隔离测试](../host/capture/README.md)
