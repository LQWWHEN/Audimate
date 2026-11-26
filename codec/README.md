# PCM 分块的 FLAC 编解码

网页 Worker 和 Python 音频线程使用同一份 `server/web/vendor/flac-1.5.0.wasm`。每个 HTTPS 块独立编码，采样率及位深跟随主持设置，压缩级别为 1。每块最多包含半秒双声道采样。服务端验证完整音频块后才写入房间，听众可从任一缓存块开始解码。宿主传入和接收交错的小端整数 PCM，支持 16、24、32 位。

日常运行使用仓库中的模块。重建时将 [Xiph 官方 libFLAC 1.5.0 源码包](https://downloads.xiph.org/releases/flac/flac-1.5.0.tar.xz) 解压至构建目录，压缩包 SHA-256 为 `f2c1c76592a82ffff8413ba3c4a1299b6c7ab06c734dee03fd88630485c2b920`。在仓库根目录用 Zig 0.13.0 运行：

```sh
python codec/build.py zig build/flac-1.5.0
```

运行 `python -m unittest server.test_flac server.test_app` 验证逐字节还原、非法包拒绝和跨主持轮次切换。运行 `node server/test_browser.cjs --flac` 验证网页实际播放。配置测试 SFU 后，`python -m server.test_flac_bridge` 在独立随机房间验证一个无损听众和两个 Opus 听众共用一个主持流。
