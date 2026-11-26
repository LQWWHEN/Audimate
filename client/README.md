# Audimate Windows 客户端

在 Windows 上从仓库根目录运行。需要 x64 Python 3（含 Tkinter）和位数一致的采音 DLL。本机默认构建位于 `build/Release/audimate_capture.dll`；其他构建目录可通过 `--dll` 指定。

```powershell
python -m pip install -r client/requirements.txt
python -m client
python -m client --dll build/msvc-x64/Release/audimate_capture.dll
```

启动后，在登录页填写房间服务地址并登录，也可以注册后直接进入房间。桌面端和网页端共用账号。服务地址默认取自环境变量 `AUDIMATE_SERVER`，其次是本机的 `client/server.local.txt`，未配置时使用 `http://127.0.0.1:17881`。[房间服务与网页使用说明](../server/README.md)

- 点击 **「收听」** 即可播放。房间空席时可等候下一位主持。展开「收听设置」可选择 Opus 或优先无损、调整同步间隔、开启自动同步或立即同步。
- 点击 **「开始主持」** 或 **「申请主持」**，在同一窗口按进程名、窗口标题或 PID 选择播放器，包含最小化与后台进程。选择采集音质和上传格式后确认，或点「返回房间」放弃设置。音质支持标准、CD、录音室和自定义采样率、位深。
- 空席时确认后开始采音，房间显示音源、格式和左右声道电平。已有主持时等待对方接受，期间可取消申请。主持可以接受或拒绝申请，也可点击 **「下台」** 结束采音。再次主持时可沿用上次设置或换一个进程。

播放偏好和最多三份 256 KiB 的传输日志位于 `%LOCALAPPDATA%/Audimate/`。运维调整诊断级别后，已连接的客户端在下一次房间轮询时采用新值。

## 测试

以下命令使用隐藏的 Tk 窗口操作真实控件，并短暂播放合成测试音。实机用例使用默认构建目录的 DLL。

```powershell
python -m unittest client.test_client client.test_online -v
```

测试覆盖搜索、刷新、PID 复用、音质校验、取消启动、失败重试和进程切源。在线用例操作同一个主窗口注册、登录、返回选源和收听，捕获真实播放进程，在另一个会话核对左右声道样本，并验证申请取消、拒绝、接受以及自定义音质在交接后生效。Windows 10 的隔离验收仍需在对应系统上运行。[采音模块与隔离测试](../host/capture/README.md)
