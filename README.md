# 直播守候（Stream Keeper）

面向普通用户的多平台直播自动录制桌面工具。粘贴直播间链接后，程序会定期检查开播状态，开播后使用 FFmpeg 自动保存为 MP4，并记录录制历史。

## 功能

- 支持抖音、小红书、哔哩哔哩和通用 HTTP/RTMP 直播流
- 多直播间循环监测，开播后自动录制
- 网络短暂中断后自动重连，电脑唤醒后自动恢复检测
- 唤醒检测会区分真实系统休眠与普通网络耗时，不会因检测较慢而打断录制
- 45 秒无首个视频数据或录制中连续 90 秒无数据时，自动刷新直播地址重连
- 抖音优先使用稳定的 FLV 直播流，并保留 HLS 备用地址
- 抖音录制会严格解码校验画面；遇到 404、坏帧或异常断流时立即结束当前段并刷新直播地址，避免继续生成只有声音的黑屏视频
- 自动校正抖音音视频时间戳回跳，短暂断流安全重连；同场直播的重连片段会在停播后自动合并并重新按设定时长分段
- 自动识别抖音会员加密直播，停止无效录制并明确提示，避免生成黑屏或花屏文件
- 监测期间阻止系统自动休眠，同时允许屏幕正常熄灭
- 关闭主窗口后缩到菜单栏继续运行
- 自带 FFmpeg，无需用户单独安装
- 自定义保存目录、画质、检查间隔和分段时长
- 文件按“主播名_年月日_今日序号.mp4”命名
- 录制状态、持续时间、错误信息和历史记录
- 只有实际生成视频文件后才显示“录制中”，历史记录会区分“未生成文件”
- 直播流末尾中断但视频已落盘时，历史记录显示“已保存”及实际段数
- 可随时停止某一路录制，退出程序时安全结束 FFmpeg
- Cookie 只保存在本机应用数据目录，不写入源码或安装包
- macOS `.dmg` 与 Windows 安装程序构建流程

## 本地运行

需要 Python 3.10 或以上版本，推荐使用 `uv`：

```bash
uv sync --extra dev
uv run python run.py
```

应用数据位置：

- macOS：`~/Library/Application Support/StreamKeeper/`
- Windows：`%APPDATA%\StreamKeeper\`

## 打包

```bash
uv sync --extra dev
uv run python scripts/build.py
```

macOS 会生成 `.app`，并在系统提供 `hdiutil` 时生成 `.dmg`。Windows 会生成可运行目录和 ZIP；安装版由 GitHub Actions 使用 Inno Setup 生成。

## 安全说明

不要把自己的 Cookie 提交到 Git。录制仅用于你有权保存的直播内容。平台页面或接口变化可能导致解析暂时失效。

## 开源来源

直播地址解析代码基于 [ihmily/DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder)，原项目使用 MIT License。原许可证保存在 `third_party/upstream/LICENSE`。
