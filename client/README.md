# Audimate Windows 客户端

在 Windows 上从仓库根目录运行。需要 x64 Python 3（含 Tkinter）和位数一致的采音 DLL。本机默认构建位于 `build/Release/audimate_capture.dll`；其他构建目录可通过 `--dll` 指定。

```powershell
python -m client
python -m client --dll build/msvc-x64/Release/audimate_capture.dll
```

1. 在“共享进程”里按程序名、窗口标题或 PID 搜索。列表优先展示有窗口的应用，也可选择后台播放器。播放器刚启动时点“刷新进程”。
2. 选择标准、CD、录音室预设，或选“自定义”填写采样率和位深。默认音质为 48 kHz/16-bit 双声道。
3. 点击“开始主持”，音频流成功启动后可查看来源、格式和左右电平。点击“下台”结束采音并返回选择界面，再次上台时可更换来源和音质。

以上步骤可独立验证本机采音。要让其他人收听，在主窗口选择进程和音质，点击“连接在线房间”，在房间窗口注册或登录，然后点击主持。空席时立即共享，有主持时发送申请，由现任主持接受或拒绝。在房间窗口可下台、取消申请、收听其他人的音源或退出登录。

服务地址默认为 `http://127.0.0.1:17881`。可在登录页直接填写测试机的 HTTPS 地址，或用环境变量 `AUDIMATE_SERVER` 指定默认值。桌面端和网页端共用账号。[房间服务与网页使用说明](../server/README.md)

## 测试

以下命令使用隐藏的 Tk 窗口操作真实控件，并短暂播放合成测试音。实机用例使用默认构建目录的 DLL。

```powershell
python -m unittest client.test_client client.test_online -v
```

测试覆盖搜索、刷新、PID 复用、音质校验、取消启动、失败重试、进程切源和音质传递。在线用例启动临时服务，操作桌面窗口注册和登录、捕获真实播放进程，再由另一个会话核对收到的左右声道样本。Windows 10 的隔离验收仍需在对应系统上运行。[采音模块与隔离测试](../host/capture/README.md)
