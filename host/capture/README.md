# Windows 主持端采音

按桌面播放器的 PID 捕获该进程及其子进程。每个会话可选择采样率和 PCM 位深，每次读取返回 10 ms 左右交错的小端有符号整数 PCM。选择浏览器 PID 的隔离粒度也是整个进程树，网页标签页分享应走浏览器采集。

## 构建和录音

Windows 11 已在 build 26300 通过隔离测试。普通 Windows 10 的兼容目标为已更新的 2004 至 22H2（build 19041 至 19045），其中 22H2 实机验收待完成。微软的进程回环样例列出的最低构建号为 20348；OBS 在 19041 起启用了同一进程过滤接口。Audimate 在打开会话时实际激活目标进程树，失败时返回 HRESULT，只分享所选进程树的音频。[微软样例](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/) · [OBS 应用采音指南](https://obsproject.com/kb/application-audio-capture-guide)

从源码构建可选用 Windows 10 SDK 10.0.19041 或更新版本。旧 SDK 缺少进程回环参数头文件时，模块在内部声明对应的二进制布局。使用现成 x64 DLL 录音只需 64 位 Python 3 及标准库。以下为 Visual Studio 2022 的构建命令，需要“使用 C++ 的桌面开发”工作负载及 CMake 3.21+。在仓库根目录运行。

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
$dll = (Resolve-Path .\build\Release\audimate_capture.dll).Path
Get-Process | Where-Object MainWindowTitle | Select-Object Id, ProcessName, MainWindowTitle
```

将示例中的 1234 替换为播放器 PID。录音时每秒显示左右声道峰值、队列丢块数和 WASAPI 报告的不连续次数。输出文件须使用新文件名。

```powershell
python host/capture/record.py --dll $dll --pid 1234 --seconds 10 --output sample.wav
```

## 选择采集音质

| `--quality` | 采样率 | 位深 | 每声道每块采样数 | 每块 PCM 字节数 |
| --- | ---: | ---: | ---: | ---: |
| `standard`（默认） | 48 kHz | 16-bit | 480 | 1920 |
| `cd` | 44.1 kHz | 16-bit | 441 | 1764 |
| `studio` | 96 kHz | 24-bit | 960 | 5760 |

```powershell
python host/capture/record.py --dll $dll --pid 1234 --quality studio --output studio.wav
python host/capture/record.py --dll $dll --pid 1234 --sample-rate 88200 --bit-depth 32 --output custom.wav
```

`--sample-rate` 和 `--bit-depth` 可分别覆盖预设。采样率接受 8000 至 192000 Hz 范围内的 100 Hz 整数倍，确保每个 10 ms 块都有整数个采样帧。位深支持 16、24、32 位，其中 24 位每个采样紧凑存储为 3 字节。WASAPI 成功启动即按请求格式交付数据，若系统拒绝该格式则返回错误。切换音质时关闭原会话，使用新配置重新打开。

播放器无声时检查 PID、播放状态和输出设备。若声音由进程树外的服务产生，或内容受到保护，所选进程树可能只返回静音。播放器退出会终止本次会话，重启播放器后需选择新的 PID。

## 接入主持端

Python 主持界面可从 `QUALITY_PRESETS` 枚举选项，传入 `Capture(dll, pid, QUALITY_PRESETS["studio"])`，也可传入自定义的 `AudioFormat(44100, 24)`。消费线程调用 `session.read(timeout_ms)`，超时返回 `None`，终止性错误抛出带 HRESULT 的 `OSError`。用 `bytes(block.pcm)` 取得原始字节，`block.samples()` 读取左右交错的整数样本，`block.audio_format` 提供解释这块数据所需的采样率和位深。后续发布器可在其入口按编码器要求转换格式。[Python 接口](record.py)

C 调用方可通过 `audimate_open_format` 传入 `AudimateFormat`，使用 `audimate_read_pcm` 将 PCM 和序号等元数据分别读入调用方的缓冲区。每块字节数为 `(sample_rate / 100) × 2 × (bits_per_sample / 8)`，缓冲区过小时可扩大后重试读取同一队头（受正常队列溢出策略约束）。原有 `audimate_open` 和 `audimate_read` 继续使用 48 kHz、16-bit 的结构布局。[C 接口](capture.h)

采集线程最多缓存 20 块（200 ms），满时丢弃最旧的完整块，新块的 `sequence` 可用于计算遗漏数量。每个会话的序号从 0 开始。切源时调用 `stop()` 清空队列并唤醒读取者，等消费线程退出后 `close()`，再打开新 PID。发布方也应丢弃自己持有的旧会话数据。

## 隔离验收

在有默认音频输出的 Windows 登录会话执行以下命令。测试会打印系统构建号与 DLL 路径，并短暂播放合成测试音。Windows 10 验收请记录构建号、最后的 PASS 或具体错误，以及所用播放器版本。

```powershell
python host/capture/test_isolation.py --dll $dll
```

测试选择一个仅由子进程发声的父 PID，左声道播放 700 Hz，右声道播放 1200 Hz。独立进程持续播放 1800 Hz。测试检查目标声道、串音和干扰音幅度，暂停目标后测量残留峰值，然后切换到干扰源确认它确实发声，并检查进程退出错误。真实播放器验收可用同样方法录制 WAV，并记录系统构建号、播放器版本、所选 PID 和暂停时段。

同一测试还使用三个音质预设和自定义 88.2 kHz/32-bit 录制 WAV，回读并核对声道数、采样率、位深、实际 PCM 长度及左右声道频率。
