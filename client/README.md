# Audimate 本地客户端

在 Windows 上从仓库根目录运行。需要 x64 Python 3（含 Tkinter）和位数一致的采音 DLL。本机默认构建位于 `build/Release/audimate_capture.dll`；其他构建目录可通过 `--dll` 指定。

```powershell
python -m client
python -m client --dll build/msvc-x64/Release/audimate_capture.dll
```

1. 在“共享进程”里按程序名、窗口标题或 PID 搜索。列表优先展示有窗口的应用，也可选择后台播放器。播放器刚启动时点“刷新进程”。
2. 选择标准、CD、录音室预设，或选“自定义”填写采样率和位深。默认音质为 48 kHz/16-bit 双声道。
3. 点击“开始主持”，音频流成功启动后可查看来源、格式和左右电平。点击“下台”结束采音并返回选择界面，再次上台时可更换来源和音质。

当前为单用户本地采音原型。进程和音质封装在不可变的 `ShareSelection` 中。后续房间层可在上台获准后启动该选择对应的采音任务；主持申请、接受与拒绝、管理员授权及听众音轨播放属于房间阶段。

## 测试

以下命令使用隐藏的 Tk 窗口操作真实控件，并短暂播放合成测试音。实机用例使用默认构建目录的 DLL。

```powershell
python -m unittest client.test_client -v
```

测试覆盖搜索、刷新、PID 复用、音质参数校验、取消启动、启动失败后重试、真实进程切源和音质传递。Windows 10 的隔离验收仍需在对应系统上运行。[采音模块与隔离测试](../host/capture/README.md)
